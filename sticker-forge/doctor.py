from __future__ import annotations

import json
import sys
from server.preflight import run_preflight


def main() -> int:
    report = run_preflight()
    print("\nSticker Forge - self-check")
    print("---------------------------------------------")
    for item in report.get("checks", []):
        marker = "✓" if item["status"] == "ok" else ("!" if item["status"] == "warn" else "✗")
        print(f"{marker}  {item['name']:<12} {item['detail']}")
    print("\nJSON:")
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
