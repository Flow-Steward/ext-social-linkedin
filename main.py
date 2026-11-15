"""Bounded stdin/stdout subprocess adapter; all business behavior is in linkedin."""

from __future__ import annotations

import json
import sys

from linkedin.errors import LinkedInError, failure
from linkedin.runtime import dispatch

INPUT_LIMIT = 128 * 1024


def main() -> int:
    raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
    try:
        if len(raw) > INPUT_LIMIT:
            raise ValueError("input limit")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("input object")
    except (ValueError, UnicodeDecodeError, RecursionError):
        response = failure(LinkedInError("invalid_payload", "A bounded JSON object is required"))
    else:
        response = dispatch(payload)
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False) + "\n")
    return 0 if response["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
