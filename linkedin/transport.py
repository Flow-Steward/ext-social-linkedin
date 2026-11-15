"""Fixed-endpoint pinned HTTPS with no retries or raw provider error exposure."""

from __future__ import annotations

import json
import re
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from flowsteward_extension_sdk import PinnedPeerError, open_pinned_url
from flowsteward_extension_sdk.http import parse_retry_after

from linkedin.errors import LinkedInError
from linkedin.validation import API_VERSION, JSON_LIMIT

API_ORIGIN = "https://api.linkedin.com"
TIMEOUT_SECONDS = 15.0
POST_URN = re.compile(r"urn:li:(share|ugcPost):[0-9]{1,40}\Z")
DEFINITIVE_REJECTIONS = {400, 401, 403, 404, 405, 409, 413, 415, 422, 429}


class LinkedInClient:
    """One invocation's access token; methods never select another author."""

    def __init__(self, token: str) -> None:
        self._token = token
        self.partial_upload = False

    def _request(
        self,
        method: str,
        url: str,
        *,
        data: bytes | None = None,
        content_type: str = "application/json",
        expected: tuple[int, ...] = (200,),
    ) -> tuple[bytes, dict[str, str]]:
        write = method != "GET"
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        if url.startswith(API_ORIGIN + "/rest/"):
            headers.update({"Linkedin-Version": API_VERSION, "X-Restli-Protocol-Version": "2.0.0"})
        if data is not None:
            headers["Content-Type"] = content_type
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with open_pinned_url(
                request, timeout_seconds=TIMEOUT_SECONDS, purpose="LinkedIn API"
            ) as response:
                if response.status not in expected:
                    raise self._status_error(response.status, response.headers, write=write)
                body = bytearray()
                while True:
                    chunk = response.read(4096)
                    if not isinstance(chunk, bytes):
                        raise LinkedInError(
                            "provider_unavailable", "Invalid LinkedIn response", no_effect=not write
                        )
                    if not chunk:
                        break
                    body.extend(chunk)
                    if len(body) > JSON_LIMIT:
                        raise LinkedInError(
                            "response_too_large",
                            "LinkedIn response exceeds its limit",
                            no_effect=not write,
                        )
                normalized: dict[str, str] = {}
                for key, value in response.headers.items():
                    key = key.lower()
                    normalized[key] = "" if key in normalized else value
                return bytes(body), normalized
        except HTTPError as exc:
            error = self._status_error(exc.code, exc.headers, write=write)
            exc.close()
            raise error from None
        except (URLError, TimeoutError, OSError, HTTPException, PinnedPeerError, ValueError) as exc:
            raise LinkedInError(
                "timeout_unknown" if write else "network_failure",
                "LinkedIn write outcome is unknown"
                if write
                else "LinkedIn identity is unavailable",
                no_effect=not write,
            ) from exc

    def _status_error(self, status: int, headers: Any, *, write: bool) -> LinkedInError:
        no_effect = not write or (status in DEFINITIVE_REJECTIONS and not self.partial_upload)
        code = {401: "authorization_required", 403: "insufficient_access", 429: "rate_limited"}.get(
            status, "invalid_payload" if status in DEFINITIVE_REJECTIONS else "provider_unavailable"
        )
        if write and not no_effect:
            code = "timeout_unknown"
        retry = (
            parse_retry_after(headers.get("Retry-After"))
            if headers is not None and status == 429
            else None
        )
        return LinkedInError(
            code,
            "LinkedIn rejected the request; check connection access and content"
            if no_effect
            else "LinkedIn write outcome is unknown",
            no_effect=no_effect,
            retry_after=retry,
        )

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = (
            json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if body is not None
            else None
        )
        if data is not None and len(data) > JSON_LIMIT:
            raise LinkedInError("invalid_payload", "LinkedIn JSON request exceeds the local limit")
        raw, _ = self._request(method, API_ORIGIN + path, data=data)
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (ValueError, UnicodeDecodeError) as exc:
            raise LinkedInError(
                "provider_unavailable" if method == "GET" else "timeout_unknown",
                "LinkedIn returned malformed metadata",
                no_effect=method == "GET",
            ) from exc

    def userinfo(self) -> dict[str, Any]:
        return self._json("GET", "/v2/userinfo")

    def initialize_image(self, author: str) -> dict[str, Any]:
        result = self._json(
            "POST",
            "/rest/images?action=initializeUpload",
            {"initializeUploadRequest": {"owner": author}},
        )
        self.partial_upload = True
        return result

    def transfer_image(self, url: str, data: bytes, mime: str) -> None:
        self._request("PUT", url, data=data, content_type=mime, expected=(200, 201))

    def create_post(self, body: dict[str, Any]) -> str:
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > JSON_LIMIT:
            raise LinkedInError("invalid_payload", "LinkedIn JSON request exceeds the local limit")
        _, headers = self._request(
            "POST", API_ORIGIN + "/rest/posts", data=encoded, expected=(201,)
        )
        urn = headers.get("x-restli-id", "")
        if POST_URN.fullmatch(urn) is None:
            raise LinkedInError(
                "timeout_unknown",
                "LinkedIn accepted a request without a usable post receipt",
                no_effect=False,
            )
        return urn
