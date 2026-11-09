"""Stage I client-readiness checklist for one report.

    python scripts/stage_i_checklist.py --result <status_final.json | v2_replay_result.json> --pdf <report.pdf>
                                        [--capture <capture.json>] [--label NAME] [--json out.json]

Each finding of the Stage I review is checked on the final report text, its charts and the rendered PDF,
and printed as PASS/FAIL with the evidence; the chart inventory (CHART LINT per figure) and the table
inventory follow.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load(path: str | None) -> dict:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


def _result(data: dict) -> tuple[dict, dict]:
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    return result, data.get("meta") or result.get("meta") or {}


def _exec_summary(report: str) -> str:
    m = re.search(r"(?ms)^## Executive Summary\s*\n(.*?)(?=^## )", report)
    return m.group(1).strip() if m else ""


def checklist(result: dict, pdf_bytes: bytes, capture: dict | None = None, today: date | None = None) -> list[dict]:
    import fitz
    from utils.chart_lint import lint_all
    from utils.flow_text import rendered_flow_findings
    from utils.leak_gate import INSTRUCTION_RE
    from utils.repetition import repetition_metrics
    from utils.stage_i_gates import collision_findings, period_findings, split_word_findings

    report = str(result.get("report") or "")
    charts = [c for c in result.get("charts") or [] if isinstance(c, dict)]
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = [p.get_text("text") or "" for p in doc]
    doc.close()
    pdf = "\n".join(pages)
    question = str((capture or {}).get("question") or result.get("question") or "")
    try:
        from utils.source_quality import window_start
        window = window_start(question or "past 8 quarters", today or date.today())
    except Exception:
        window = None
    rows: list[dict] = []

    def add(n, finding, ok, evidence):
        rows.append({"n": n, "finding": finding, "status": "PASS" if ok else "FAIL", "evidence": str(evidence)[:220]})

    # 1 leaks
    leaks = [m.group(0) for m in INSTRUCTION_RE.finditer(pdf)]
    add(1, "No leaked writer instructions (PDF)", not leaks, leaks[:3] or "none")
    # 2 periods
    stale = period_findings(report, window)
    fy_mix = re.findall(r"\bQ[1-4] 20\d{2}\b", " ".join(str(c.get("title")) for c in charts))
    add(2, "No out-of-window tables outside the labelled historical block", not stale, [s["caption"] for s in stale][:3] or "none")
    add(2, "Fiscal quarter labels in chart titles", not fy_mix, fy_mix[:3] or "none")
    prose_cal = [m.group(0) for line in report.split(chr(10)) if not line.lstrip().startswith(("|", "#"))
                 for m in re.finditer(r"\bQ[1-4] 20\d{2}\b", line)]
    add(2, "Fiscal quarter labels in the prose", not prose_cal, prose_cal[:3] or "none")
    # 3 flattened tables / diagrams
    flow = rendered_flow_findings(pdf)
    add(3, "No flattened tables or arrow diagrams in the PDF", not flow, [f["text"][:80] for f in flow][:3] or "none")
    # 4 thesis vs regime
    if capture:
        try:
            from utils.dataset_charts import find_quarterly_dataset
            from utils.evidence_store import store_from_passages
            from utils.regime import compute_regime, thesis_needs_rewrite
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from eval_report import restore_fetched_dataset
            stages = capture.get("stages") or {}
            store = store_from_passages(((stages.get("evidence_store") or {}).get("data") or {}).get("passages") or {})
            restore_fetched_dataset(store, stages, ((stages.get("canonical_source_manifest") or {}).get("data") or {}).get("sources") or [])
            regime = compute_regime(find_quarterly_dataset(store))
            opening = _exec_summary(report).split("\n\n")[0]
            reason = thesis_needs_rewrite(opening, regime) if regime else ""
            add(4, "Executive-summary thesis matches the computed regime and its dispersion", not reason,
                reason or (opening[:200] if regime else "n/a (no sector dataset in this report)"))
        except Exception as exc:
            add(4, "Executive-summary thesis matches the computed regime", False, f"check failed: {type(exc).__name__}: {exc}")
    # 5 self-description
    add(5, "Self-description resolved (no placeholder)", "{{CITED_SOURCE_COUNT}}" not in report, "placeholder absent")
    # 6 repetition
    metrics = repetition_metrics(report, charts)
    add(6, "Max mentions per statistic in body prose <= 2", metrics["max_headline_repeats"] <= 2,
        f"max {metrics['max_headline_repeats']} ({metrics['max_headline_stat']}), over cap {metrics['stats_over_cap']}")
    add(6, "No duplicate tables", metrics["duplicate_tables"] == 0, f"{metrics['duplicate_tables']} of {metrics['tables']}")
    add(6, "Figure boxes do not repeat the body; no CONTEXT 'Supports:'",
        metrics["figure_blocks_echoing_body"] == 0 and not re.search(r"(?i)\bcontext\s*\n?\s*supports:", pdf),
        f"echoing {metrics['figure_blocks_echoing_body']}, duplicate box sentences {metrics['duplicate_figure_block_sentences']}")
    # 7 data sanity: FS QTD agrees with Bank
    qtd = next((c for c in charts if re.search(r"(?i)quarter-to-date|QTD", str(c.get("title")))), None)
    vals = {}
    if qtd:
        for s in qtd.get("series") or []:
            for p in s.get("data") or []:
                vals[str(p.get("label")).casefold()] = p.get("value")
    fs, bank = vals.get("nifty financial services"), vals.get("nifty bank")
    ok = fs is None or bank is None or abs(float(fs) - float(bank)) <= 6
    add(7, "Financial Services QTD agrees with Bank (<= 6 pp) or is withheld", ok and "17.97" not in pdf,
        f"FS {fs}, Bank {bank}; '+17.97%' in PDF: {'17.97' in pdf}")
    # 8 charts and tables
    inventory, failing = lint_all(charts)
    add(8, "Every chart passes CHART LINT", failing == 0, f"{failing} failing of {len(charts)}")
    generic = re.findall(r"(?i)\band \d+ others?\b|Values \(%\)", pdf)
    add(8, "No generic chart titles in the PDF", not generic, generic[:3] or "none")
    table_nos = [int(n) for n in re.findall(r"\*\*Table (\d+):", report) + re.findall(r"(?m)^#{3,4} Table (\d+):", report)]
    from utils.repetition import _tables
    total_tables = len(_tables(report.split(chr(10))))
    add(8, "Tables numbered 1..N, separately from figures",
        table_nos == list(range(1, len(table_nos) + 1)) and len(table_nos) == total_tables,
        f"{len(table_nos)} numbered of {total_tables} tables")
    fig_tables = re.findall(r"\*\*Figure \d+:[^\n]*\*\*\s*\n\s*\|", report)
    add(8, "No table captioned as a figure", not fig_tables, len(fig_tables))
    rationale = re.findall(r"(?i)chronological series", pdf)
    add(8, "No chart-type rationale text", not rationale, len(rationale))
    # 9 smaller items
    small = {
        "a/an": re.findall(r"\b[Aa] (?!one|once|eu|uni|use|usu|ur)[aeio][a-z]+|\b[Aa]n (?!hour|honest|heir)[bcdfgjklmnpqrstvwyz][a-z]+", pdf),
        "truncated date": re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{2}\b(?!\d)", pdf),
        "run-together source": re.findall(r"supplied\)[A-Z]", pdf),
        "calculated dataset shown as primary official": re.findall(
            r"(?i)(?:No public URL supplied|calculated dataset)\)[^\[]{0,200}?primary\s+official", pdf),
        "raw bullet mid-sentence": re.findall(r"[a-z,] \* [a-z]", pdf),
        "ordinal gap": [p for p in re.split(r"\n\s*\n", _exec_summary(report))
                        if len(re.findall(r"\b(?:First|Second|Third|Fourth|Fifth|Sixth),", p)) >= 2
                        and re.findall(r"\b(First|Second|Third|Fourth|Fifth|Sixth),", p)
                        != ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"][:len(re.findall(r"\b(?:First|Second|Third|Fourth|Fifth|Sixth),", p))]],
        "empty What to watch": re.findall(r"(?m)^>\s*\[!WATCH[^\]]*\]\s*(?:\[S[^\]]*\])?\s*\.?\s*$", report),
    }
    for name, hits in small.items():
        add(9, f"No {name}", not hits, [str(h)[:60] for h in hits][:3] or "none")
    def _body(t):  # page text without the running header / page number
        return re.sub(r"(?s)^.*?R E S E A R C H\s*\n\d+\s*\n[^\n]*\n", "", t).strip()
    blank = [i + 1 for i, t in enumerate(pages[1:-1], 1) if len(_body(t)) < 40]
    spill = [i + 1 for i, t in enumerate(pages[1:-1], 1) if 40 <= len(_body(t)) < 350]
    add(9, "No blank (header-only) pages", not blank, blank[:5] or f"none; {len(spill)} short section-end page(s) (layout: sections start on a new page)")
    # 10 rendered gates
    splits = split_word_findings(pdf, report)
    collisions = collision_findings(pdf_bytes)
    add(10, "No split words in the PDF", not splits, [s["text"] for s in splits][:3] or "none")
    add(10, "No overlapping labels in the PDF", not collisions, [c["text"] for c in collisions][:3] or "none")
    return rows, inventory


def table_inventory(report: str) -> list[dict]:
    out = []
    lines = report.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:\*\*|#{3,4}\s+)(Table \d+):\s*(.+?)(?:\*\*)?\s*$", line)
        if m:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            header = lines[j] if j < len(lines) else ""
            k = j + 2
            while k < len(lines) and lines[k].strip().startswith("|"):
                k += 1
            src = lines[k] if k < len(lines) and lines[k].strip().startswith("Source") else ""
            out.append({"table": m.group(1), "title": m.group(2)[:90], "columns": header.count("|") - 1,
                        "source": src.strip()[:60] or "MISSING"})
    return out


def render(rows: list[dict], inventory: list[dict], tables: list[dict], label: str) -> str:
    out = [f"## Stage I checklist — {label}", "", "| # | Finding | Status | Evidence |", "|---|---|---|---|"]
    out += [f"| {r['n']} | {r['finding']} | {r['status']} | {str(r['evidence']).replace('|', '/')} |" for r in rows]
    out += ["", f"### Chart inventory — {label}", "", "| Figure | Title | Type | X | Y | Period | Source | Lint |", "|---|---|---|---|---|---|---|---|"]
    out += [f"| {r['figure']} | {r['title']} | {r['type']} | {r['x']} | {r['y']} | {r['period']} | {r['source'][:60]} | {r['lint']} |".replace("\n", " ")
            for r in inventory]
    out += ["", f"### Table inventory — {label}", "", "| Table | Title | Columns | Source |", "|---|---|---|---|"]
    out += [f"| {t['table']} | {t['title']} | {t['columns']} | {t['source']} |" for t in tables]
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--capture")
    ap.add_argument("--label", default="report")
    ap.add_argument("--today", default="")
    ap.add_argument("--json")
    ap.add_argument("--md")
    args = ap.parse_args()
    result, _meta = _result(_load(args.result))
    capture = _load(args.capture) if args.capture else None
    today = date.fromisoformat(args.today) if args.today else None
    rows, inventory = checklist(result, Path(args.pdf).read_bytes(), capture, today)
    tables = table_inventory(str(result.get("report") or ""))
    text = render(rows, inventory, tables, args.label)
    print(text)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps({"rows": rows, "charts": inventory, "tables": tables}, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    fails = sum(1 for r in rows if r["status"] == "FAIL")
    print(f"\n{len(rows) - fails} PASS, {fails} FAIL")


if __name__ == "__main__":
    main()
