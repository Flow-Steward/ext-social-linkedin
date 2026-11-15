"""Local package health; never performs a provider request."""

import json


def main() -> int:
    print(json.dumps({"ok": True, "status": "healthy"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
