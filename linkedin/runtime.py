"""Dispatch only the approved publication and connection operations."""

from __future__ import annotations

from typing import Any

from flowsteward_extension_sdk import runtime_context_from_payload

from linkedin.connection import access_token, bind_page, resolve_connection, subject
from linkedin.errors import LinkedInError, failure, success
from linkedin.transport import LinkedInClient
from linkedin.validation import API_VERSION, provider_post, safe_text, validate_post

WRITES = frozenset({"publish_post", "upload_image"})
INPUTS = {
    "validate_post": {"post"},
    "publish_post": {"connection_ref", "post"},
    "upload_image": {"connection_ref", "artifact_handle"},
    "test_connection": {"connection_ref"},
    "verify_member_identity": set(),
    "bind_page": {"connection_ref", "organization_urn"},
}


def dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the host envelope before reading secrets, grants or the network."""
    try:
        runtime_context_from_payload(payload)
    except ValueError:
        return failure(
            LinkedInError("invalid_host_envelope", "The host invocation envelope is invalid")
        )
    operation = ""
    try:
        if payload.get("mode") != "action" or not isinstance(payload.get("action"), dict):
            raise LinkedInError("invalid_payload", "An action invocation is required")
        action = payload["action"]
        operation = action.get("action_id")
        if not isinstance(operation, str) or operation not in INPUTS:
            raise LinkedInError("invalid_payload", "Unknown LinkedIn operation")
        inputs = action.get("input", {})
        if not isinstance(inputs, dict) or set(inputs) != INPUTS[operation]:
            raise LinkedInError("invalid_payload", "Operation input fields are invalid")
        test_mode = payload["runtime_context"].get("test_mode", False)
        if not isinstance(test_mode, bool):
            raise LinkedInError("invalid_payload", "Test mode must be boolean")
        if operation == "validate_post":
            return success(validate_post(inputs["post"]))
        if operation == "bind_page":
            return bind_page(payload, inputs, test_mode=test_mode)
        if operation == "verify_member_identity":
            target = action.get("target", {})
            connection = target.get("connection", {}) if isinstance(target, dict) else {}
            if not isinstance(connection, dict):
                raise LinkedInError("invalid_connection", "A host OAuth target is required")
            data = LinkedInClient(access_token(connection)).userinfo()
            identity = subject(data.get("sub"))
            display = data.get("name")
            return success(
                {
                    "subject": identity,
                    "verified_display_name": display
                    if safe_text(display, maximum=512)
                    else identity,
                    "safe_metadata": {},
                    "health": "ok",
                }
            )
        connection = resolve_connection(payload, inputs)
        if operation == "publish_post":
            body = provider_post(inputs["post"], connection.author)
        if operation == "upload_image" and (
            not isinstance(inputs.get("artifact_handle"), str)
            or not 1 <= len(inputs["artifact_handle"]) <= 512
        ):
            raise LinkedInError(
                "invalid_payload", "An authorized image artifact handle is required"
            )
        if operation in WRITES and test_mode:
            return {
                "ok": True,
                "result": {"status": "suppressed"},
                "external_effect_status": "suppressed",
            }
        client = LinkedInClient(connection.token())
        if operation == "publish_post":
            urn = client.create_post(body)
            return success(
                {
                    "post_urn": urn,
                    "post_url": f"https://www.linkedin.com/feed/update/{urn}/",
                    "author_urn": connection.author,
                    "status": "accepted",
                    "api_version": API_VERSION,
                },
                effect=True,
            )
        if operation == "test_connection":
            authorization = "unverified"
            if connection.kind == "linkedin_member":
                current = subject(client.userinfo().get("sub"))
                if connection.author != "urn:li:person:" + current:
                    raise LinkedInError(
                        "invalid_connection", "Reconnect the original LinkedIn member"
                    )
                authorization = "identity_verified"
            return success(
                {
                    "author_urn": connection.author,
                    "connection_type": connection.kind,
                    "authorization": authorization,
                }
            )
        from linkedin.images import upload_image

        return success(
            upload_image(payload, inputs["artifact_handle"], connection.author, client), effect=True
        )
    except LinkedInError as exc:
        return failure(exc)
    except (TypeError, ValueError, KeyError, OverflowError):
        return failure(
            LinkedInError(
                "internal_error",
                "The LinkedIn operation could not complete",
                no_effect=operation not in WRITES,
            )
        )
