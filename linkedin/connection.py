"""Read author authority only from the host-resolved project connection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from linkedin.errors import LinkedInError

SUBJECT = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
ORGANIZATION = re.compile(r"urn:li:organization:[1-9][0-9]{0,30}\Z")


def access_token(connection: dict[str, Any]) -> str:
    secrets = connection.get("secrets")
    value = secrets.get("access_token") if isinstance(secrets, dict) else None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 8192
        or not value.isascii()
        or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
    ):
        raise LinkedInError("authorization_required", "Authorize the selected LinkedIn connection")
    return value


def subject(value: object) -> str:
    if not isinstance(value, str) or SUBJECT.fullmatch(value) is None:
        raise LinkedInError(
            "invalid_connection", "Authenticated LinkedIn member identity is missing"
        )
    return value


@dataclass(frozen=True)
class Connection:
    """Scoped connection identity; token is intentionally excluded from repr."""

    author: str
    kind: str
    target: dict[str, Any] = field(repr=False)

    def token(self) -> str:
        return access_token(self.target)


def resolve_connection(payload: dict[str, Any], inputs: dict[str, Any]) -> Connection:
    action = payload.get("action", {})
    target = action.get("target", {}) if isinstance(action, dict) else {}
    connection = target.get("connection") if isinstance(target, dict) else None
    context = payload.get("runtime_context", {})
    if not isinstance(connection, dict) or not isinstance(context, dict):
        raise LinkedInError("invalid_connection", "Select a project LinkedIn connection")
    for key in ("account_id", "project_id"):
        if (
            not isinstance(context.get(key), str)
            or not context[key]
            or connection.get(key) != context[key]
        ):
            raise LinkedInError("invalid_connection", "Connection is outside the invocation scope")
    if not inputs.get("connection_ref") or inputs["connection_ref"] != connection.get(
        "connection_id"
    ):
        raise LinkedInError("invalid_connection", "Input must reference the selected connection")
    config = connection.get("config")
    if not isinstance(config, dict):
        raise LinkedInError("invalid_connection", "LinkedIn connection configuration is missing")
    kind = connection.get("connection_type")
    if kind == "linkedin_member":
        oauth = config.get("oauth")
        identity = oauth.get("subject") if isinstance(oauth, dict) else None
        author = "urn:li:person:" + subject(identity)
    elif kind == "linkedin_page":
        page_author = config.get("organization_urn")
        if not isinstance(page_author, str) or ORGANIZATION.fullmatch(page_author) is None:
            raise LinkedInError(
                "invalid_connection", "Configure one valid LinkedIn organization URN"
            )
        author = page_author
    else:
        raise LinkedInError(
            "invalid_connection", "The selected connection is not a LinkedIn author"
        )
    access_token(connection)
    return Connection(author=author, kind=kind, target=connection)
