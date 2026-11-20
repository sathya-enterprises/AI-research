"""Could the collected data have produced charts? Offline, per saved research capture.

Reads the fetched source texts in each capture (no LLM, search or fetch calls) and counts
chart-ready data that is ON the question's topic:

  source tables  - pipe tables in fetched pages with >= 3 numeric rows whose header,
                   caption or row labels share words with the question
  prose groups   - sentences carrying >= 3 figures in the same unit (%, crore, billion,
                   GW, tonnes...) about the topic, e.g. "Financials Rs 43,102 crore,
                   Telecom Rs 7,600 crore, Auto Rs 7,600 crore": a bar or line chart's worth
  report tables  - tables in the final report with >= 3 numeric rows

and compares that with the charts the report actually shipped.

Usage:
  python scripts/chart_opportunities.py [--json out.json] [--examples N] [capture.json ...]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.topic_scorecard import chartable_tables, default_captures, _stage  # noqa: E402

_STOP = set("""a an and are as at be by can compare compared did do does for from give had has have how in into is it its
last me of on or over past report the their them these this those to was were what when where which while who why will with
within years year india indian across between analyse analyze explain list down all any been both changed change grow grown
growth evolve evolved trends trend major main key top data using use show shows period recent latest""".split())
_UNIT = (r"(?:%|per\s*cent|lakh\s+crore|crore|cr\b|billion|bn\b|million|mn\b|trillion|tn\b|gw\b|mw\b|tonnes?|mt\b|"
         r"units\b|lakh\b|bps\b|basis\s+points|x\b)")
_FIGURE = re.compile(rf"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?\s*{_UNIT}", re.I)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b|\bQ[1-4]\s*(?:FY)?\s*'?\d{2,4}\b|\bFY\s*'?\d{2,4}\b", re.I)
_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def topic_words(question: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z][a-z0-9&-]{2,}", question.casefold()) if w not in _STOP}
    return words


def _hits(text: str, words: set[str]) -> int:
    low = text.casefold()
    return sum(1 for w in words if re.search(rf"\b{re.escape(w)}", low))


def source_texts(stages: dict) -> list[tuple[str, str]]:
    """(source title, fetched text) for every fetched page, official dataset and calculated dataset."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ("sources_after_article_fetch", "official_datasets", "calculated_sector_quarterly_dataset",
                "sources_after_site_crawl"):
        data = _stage(stages, key)
        rows = (data.get("sources") or data.get("rows") or []) if isinstance(data, dict) else (data or [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            url = str(row.get("url") or row.get("title") or "")
            text = "\n".join(str(row.get(k) or "") for k in ("pageContent", "fullContent", "text"))
            if url in seen or len(text.strip()) < 80:
                continue
            seen.add(url)
            out.append((str(row.get("title") or url)[:90], text))
    return out


def table_candidates(title: str, text: str, words: set[str]) -> list[str]:
    found: list[str] = []
    lines = text.splitlines() + [""]
    block: list[str] = []
    start = 0
    for i, line in enumerate(lines):
        if line.count("|") >= 2:
            if not block:
                start = i
            block.append(line)
            continue
        if block:
            body = [r for r in block[1:] if not re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", r)]
            numeric = [r for r in body if _NUM.search("|".join(r.strip().strip("|").split("|")[1:]))]
            context = " ".join(lines[max(0, start - 2):start] + block[:1] + [r.split("|")[1] if r.count("|") > 1 else r for r in body[:12]])
            if len(numeric) >= 3 and _hits(context + " " + title, words) >= 2:
                header = re.sub(r"\s+", " ", block[0]).strip()[:90]
                found.append(f"[table {len(numeric)} rows] {title[:50]} :: {header}")
            block = []
    return found


def prose_candidates(title: str, text: str, words: set[str]) -> list[str]:
    found: list[str] = []
    for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z])|\n", text):
        if "|" in sentence or len(sentence) > 600:
            continue
        figures = _FIGURE.findall(sentence)
        if len(figures) < 3 or _hits(sentence, words) < 1:
            continue
        units = [re.sub(r"[\d,.\s+−-]", "", f).casefold()[:4] for f in figures]
        top_unit = max(set(units), key=units.count)
        if units.count(top_unit) >= 3:
            kind = "series" if len(set(_YEAR.findall(sentence))) >= 3 else "comparison"
            found.append(f"[{kind} x{units.count(top_unit)}] {re.sub(r'\s+', ' ', sentence).strip()[:150]}")
    return found


def analyse(path: str, examples: int) -> dict | None:
    with open(path, encoding="utf-8") as fh:
        capture = json.load(fh)
    stages = capture.get("stages") or capture
    final = _stage(stages, "final_output") or {}
    meta = _stage(stages, "request_metadata") or {}
    question = str(meta.get("question") or "")
    if not final.get("report"):
        return None
    words = topic_words(question)
    tables: list[str] = []
    prose: list[str] = []
    for title, text in source_texts(stages):
        tables += table_candidates(title, text, words)
        prose += prose_candidates(title, text, words)
    prose = list(dict.fromkeys(prose))
    return {
        "question": question[:80], "tier": meta.get("reportTier") or "",
        "chartsShipped": len(final.get("charts") or []),
        "sourceTables": len(tables), "proseGroups": len(prose),
        "reportTables": chartable_tables(str(final.get("report") or "")),
        "tableExamples": tables[:examples], "proseExamples": prose[:examples],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="*")
    ap.add_argument("--json")
    ap.add_argument("--examples", type=int, default=0)
    args = ap.parse_args()
    rows = [r for r in (analyse(p, max(args.examples, 3)) for p in (args.captures or default_captures())) if r]
    print(f"{'shipped':>7} {'src-tbl':>7} {'prose':>6} {'rep-tbl':>7}  verdict      question")
    for r in rows:
        possible = r["sourceTables"] + r["proseGroups"] + r["reportTables"]
        verdict = ("no data" if possible == 0 else "missed" if r["chartsShipped"] == 0
                   else "under-used" if possible >= 3 * max(1, r["chartsShipped"]) else "ok")
        r["verdict"] = verdict
        print(f"{r['chartsShipped']:>7} {r['sourceTables']:>7} {r['proseGroups']:>6} {r['reportTables']:>7}  {verdict:12} {r['question']}")
        for example in (r["tableExamples"] + r["proseExamples"])[: args.examples * 2]:
            print(f"{'':42}- {example}")
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("no data", "missed", "under-used", "ok")}
    print(f"\nreports={len(rows)} " + " ".join(f"{k}={v}" for k, v in counts.items()))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
