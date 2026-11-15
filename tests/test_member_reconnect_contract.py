"""Real host/extension handoff must keep the connection's publishing author."""

from pathlib import Path

import pytest

from conftest import json_response
from linkedin.runtime import dispatch

pytestmark = pytest.mark.skipif(not Path("/app/core").is_dir(), reason="requires fs-audit host")


@pytest.mark.parametrize("fail_first", [False, True])
@pytest.mark.parametrize("new_subject", ["member-A", "member-B"])
def test_member_reconnect_keeps_existing_author(
    monkeypatch, payload, network, fail_first, new_subject
):
    # Actual host service + actual LinkedIn identity action. Only repository,
    # token exchange and UserInfo network use the host's in-memory fixtures.
    monkeypatch.syspath_prepend("/app")
    monkeypatch.syspath_prepend("/app/tests/unit")
    monkeypatch.chdir("/app")
    import test_extension_oauth_callback as host

    assert host.compile_oauth_providers(host._manifest())[0].callback_verification is None
    repository = host._Repository()
    repository.state["existing_connection_id"] = "connection-existing"
    repository.state["intent_payload"] = {"connection_credential_revision": 7}
    repository.connection["connection_config"]["oauth"]["subject"] = "member-A"
    if fail_first:
        network[1].append(json_response({}, status=503))
    network[1].append(json_response({"sub": new_subject, "name": "Verified Member"}))

    def verify_new_identity(**values):
        payload["action"] = {
            "action_id": "verify_member_identity",
            "input": {},
            "target": values["target"],
        }
        response = dispatch(payload)
        return {"status": "succeeded" if response["ok"] else "failed", "response": response}

    service = host._service(repository, post_connect=verify_new_identity)
    result = host._handle(service)
    if fail_first:
        assert result.outcome == "needs_attention"
        result = service.retry_post_connect(
            tenant_id="tenant-a",
            account_id="account-a",
            project_id="project-a",
            extension_install_id="install-a",
            extension_id="fixture.extension-oauth",
            provider_id="example",
            configuration_id=repository.state["configuration_id"],
            connection_id="connection-existing",
            connection_type="example_mail",
        )
    oauth = repository.connection["connection_config"]["oauth"]
    assert result.connection_id == "connection-existing"
    assert oauth["subject"] == "member-A"
    if new_subject == "member-B":
        assert result.outcome == "needs_attention"
        assert oauth["last_error_code"] == "subject_changed"
        assert repository.post_connect_updates[-1]["runtime_status"] == "unavailable"
    else:
        assert result.outcome == "connected"
        assert "replaced_subject" not in oauth
    assert len(network[0]) == (2 if fail_first else 1)
    assert all(request.get_method() == "GET" for request in network[0])
