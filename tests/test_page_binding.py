import json

import pytest

from conftest import Response
from linkedin.runtime import dispatch


URN = "urn:li:organization:123"


@pytest.fixture
def page_binding(payload):
    payload["action"].update(
        action_id="bind_page",
        input={"connection_ref": "connection", "organization_urn": URN},
    )
    payload["action"]["target"]["connection"].update(
        connection_type="linkedin_page",
        config={"oauth": {"lifecycle_status": "connected"}},
    )
    return payload


def test_bind_page_supplies_a_scoped_same_value_mutation_and_author(page_binding, network):
    result = dispatch(page_binding)
    assert result["ok"], result
    assert result["host_mutations"] == [
        {
            "kind": "project_connection_config_compare_and_set",
            "payload": {
                "connection_id": "connection",
                "expect": [
                    {
                        "path": "organization_urn",
                        "state": "absent_or_equals",
                        "value": URN,
                    }
                ],
                "set": {"organization_urn": URN},
            },
        }
    ]
    assert not network[0]
    target = page_binding["action"]["target"]["connection"]
    target["config"].update(result["host_mutations"][0]["payload"]["set"])
    assert dispatch(page_binding)["host_mutations"] == result["host_mutations"]
    page_binding["action"].update(
        action_id="publish_post",
        input={"connection_ref": "connection", "post": {"kind": "text", "text": "Hello"}},
    )
    network[1].append(Response(headers={"x-restli-id": "urn:li:share:123"}))
    publication = dispatch(page_binding)
    assert publication["ok"], publication
    assert json.loads(network[0][0].data)["author"] == URN


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("organization_urn", "urn:li:person:123", "invalid_payload"),
        ("connection_ref", "other", "invalid_connection"),
        ("connection_type", "linkedin_member", "invalid_connection"),
        ("project_id", "other", "invalid_connection"),
        ("lifecycle_status", "disconnected", "authorization_required"),
        ("saved_urn", "urn:li:organization:321", "invalid_connection"),
    ],
)
def test_bind_page_rejects_invalid_requests_without_mutation(page_binding, field, value, error):
    action = page_binding["action"]
    target = action["target"]["connection"]
    if field in action["input"]:
        action["input"][field] = value
    elif field == "saved_urn":
        target["config"]["organization_urn"] = value
    elif field == "lifecycle_status":
        target["config"]["oauth"][field] = value
    else:
        target[field] = value
    result = dispatch(page_binding)
    assert result["error_code"] == error
    assert "host_mutations" not in result


def test_bind_page_in_test_mode_has_no_mutation(page_binding):
    page_binding["runtime_context"]["test_mode"] = True
    result = dispatch(page_binding)
    assert result["ok"], result
    assert "host_mutations" not in result
