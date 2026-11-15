"""Bounded artifact-backed JPEG/PNG upload, without resizing or status polling."""

from __future__ import annotations

import struct
import zlib
from typing import Any
from urllib.parse import urlsplit

from flowsteward_extension_sdk import (
    ArtifactAccessError,
    PinnedPeerError,
    find_artifact_descriptor,
    stream_artifact_bytes,
)

from linkedin.errors import LinkedInError
from linkedin.transport import LinkedInClient
from linkedin.validation import IMAGE_LIMIT, PIXEL_LIMIT, image_urn

BINDING = "linkedin_image"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _invalid() -> LinkedInError:
    return LinkedInError(
        "invalid_image", "Use a structurally valid JPEG/PNG within the image limits"
    )


def _png(data: bytes) -> tuple[int, int]:
    pos, dimensions, saw_data, color = 8, None, False, -1
    palette = False
    while pos + 12 <= len(data):
        size = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        end = pos + 12 + size
        if end > len(data):
            raise _invalid()
        body = data[pos + 8 : end - 4]
        checksum = int.from_bytes(data[end - 4 : end], "big")
        if zlib.crc32(kind + body) != checksum:
            raise _invalid()
        if dimensions is None:
            if kind != b"IHDR" or size != 13:
                raise _invalid()
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body
            )
            depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
            if (
                depth not in depths.get(color, set())
                or compression
                or filtering
                or interlace not in {0, 1}
            ):
                raise _invalid()
            dimensions = (width, height)
        elif kind == b"IHDR":
            raise _invalid()
        if kind == b"PLTE":
            palette = 0 < size <= 768 and size % 3 == 0
            if not palette or saw_data:
                raise _invalid()
        elif kind == b"IDAT":
            if color == 3 and not palette:
                raise _invalid()
            saw_data = saw_data or size > 0
        elif kind == b"IEND":
            if size or not saw_data or end != len(data):
                raise _invalid()
            return dimensions
        elif kind != b"IHDR" and kind[0] < 97:
            raise _invalid()
        pos = end
    raise _invalid()


def _jpeg(data: bytes) -> tuple[int, int]:
    pos, dimensions, scan = 2, None, False
    while pos < len(data):
        if scan:
            pos = data.find(b"\xff", pos)
            if pos < 0:
                raise _invalid()
        if data[pos] != 255:
            raise _invalid()
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            raise _invalid()
        marker = data[pos]
        pos += 1
        if scan and (marker == 0 or 208 <= marker <= 215):
            continue
        if marker == 217:
            if dimensions is None or not scan or pos != len(data):
                raise _invalid()
            return dimensions
        if marker in {0, 216} or pos + 2 > len(data):
            raise _invalid()
        size = int.from_bytes(data[pos : pos + 2], "big")
        if size < 2 or pos + size > len(data):
            raise _invalid()
        body = data[pos + 2 : pos + size]
        if marker in {192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207}:
            if len(body) < 6 or len(body) != 6 + 3 * body[5]:
                raise _invalid()
            current = (int.from_bytes(body[3:5], "big"), int.from_bytes(body[1:3], "big"))
            if dimensions is not None and dimensions != current:
                raise _invalid()
            dimensions = current
        if marker == 218:
            if dimensions is None or len(body) < 6:
                raise _invalid()
            scan = True
        pos += size
    raise _invalid()


def inspect_image(data: bytes, mime: str) -> tuple[int, int]:
    """Check structural format, MIME and dimensions, not rendered pixel validity."""
    if not data or len(data) > IMAGE_LIMIT:
        raise _invalid()
    if mime == "image/png" and data.startswith(PNG_SIGNATURE):
        dimensions = _png(data)
    elif mime == "image/jpeg" and data.startswith(b"\xff\xd8"):
        dimensions = _jpeg(data)
    else:
        raise _invalid()
    width, height = dimensions
    if width <= 0 or height <= 0 or width * height >= PIXEL_LIMIT:
        raise _invalid()
    return dimensions


def read_image(payload: dict[str, Any], handle: str) -> tuple[bytes, str]:
    """Read only the selected host-granted artifact, with exact byte accounting."""
    try:
        descriptor = find_artifact_descriptor(
            payload, artifact_id=handle, binding_key=BINDING, role="input"
        )
        access, scope = descriptor.get("access", {}), descriptor.get("scope", {})
        context = payload.get("runtime_context", {})
        expected = handle.removeprefix("artifact:")
        size, mime = descriptor.get("size_bytes"), descriptor.get("content_type")
        cap = access.get("max_size_bytes") if isinstance(access, dict) else None
        if (
            descriptor.get("role") != "input"
            or descriptor.get("binding_key") != BINDING
            or descriptor.get("artifact_id") != expected
            or descriptor.get("artifact_handle") not in {handle, "artifact:" + expected}
            or not isinstance(scope, dict)
            or not isinstance(context, dict)
            or any(
                not context.get(k) or scope.get(k) != context[k]
                for k in ("account_id", "project_id")
            )
            or not isinstance(access, dict)
            or access.get("transport") != "presigned_url"
            or access.get("mode") not in {"read", "read_write"}
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 < size <= IMAGE_LIMIT
            or not isinstance(cap, int)
            or isinstance(cap, bool)
            or cap < size
            or mime not in {"image/png", "image/jpeg"}
        ):
            raise ArtifactAccessError("invalid image grant")
        data = bytearray()
        for chunk in stream_artifact_bytes(
            payload, artifact_id=handle, binding_key=BINDING, chunk_size=65536, timeout_seconds=30
        ):
            if not isinstance(chunk, bytes) or len(data) + len(chunk) > size:
                raise ArtifactAccessError("invalid image stream")
            data.extend(chunk)
        if len(data) != size:
            raise ArtifactAccessError("incomplete image stream")
        binary = bytes(data)
        inspect_image(binary, mime)
        return binary, mime
    except LinkedInError:
        raise
    except (ArtifactAccessError, PinnedPeerError, OSError, TypeError, ValueError) as exc:
        raise LinkedInError(
            "artifact_input_unavailable", "The authorized image artifact is unavailable"
        ) from exc


def _upload_url(value: object) -> str:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 8192
        and value.isascii()
        and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
    ):
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme == "https"
                and parsed.hostname == "www.linkedin.com"
                and parsed.port in {None, 443}
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and parsed.path.startswith("/dms-uploads/")
                and not any(s in {".", ".."} for s in parsed.path.split("/"))
                and "\\" not in parsed.path
                and "%" not in parsed.path
            ):
                return value
        except ValueError:
            pass
    raise LinkedInError(
        "timeout_unknown", "LinkedIn returned an unsupported upload destination", no_effect=False
    )


def upload_image(
    payload: dict[str, Any], handle: str, author: str, client: LinkedInClient
) -> dict[str, Any]:
    """Initialize and transfer one image; do not query processing status."""
    data, mime = read_image(payload, handle)
    response = client.initialize_image(author)
    value = response.get("value")
    if not isinstance(value, dict) or not image_urn(value.get("image")):
        raise LinkedInError(
            "timeout_unknown", "LinkedIn returned an incomplete upload receipt", no_effect=False
        )
    destination = _upload_url(value.get("uploadUrl"))
    client.transfer_image(destination, data, mime)
    return {"image_urn": value["image"], "author_urn": author, "status": "uploaded"}
