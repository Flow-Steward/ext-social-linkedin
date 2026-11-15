import copy
import json

import pytest


@pytest.fixture
def payload():
    return {
        "contract_version": "extension_host_v2",
        "runtime_context": {
            "account_id": "account",
            "project_id": "project",
            "job_id": "job",
            "group_id": None,
            "for_each_id": None,
            "step_id": "publish",
            "step_execution_id": "execution",
            "test_mode": False,
        },
        "mode": "action",
        "host": {"scope": {"account_id": "account", "project_id": "project"}},
        "action": {
            "action_id": "publish_post",
            "input": {"connection_ref": "connection", "post": {"kind": "text", "text": "Hello"}},
            "target": {
                "connection": {
                    "connection_id": "connection",
                    "account_id": "account",
                    "project_id": "project",
                    "connection_type": "linkedin_member",
                    "secrets": {"access_token": "fixture-bearer"},
                    "config": {"oauth": {"subject": "member123"}},
                }
            },
        },
    }


class Response:
    def __init__(self, status=201, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n):
        chunk, self.body = self.body[:n], self.body[n:]
        return chunk


@pytest.fixture
def network(monkeypatch):
    from linkedin import transport

    calls, responses = [], []

    def opened(request, **kwargs):
        calls.append(copy.copy(request))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(transport, "open_pinned_url", opened)
    return calls, responses


def json_response(body, status=200, headers=None):
    return Response(status, json.dumps(body).encode(), headers)
