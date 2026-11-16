"""Compare live runs before and after fact ids: does the writer use [[F3]], and does it help?

For each prompt in both capture maps:
  offered   fact ids shown to the writer (FACT SHEET lines "- F<n> |" in section prompts)
  used      [[F<n>]] references in the writer's section drafts
  typed     statistics the writer still typed (outside ids and calculations)
  removed   sentences the live number policy removed, with the reasons
  outcome   publication outcome, words
Usage: python scripts/factid_compare.py OLD_captures.json NEW_captures.json [ids]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.number_policy import statistic_tokens  # noqa: E402

REF = re.compile(r"\[\[\s*(?:fact\s*:\s*)?F\d+\s*\]\]", re.I)
CALC = re.compile(r"\[\[\s*calc\s*:[^\]]*\]\]", re.I)
OFFERED = re.compile(r"(?m)^- F\d+ \|")


def stage(capture: dict, name: str) -> dict:
    s = (capture.get("stages") or {}).get(name) or {}
    return s.get("data", s) if isinstance(s, dict) else {}


def measure(capture_file: str) -> dict:
    cap = json.loads((ROOT / "diagnostics" / capture_file).read_text(encoding="utf-8"))
    calls = [c for c in cap.get("llmCalls") or [] if c.get("purpose") in {"section", "section_extend"} and c.get("ok")]
    offered = sum(len(OFFERED.findall(str(c.get("prompt") or c.get("llmPrompt") or ""))) for c in calls)
    used = typed = 0
    for c in calls:
        text = str(c.get("llmResponse") or "").split("<<<SUMMARY>>>")[0]
        used += len(REF.findall(text))
        typed += len(statistic_tokens(REF.sub(" ", CALC.sub(" ", text))))
    removed = (stage(cap, "pipeline_v2").get("numberPolicy") or {}).get("sentencesRemoved") or []
    reasons = Counter(re.sub(r"[-+−]?\d[\d,]*(?:\.\d+)?", "N", str(r.get("reason", "")))[:70] for r in removed)
    final = stage(cap, "final_output")
    v2 = stage(cap, "pipeline_v2")
    return {"sections": len(calls), "offered": offered, "used": used, "typed": typed, "removed": len(removed),
            "reasons": reasons, "outcome": final.get("publicationOutcome") or final.get("publicationStatus"),
            "words": v2.get("words")}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    old = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["captures"]
    new = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["captures"]
    ids = sys.argv[3].split(",") if len(sys.argv) > 3 else [k for k in new if k in old]
    tot = Counter()
    for pid in ids:
        a, b = old.get(pid, {}).get("capture"), new.get(pid, {}).get("capture")
        if not a or not b or a == b:
            print(f"{pid}: not re-run yet")
            continue
        ma, mb = measure(a), measure(b)
        print(f"\n== {pid}")
        for label, m in (("before", ma), ("after ", mb)):
            print(f"  {label}: sections {m['sections']:2d} | ids offered {m['offered']:4d} used {m['used']:3d} | "
                  f"typed {m['typed']:4d} | removed {m['removed']:3d} | {m['outcome']} | words {m['words']}")
        for reason, n in mb["reasons"].most_common(6):
            print(f"      after: {n:3d}  {reason}")
        for k in ("used", "typed", "removed"):
            tot[f"{k}_before"] += ma[k]
            tot[f"{k}_after"] += mb[k]
    if tot:
        print(f"\nTOTAL ids used {tot['used_before']} -> {tot['used_after']} | typed numbers {tot['typed_before']} -> "
              f"{tot['typed_after']} | sentences removed {tot['removed_before']} -> {tot['removed_after']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
