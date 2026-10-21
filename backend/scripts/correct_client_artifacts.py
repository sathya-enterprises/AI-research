"""Minimal client-facing correction: query-derived opening headings only."""
from __future__ import annotations

import json
from pathlib import Path

from client_readiness_audit import OPENING_HEADINGS, REPORTS, ROOT


def main() -> None:
    changed = []
    for slug, _ in REPORTS:
        path = ROOT / slug / "report.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        heading = OPENING_HEADINGS[slug]
        presentation = data.get("presentation") or {}
        executive = presentation.get("executive_summary") or {}
        executive["heading"] = heading
        presentation["executive_summary"] = executive
        sections = presentation.get("sections") or []
        if sections and isinstance(sections[0], dict):
            first_title = str(sections[0].get("title") or "")
            if first_title.startswith("Executive Summary") or first_title.startswith("Executive Overview"):
                sections[0]["title"] = heading
        presentation["sections"] = sections
        data["presentation"] = presentation
        body = data.get("report") or ""
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") and "Executive Summary" in line:
                prefix = line[: len(line) - len(line.lstrip())]
                hashes = line[len(prefix):].split(" ", 1)[0]
                lines[i] = f"{prefix}{hashes} {heading}"
                break
        data["report"] = "\n".join(lines)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        changed.append(slug)
    print(json.dumps({"changed": changed, "scope": "opening headings and presentation metadata only"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
