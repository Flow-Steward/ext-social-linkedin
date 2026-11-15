import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "raw,code",
    [(b"[]", "invalid_payload"), (b"{", "invalid_payload"), (b"{}", "invalid_host_envelope")],
)
def test_invalid_subprocess_input_has_canonical_failure(raw, code):
    proc = subprocess.run(
        [sys.executable, "main.py"],
        input=raw,
        capture_output=True,
        cwd=ROOT,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 2
    result = json.loads(proc.stdout)
    assert result["ok"] is False
    assert result["error_code"] == code
    assert result["errors"][0] == {"code": code, "message": result["error"]}
    assert proc.stderr == b""


def test_subprocess_validate_without_credentials(payload):
    payload["action"] = {
        "action_id": "validate_post",
        "input": {"post": {"kind": "text", "text": "hello"}},
    }
    proc = subprocess.run(
        [sys.executable, "main.py"],
        input=json.dumps(payload).encode(),
        capture_output=True,
        cwd=ROOT,
        timeout=5,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["result"]["valid"] is True


def test_local_health_is_healthy():
    proc = subprocess.run(
        [sys.executable, "health.py"], capture_output=True, cwd=ROOT, timeout=5, check=False
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["status"] == "healthy"
