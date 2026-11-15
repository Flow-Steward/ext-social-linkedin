"""Conservative local content policy and lossless LinkedIn little encoding."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from linkedin.errors import LinkedInError

API_VERSION = "202610"
JSON_LIMIT = 64 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
PIXEL_LIMIT = 36_152_320
LIMITS = {
    "text_utf16_units": 3000,
    "title_utf16_units": 200,
    "description_utf16_units": 256,
    "alt_text_utf16_units": 120,
    "source_url_utf8_bytes": 2048,
    "provider_json_bytes": JSON_LIMIT,
    "image_bytes": IMAGE_LIMIT,
    "image_pixels_exclusive": PIXEL_LIMIT,
}
IMAGE_URN = re.compile(r"urn:li:image:[A-Za-z0-9_-]{1,256}\Z")
RESERVED = frozenset(r"|{}@[]()<>#\*_~")
FIELDS = {
    "text": {"kind", "text"},
    "image": {"kind", "text", "image_urn", "alt_text"},
    "article": {"kind", "text", "source_url", "title", "description", "thumbnail_image_urn"},
}


def safe_text(value: object, *, maximum: int, required: bool = True) -> bool:
    """Measure UTF-16 units without accepting surrogate/control code points."""
    if not isinstance(value, str) or (required and not value.strip()):
        return False
    if any(unicodedata.category(c) in {"Cc", "Cs"} and c not in "\r\n\t" for c in value):
        return False
    return len(value.encode("utf-16-le")) // 2 <= maximum


def image_urn(value: object) -> bool:
    return isinstance(value, str) and IMAGE_URN.fullmatch(value) is not None


def _article_url(value: object) -> bool:
    if not isinstance(value, str) or not value or not safe_text(value, maximum=2048):
        return False
    if len(value.encode("utf-8")) > 2048 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and (parsed.port is None or 1 <= parsed.port <= 65535)
        )
    except ValueError:
        return False


def validate_post(value: object) -> dict[str, Any]:
    """Validate content without network, connection or artifact access."""
    violations: list[dict[str, str]] = []

    def invalid(field: str) -> None:
        violations.append({"field": field, "code": "invalid_content"})

    if not isinstance(value, dict):
        invalid("post")
    else:
        kind = value.get("kind")
        allowed = FIELDS.get(kind) if isinstance(kind, str) else None
        if allowed is None:
            invalid("post.kind")
        else:
            if set(value) - allowed:
                invalid("post.fields")
            if not safe_text(value.get("text"), maximum=3000):
                invalid("post.text")
            if kind == "image":
                if not image_urn(value.get("image_urn")):
                    invalid("post.image_urn")
                if "alt_text" in value and not safe_text(
                    value["alt_text"], maximum=120, required=False
                ):
                    invalid("post.alt_text")
            if kind == "article":
                if not _article_url(value.get("source_url")):
                    invalid("post.source_url")
                if not safe_text(value.get("title"), maximum=200):
                    invalid("post.title")
                if "description" in value and not safe_text(
                    value["description"], maximum=256, required=False
                ):
                    invalid("post.description")
                if "thumbnail_image_urn" in value:
                    thumbnail = value["thumbnail_image_urn"]
                    if thumbnail != "" and not image_urn(thumbnail):
                        invalid("post.thumbnail_image_urn")
    return {"valid": not violations, "violations": violations, "limits": dict(LIMITS)}


def provider_post(post: dict[str, Any], author: str) -> dict[str, Any]:
    """Construct the single current organic Posts API request shape."""
    if not validate_post(post)["valid"]:
        raise LinkedInError("invalid_payload", "The prepared post violates the content policy")
    result: dict[str, Any] = {
        "author": author,
        "commentary": "".join("\\" + c if c in RESERVED else c for c in post["text"]),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
    }
    if post["kind"] == "image":
        media = {"id": post["image_urn"]}
        if post.get("alt_text"):
            media["altText"] = post["alt_text"]
        result["content"] = {"media": media}
    elif post["kind"] == "article":
        article = {"source": post["source_url"], "title": post["title"]}
        for source, target in [
            ("description", "description"),
            ("thumbnail_image_urn", "thumbnail"),
        ]:
            if post.get(source):
                article[target] = post[source]
        result["content"] = {"article": article}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > JSON_LIMIT:
        raise LinkedInError(
            "invalid_payload", "The encoded provider request exceeds the local limit"
        )
    return result
