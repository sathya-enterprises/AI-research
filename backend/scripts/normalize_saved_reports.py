"""Apply the production report-text normalizer to saved report contracts.

This is a maintenance utility for artifacts produced before a shared
normalization fix. It does not search, synthesize, change claims, or alter
source accounting.
"""
from __future__ import annotations

import json
from pathlib import Path

from routes.report import _normalise_report_text


ROOT = Path("artifacts/client-demo-10-reports")


def main() -> int:
    changed = []
    for path in sorted(ROOT.glob("*/report.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        before = str(payload.get("report") or "")
        after = _normalise_report_text(before)
        if after == before:
            continue
        payload["report"] = after
        payload["report_chars"] = len(after)
        scope = payload.get("researchScope") or {}
        depth = scope.get("depth") or {}
        if depth:
            depth["report_characters"] = len(after)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changed.append(path.parent.name)
    print(json.dumps({"changed": changed, "count": len(changed)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
