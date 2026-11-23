"""Read-only integration with the actual host when run through fs-audit.

Private host imports are confined to tests; the shipped runtime uses the public SDK.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import Response
from linkedin.runtime import dispatch

pytestmark = pytest.mark.skipif(not Path("/app/core").is_dir(), reason="requires fs-audit host")
ROOT = Path(__file__).resolve().parents[1]


def test_distribution_descriptor_is_accepted_by_actual_host(monkeypatch):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions.extension_descriptor import validate_extension_descriptor

    descriptor = yaml.safe_load((ROOT / "extension-descriptor.yaml").read_text())
    result = validate_extension_descriptor(
        descriptor, artifact_resolver=lambda ref, *args: (ROOT / ref).is_file()
    )
    assert result["valid"] is True, result["errors"]
    manifest = yaml.safe_load((ROOT / "extension.yaml").read_text())
    assert descriptor["extension_id"] == manifest["extension_id"]
    assert descriptor["release_version"] == manifest["version"]
    assert all(entry["publish"] is False for entry in descriptor["artifacts"]["workflows"])


def test_catalog_install_validation_inherits_the_bundle_oauth_contract(monkeypatch):
    """Install validates the descriptor file with its bundle, not the bare dict.

    1.0.0 to 1.0.3 passed the dict check above but failed catalog install with
    "Descriptor validation failed": the descriptor declared its own
    extension_contract_v2 without oauth_providers, so the OAuth providers and
    callback declared only in extension.yaml were never inherited.
    """
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions.descriptor_bundle_runtime import (
        descriptor_with_bundle_runtime,
    )
    from core.application.extensions.lifecycle import validate_extension_descriptor_file

    path = ROOT / "extension-descriptor.yaml"
    result = validate_extension_descriptor_file(path)
    assert result["valid"] is True, result["errors"]
    merged, errors = descriptor_with_bundle_runtime(
        descriptor_path=path, normalized_descriptor=result["normalized"]
    )
    assert errors == []
    providers = {row["provider_id"] for row in merged["oauth_providers"]}
    assert providers == {"linkedin_member_oauth", "linkedin_page_oauth"}
    assert {row["auth"]["provider_id"] for row in merged["connection_types"]} == providers
    assert {row["callback_id"] for row in merged["callbacks"]} == {"oauth_authorization"}
    assert {row["operation_id"] for row in merged["operation_manifest"]} == {
        "validate_post",
        "upload_image",
        "publish_post",
        "test_connection",
    }


def test_each_oauth_provider_can_start_from_its_setup_page(monkeypatch):
    """The Connect panel exists only for a setup page that declares a connection_form.

    1.0.4 installed, but the setup page had no connection_form, so the host showed
    only the app configuration and the member or Page account could not be
    connected from the UI. The host's own authorization check rejects a return
    page that does not declare the form for the provider, connection type and intent.
    """
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions.descriptor_bundle_runtime import (
        descriptor_with_bundle_runtime,
    )
    from core.application.extensions.lifecycle import validate_extension_descriptor_file
    from core.application.extensions.oauth_authorization import OAuthAuthorizationService

    path = ROOT / "extension-descriptor.yaml"
    result = validate_extension_descriptor_file(path)
    assert result["valid"] is True, result["errors"]
    merged, errors = descriptor_with_bundle_runtime(
        descriptor_path=path, normalized_descriptor=result["normalized"]
    )
    assert errors == []
    for provider in merged["oauth_providers"]:
        connection_type = next(
            row["connection_type_id"]
            for row in merged["connection_types"]
            if row["auth"]["provider_id"] == provider["provider_id"]
        )
        intent_id = provider["authorization_intents"][0]["intent_id"]
        assert OAuthAuthorizationService._return_page_owns_intent(
            merged,
            page_id=provider["setup_page_id"],
            provider_id=provider["provider_id"],
            connection_type=connection_type,
            intent_id=intent_id,
        ), provider["provider_id"]


@pytest.mark.parametrize("name", ["publish_prepared_post.yaml", "publish_image.yaml"])
def test_workflow_example_has_valid_native_graph(monkeypatch, name):
    monkeypatch.syspath_prepend("/app")
    from core.application.workflows.workflow_definition_validation import (
        validate_workflow_definition,
    )

    record = yaml.safe_load((ROOT / "examples" / name).read_text())
    definition = {**record, **record["settings"]}
    assert validate_workflow_definition(definition) == []


def test_authenticated_subject_reaches_actual_connector_target(monkeypatch, payload):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions.oauth_callback_policy import _post_connect_config
    from core.application.extensions.oauth_post_connect import parse_oauth_post_connect_result
    from core.application.orchestration.connector_connection import _build_runtime_connection_target

    identity = parse_oauth_post_connect_result({"subject": "member123", "health": "ok"})
    config, _ = _post_connect_config({}, identity)
    row = dict(payload["action"]["target"]["connection"])
    row.update(
        connection_config=config,
        extension_id="flowsteward.linkedin",
        extension_install_id="install",
        enabled=True,
    )
    db = SimpleNamespace(get_project_connection=lambda **kw: row)
    target, logged = _build_runtime_connection_target(
        db,
        connection_binding={"connection_id": "connection"},
        runtime_context=payload["runtime_context"],
        extension_id="flowsteward.linkedin",
        connector_install_id="install",
        required_connection_type_ids=frozenset({"linkedin_member"}),
    )
    assert target["config"]["oauth"]["subject"] == "member123"
    assert logged["secrets"] == {}


@pytest.mark.parametrize("definitive,expected", [(True, "failed"), (False, "timeout_unknown")])
def test_effect_confirmation_reads_real_metadata(monkeypatch, definitive, expected):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions import external_effects

    writes = []

    def update(db, **kw):
        writes.append(kw)
        return kw

    monkeypatch.setattr(
        external_effects.external_effect_store, "update_external_effect_status", update
    )
    response = {"ok": False, "error_code": "invalid_payload" if definitive else "timeout_unknown"}
    if definitive:
        response["definitely_no_external_effect"] = True
    else:
        response["external_effect_status"] = "timeout_unknown"
    external_effects.confirm_external_effect_from_invocation(
        object(),
        effect_id="effect",
        invocation={"status": "failed", "response": response},
    )
    assert writes[0]["status"] == expected


def test_success_receipt_survives_host_replay_projection(monkeypatch, payload, network):
    monkeypatch.syspath_prepend("/app")
    from core.application.extensions.external_effect_replay import project_external_effect_replay
    from core.application.extensions.external_effects import apply_external_effect_confirmation

    network[1].append(Response(headers={"x-restli-id": "urn:li:share:123"}))
    response = dispatch(payload)
    operations = yaml.safe_load((ROOT / "contracts/operation_manifest.yaml").read_text())[
        "operations"
    ]
    projected = project_external_effect_replay(
        extension={"operation_manifest": operations},
        operation_id="publish_post",
        invocation={"status": "succeeded", "response": response},
        protected_values=["fixture-bearer"],
    )
    assert projected["response"]["result"] == response["result"]
    replay = apply_external_effect_confirmation(
        {"status": "succeeded"},
        {"status": "succeeded", "__confirmed_replay_invocation": projected},
    )
    assert replay == projected
    assert len(network[0]) == 1
