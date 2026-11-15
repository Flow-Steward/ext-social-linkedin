"""Canonical bounded errors and external-effect metadata."""

from __future__ import annotations

from typing import Any


class LinkedInError(Exception):
    """A safe public error with an explicit external-write outcome."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        no_effect: bool = True,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.no_effect = no_effect
        self.retry_after = retry_after


def failure(error: LinkedInError) -> dict[str, Any]:
    """Return the subprocess failure contract without raw provider diagnostics."""
    result: dict[str, Any] = {}
    if error.retry_after is not None:
        result["retry_after_seconds"] = error.retry_after
    response: dict[str, Any] = {
        "ok": False,
        "result": result,
        "error_code": error.code,
        "error": str(error),
        "errors": [{"code": error.code, "message": str(error)}],
    }
    if error.no_effect:
        response["definitely_no_external_effect"] = True
    else:
        response["external_effect_status"] = "timeout_unknown"
    return response


def success(result: dict[str, Any], *, effect: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": True, "result": result}
    if effect:
        response["external_effect_status"] = "succeeded"
        object_id = result.get("post_urn") or result.get("image_urn")
        response["external_effect"] = {"status": "succeeded", "external_object_id": object_id}
    return response
