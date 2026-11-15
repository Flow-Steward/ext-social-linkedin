import copy
import http.client
import json
from urllib.error import HTTPError, URLError

import pytest

from conftest import Response, json_response
from linkedin.runtime import dispatch


@pytest.mark.parametrize(
    "problem", [http.client.IncompleteRead(b"partial"), http.client.BadStatusLine("fixture-bearer")]
)
def test_interrupted_http_response_is_canonical_unknown(payload, network, problem):
    network[1].append(problem)
    result = dispatch(payload)
    assert result["ok"] is False
    assert result["external_effect_status"] == "timeout_unknown"
    assert "fixture-bearer" not in json.dumps(result)


def test_connection_repr_does_not_include_token(payload):
    from linkedin.connection import resolve_connection

    connection = resolve_connection(payload, payload["action"]["input"])
    assert "fixture-bearer" not in repr(connection)


@pytest.mark.parametrize("page", [False, True])
@pytest.mark.parametrize("urn", ["urn:li:share:123", "urn:li:ugcPost:456"])
@pytest.mark.parametrize("kind", ["text", "image", "article"])
def test_publish_both_authors_and_receipt(payload, network, page, urn, kind):
    calls, replies = network
    post = {"kind": kind, "text": "Hello"}
    if kind == "image":
        post["image_urn"] = "urn:li:image:asset1"
    elif kind == "article":
        post.update(source_url="https://example.org/release", title="Release")
    payload["action"]["input"]["post"] = post
    if page:
        conn = payload["action"]["target"]["connection"]
        conn.update(
            connection_type="linkedin_page", config={"organization_urn": "urn:li:organization:123"}
        )
    replies.append(Response(headers={"x-restli-id": urn}))
    result = dispatch(payload)
    assert result["ok"] is True
    assert result["result"]["status"] == "accepted"
    assert result["result"]["post_url"] == "https://www.linkedin.com/feed/update/" + urn + "/"
    expected = "urn:li:organization:123" if page else "urn:li:person:member123"
    assert result["result"]["author_urn"] == expected
    assert result["external_effect_status"] == "succeeded"
    assert len(calls) == 1
    assert calls[0].full_url == "https://api.linkedin.com/rest/posts"
    assert calls[0].get_header("Linkedin-version") == "202610"
    assert calls[0].get_header("X-restli-protocol-version") == "2.0.0"
    assert json.loads(calls[0].data)["author"] == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("contract_version"),
        lambda p: p["runtime_context"].pop("step_id"),
        lambda p: p["action"]["target"]["connection"].update(project_id="other"),
        lambda p: p["action"]["target"]["connection"].update(account_id="other"),
        lambda p: p["action"]["input"].update(connection_ref="other"),
        lambda p: p["action"]["target"]["connection"].update(config={"oauth": {}}),
        lambda p: p["action"]["input"]["post"].update(author="urn:li:person:evil"),
        lambda p: p["action"]["target"]["connection"].update(secrets={}),
        lambda p: p["runtime_context"].update(test_mode="false"),
        lambda p: p["action"]["input"].update(access_token="input-token"),
    ],
)
def test_rejects_before_network(payload, network, mutate):
    mutate(payload)
    result = dispatch(payload)
    assert result["ok"] is False
    assert result["definitely_no_external_effect"] is True
    assert network[0] == []


@pytest.mark.parametrize("operation", ["publish_post", "upload_image"])
def test_test_mode_has_no_receipt_or_network(payload, network, operation):
    payload["action"]["action_id"] = operation
    payload["runtime_context"]["test_mode"] = True
    if operation == "upload_image":
        payload["action"]["input"] = {"connection_ref": "connection", "artifact_handle": "artifact"}
    result = dispatch(payload)
    assert result["ok"] is True
    assert result["result"]["status"] == "suppressed"
    assert result["external_effect_status"] == "suppressed"
    assert "post_urn" not in result["result"]
    assert "image_urn" not in result["result"]
    assert network[0] == []


@pytest.mark.parametrize(
    "status,code,unknown",
    [
        (401, "authorization_required", False),
        (403, "insufficient_access", False),
        (429, "rate_limited", False),
        (500, "timeout_unknown", True),
        (302, "timeout_unknown", True),
    ],
)
def test_http_failures_are_safe_and_do_not_retry(payload, network, status, code, unknown):
    calls, replies = network
    replies.append(
        HTTPError(
            "https://api.linkedin.com/rest/posts",
            status,
            "private body",
            {"Retry-After": "30"},
            None,
        )
    )
    result = dispatch(payload)
    assert result["ok"] is False
    assert result["error_code"] == code
    assert "private body" not in json.dumps(result)
    assert len(calls) == 1
    if unknown:
        assert result["external_effect_status"] == "timeout_unknown"
        assert "definitely_no_external_effect" not in result
    else:
        assert result["definitely_no_external_effect"] is True
    if status == 429:
        assert result["result"]["retry_after_seconds"] == 30


@pytest.mark.parametrize(
    "reply",
    [
        TimeoutError("fixture-bearer"),
        URLError("fixture-bearer"),
        Response(),
        Response(headers={"x-restli-id": "not-an-id"}),
        Response(200, headers={"x-restli-id": "urn:li:share:123"}),
    ],
)
def test_uncertain_creation_is_not_retryable(payload, network, reply):
    network[1].append(reply)
    result = dispatch(payload)
    assert result["ok"] is False
    assert result["external_effect_status"] == "timeout_unknown"
    assert "definitely_no_external_effect" not in result
    assert "fixture-bearer" not in json.dumps(result)


def test_hidden_identity_uses_only_userinfo_not_unverified_input(payload, network):
    payload["action"].update(action_id="verify_member_identity", input={})
    payload["action"]["target"] = {"connection": {"secrets": {"access_token": "fixture-bearer"}}}
    network[1].append(json_response({"sub": "authenticated123", "name": "A"}))
    result = dispatch(payload)
    assert result["ok"] is True
    assert result["result"] == {
        "subject": "authenticated123",
        "verified_display_name": "A",
        "safe_metadata": {},
        "health": "ok",
    }
    assert network[0][0].full_url == "https://api.linkedin.com/v2/userinfo"


def test_page_test_is_explicitly_unverified(payload, network):
    payload["action"].update(action_id="test_connection", input={"connection_ref": "connection"})
    payload["action"]["target"]["connection"].update(
        connection_type="linkedin_page", config={"organization_urn": "urn:li:organization:123"}
    )
    result = dispatch(payload)
    assert result["result"]["authorization"] == "unverified"
    assert network[0] == []


def test_validate_is_connection_free(payload, network):
    payload["action"] = {
        "action_id": "validate_post",
        "input": {"post": {"kind": "text", "text": ""}},
    }
    result = dispatch(payload)
    assert result["ok"] is True
    assert result["result"]["valid"] is False
    assert network[0] == []


def test_userinfo_mismatch_does_not_rebind_member(payload, network):
    payload["action"].update(action_id="test_connection", input={"connection_ref": "connection"})
    network[1].append(json_response({"sub": "other-member"}))
    assert dispatch(payload)["error_code"] == "invalid_connection"


def test_validation_failure_never_mutates_original(payload, network):
    original = copy.deepcopy(payload)
    payload["action"]["input"]["post"]["text"] = "\ud800"
    result = dispatch(payload)
    assert result["ok"] is False
    assert original["action"]["input"]["post"]["text"] == "Hello"
