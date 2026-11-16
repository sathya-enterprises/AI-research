"""How often does the number check KEEP a wrong number? (mutation test over the benchmark captures)

The scorecard's "numbers removed" only shows one side: a change that accepts more numbers lowers it
whether the extra numbers are right or wrong. This measures the other side without hand labels:

  1. take sentences the writer actually wrote that quote a table cell and that the check keeps;
  2. swap that number for another cell of the same source table -
       wrong row:    same column, another row ("Kenya's 22.67" written as India's)
       wrong column: same row, another column (another year or another metric of the same entity);
  3. run the number check on the altered sentence. It should be removed.

wronglyKept = altered sentences that survive. Lower is better; it should not rise when a change lowers
"numbers removed".

Usage: python scripts/benchmark_precision.py [--ids a,b] [--per-capture 40]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

_DIGITS_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _cached_fact_sheets() -> None:
    """The number check builds a fact sheet over every passage per call; build it once per capture."""
    import utils.fact_sheet as fs

    if getattr(fs, "_benchmark_cache", None) is not None:
        return
    original = fs.FactSheet
    cache: dict = {}

    def cached(passages):
        passages = list(passages)
        key = tuple(sorted((str(p.passage_id), len(str(p.text or ""))) for p in passages))
        if key not in cache:
            cache.clear()
            cache[key] = original(passages)
        return cache[key]

    fs._benchmark_cache = cache
    fs.FactSheet = cached


def _cited(sentence: str, texts: dict[str, str]) -> set[str]:
    from utils.number_policy import CITE_RE

    ids = {t.strip().upper() for g in CITE_RE.findall(sentence) for t in re.split(r"\s*[,;]\s*", g)}
    return {pid for pid in texts if pid in ids or pid.split(".")[0] in ids}


def _mutants(sentence: str, sheet, texts: dict[str, str]) -> list[tuple[str, str, str]]:
    """(kind, altered sentence, description) for each table-cell number in a kept sentence."""
    from utils.fact_sheet import lookup, unit_family
    from utils.number_policy import CITE_RE, statistic_tokens

    cited = _cited(sentence, texts)
    if not cited:
        return []
    out = []
    bare = CITE_RE.sub(" ", sentence)
    for token in statistic_tokens(sentence):
        start = sentence.find(token)
        if start < 0:
            continue
        unit = unit_family(sentence[start + len(token):start + len(token) + 16])
        found = lookup(sheet, token, sign="", unit=unit, sentence=bare, passage_ids=cited)
        fact = found.fact
        if found.status != "ok" or fact is None or fact.kind != "table" or not fact.label:
            continue
        table = [f for f in sheet.facts if f.passage_id == fact.passage_id and f.kind == "table" and f.unit == fact.unit]
        present = set(_DIGITS_RE.findall(sentence))
        for kind, pool in (
            ("wrong row", [f for f in table if f.metric == fact.metric and f.label != fact.label]),
            ("wrong column", [f for f in table if f.label == fact.label and f.metric != fact.metric]),
        ):
            for other in sorted(pool, key=lambda f: f.fid):
                digits = _DIGITS_RE.search(other.raw)
                if not digits or other.value.lstrip("-") == fact.value.lstrip("-") or digits.group(0) in present:
                    continue
                # The new value must not also be a cell of the claimed row/column (then it is not wrong).
                same = [f for f in sheet.candidates(digits.group(0), {fact.passage_id})
                        if (kind == "wrong row" and f.label == fact.label) or (kind == "wrong column" and f.metric == fact.metric)]
                if same:
                    continue
                altered = sentence[:start] + digits.group(0) + sentence[start + len(token):]
                out.append((kind, altered, f"{token} -> {digits.group(0)} ({other.label[:30]} / {other.metric[:30]})"))
                break
    return out


def precision_metrics(capture: dict, per_capture: int = 40) -> dict:
    import logging

    logging.disable(logging.INFO)
    _cached_fact_sheets()
    from types import SimpleNamespace

    import utils.fact_sheet as fs
    from utils.number_policy import apply_number_policy

    passages = ((capture.get("stages") or {}).get("evidence_store") or {})
    passages = (passages.get("data", passages) or {}).get("passages") or {}
    texts = {pid: (p.get("text") if isinstance(p, dict) else str(p)) or "" for pid, p in passages.items()}
    sheet = fs.FactSheet([SimpleNamespace(passage_id=pid, text=text) for pid, text in texts.items()])
    drafts = [c for c in capture.get("llmCalls") or []
              if c.get("purpose") in {"section", "section_extend", "executive_summary"} and c.get("ok") and c.get("llmResponse")]
    tested, kept, examples = 0, 0, []
    seen: set[str] = set()
    for call in drafts:
        for line in str(call["llmResponse"]).splitlines():
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z*])", line.strip()):
                if tested >= per_capture or sentence in seen or not _DIGITS_RE.search(sentence):
                    continue
                seen.add(sentence)
                if apply_number_policy(sentence, passage_texts=texts, allowed_numbers=set(), section_id="p").removed:
                    continue  # only sentences the check keeps as written
                for kind, altered, what in _mutants(sentence, sheet, texts):
                    if tested >= per_capture:
                        break
                    tested += 1
                    result = apply_number_policy(altered, passage_texts=texts, allowed_numbers=set(), section_id="p")
                    if not result.removed:
                        kept += 1
                        examples.append(f"[{kind}] {what}: {re.sub(r'\s+', ' ', altered)[:200]}")
    return {"mutantsTested": tested, "wronglyKept": kept, "wronglyKeptExamples": examples[:10]}


def _one(args: tuple[str, str, int, str]) -> dict:
    pid, capture_file, per, diagnostics = args
    capture = json.loads(((Path(diagnostics) if diagnostics else ROOT / "diagnostics") / capture_file).read_text(encoding="utf-8"))
    return {"id": pid, **precision_metrics(capture, per)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ids", default="")
    parser.add_argument("--per-capture", type=int, default=40)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--captures", default=str(ROOT / "benchmark" / "captures.json"))
    parser.add_argument("--diagnostics", default="", help="captures folder (default backend/diagnostics)")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    captures = json.loads(Path(args.captures).read_text(encoding="utf-8"))["captures"]
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
    jobs = [(pid, row["capture"], args.per_capture, args.diagnostics) for pid, row in captures.items()
            if row.get("capture") and (not wanted or pid in wanted)]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        rows = list(pool.map(_one, jobs))
    tested = sum(r["mutantsTested"] for r in rows)
    kept = sum(r["wronglyKept"] for r in rows)
    for row in rows:
        print(f"{row['id']}: {row['wronglyKept']}/{row['mutantsTested']} wrong numbers kept")
        for example in row["wronglyKeptExamples"][:3]:
            print(f"    {example}")
    print(f"\nTOTAL: {kept} of {tested} altered (wrong) numbers kept ({(100 * kept / tested) if tested else 0:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
