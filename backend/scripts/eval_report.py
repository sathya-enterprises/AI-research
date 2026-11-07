"""Offline evaluation of research-report runs (no API credits).

A *run directory* holds the artefacts of one live report job:

    capture.json        research capture (GG_RESEARCH_CAPTURE=1)
    status_final.json   final /api/chat/report/status payload (contains `result`)
    report.pdf          PDF from /api/chat/report/pdf            (optional)
    server.log          backend log for that job                  (optional)

Run directories live under backend/diagnostics/eval_runs/ (gitignored).

What is replayed offline and what is not:
  * Output metrics are computed from the saved report/PDF.
  * --rerender rebuilds the PDF from the saved result with the *current*
    renderer (routes/pdf.py), so PDF fixes can be measured without credits.
  * --retrieval re-runs the *current* retrieval scoring (relevance gate,
    source quality, dedup, evidence store) on the captured search results.
  * Report text cannot be regenerated offline: LLM calls are not captured.
    Writer changes are measured with a new live capture.

Usage (from backend/):
    python scripts/eval_report.py                       # all runs under diagnostics/eval_runs
    python scripts/eval_report.py RUN_DIR [RUN_DIR ...] --rerender --retrieval
    python scripts/eval_report.py --save-baseline scripts/eval_baselines/x.json
    python scripts/eval_report.py --baseline scripts/eval_baselines/x.json    # before/after table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
DEFAULT_RUNS = BACKEND / "diagnostics" / "eval_runs"

CITE_RE = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)*)\]")
MARKER_RE = re.compile(r"\[(?:CHART|TABLE|WEB_IMG|FILE_IMG|IMAGE)_\d+\]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
HEADING_RE = re.compile(r"(?m)^(#{2,4})\s+(.+?)\s*#*\s*$")
LIMITATION_RE = re.compile(r"(?i)quantitative evidence limitation|numerical statements were omitted|not verified in retrieved")
BACK_MATTER_RE = re.compile(r"(?i)\b(methodology|data dictionary|appendix|sources|references|disclaimer|glossary)\b")
STOP = {"the", "and", "of", "in", "a", "an", "to", "for", "vs", "on", "with", "by", "its", "india", "indian"}


# ── helpers ───────────────────────────────────────────────────────────────────
def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _cited_ids(text: str) -> set[str]:
    out: set[str] = set()
    for group in CITE_RE.findall(text or ""):
        out.update(re.findall(r"S\d+", group))
    return out


def _norm_heading(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", " ", text.casefold()))
    return {w.rstrip("s") for w in words if w not in STOP and len(w) > 2}


def duplicate_headings(headings: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Pairs of headings that cover the same topic (token Jaccard >= 0.5, >=2 shared tokens)."""
    pairs = []
    norm = [(lvl, h, _norm_heading(h)) for lvl, h in headings]
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            a, b = norm[i][2], norm[j][2]
            if not a or not b:
                continue
            shared = a & b
            if len(shared) >= 2 and len(shared) / len(a | b) >= 0.5:
                pairs.append((norm[i][1], norm[j][1]))
    return pairs


def husk_blocks(report: str) -> list[str]:
    """Blocks whose only content is citation markers / punctuation (e.g. '> [S5], [S37]')."""
    husks = []
    for block in re.split(r"\n\s*\n", report or ""):
        for line in block.splitlines():
            stripped = re.sub(r"^\s*(?:>|[-*+]|\d+[.)])\s*", "", line).strip()
            if not stripped or stripped.startswith("#") or "|" in stripped:
                continue
            residue = CITE_RE.sub("", stripped)
            residue = re.sub(r"[\s,.;:()\-–—|*_]+", "", residue)
            if CITE_RE.search(stripped) and not residue:
                husks.append(line.strip()[:80])
    return husks


def sentence_fragments(report: str) -> list[str]:
    """Paragraphs / bullets that start mid-sentence or end on a cut number (guard residue)."""
    out = []
    for line in (report or "").splitlines():
        body = re.sub(r"^\s*(?:>|[-*+]|\d+[.)])\s*", "", line).strip()
        if not body or body.startswith(("#", "|", "[", "(", "`")):
            continue
        if re.match(r"^(?:despite|and|but|while|which|whereas|although|or|with|as well as)\b", body):
            out.append(body[:80])
        elif re.search(r"\b(?:at|to|of|by) \d{1,3}\.\s*$", body):
            out.append(body[-60:])
    return out


def empty_headings(report: str) -> list[str]:
    """Headings with no body before the next heading of the same/higher level (or only a limitation banner)."""
    matches = list(HEADING_RE.finditer(report or ""))
    out = []
    for idx, m in enumerate(matches):
        level = len(m.group(1))
        end = len(report)
        for nxt in matches[idx + 1:]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        else:
            nxt = None
        body = report[m.end():end]
        # Sub-headings count as content for a parent heading.
        has_child = any(m.end() <= c.start() < end and len(c.group(1)) > level for c in matches)
        text = LIMITATION_RE.sub("", re.sub(r"(?m)^\s*>.*$", lambda x: x.group(0) if not LIMITATION_RE.search(x.group(0)) else "", body))
        text = MARKER_RE.sub(" CHART ", text)
        words = WORD_RE.findall(CITE_RE.sub("", text))
        if not has_child and len(words) < 8:
            out.append(m.group(2)[:80])
    return out


def misplaced_sections(headings: list[tuple[int, str]]) -> list[str]:
    """Analytical H2 sections that appear after a methodology/appendix-type H2."""
    seen_back = False
    out = []
    for level, text in headings:
        if level != 2:
            continue
        if BACK_MATTER_RE.search(text):
            seen_back = True
        elif seen_back and not re.search(r"(?i)conclusion|outlook|summary", text):
            out.append(text[:80])
    return out


def _chart_labels_and_values(chart: dict) -> tuple[set[str], set[str]]:
    labels: set[str] = set()
    values: set[str] = set()
    for series in chart.get("series") or []:
        if not isinstance(series, dict):
            continue
        for point in series.get("data") or series.get("values") or []:
            if not isinstance(point, dict):
                continue
            label = str(next((point[k] for k in ("label", "name", "category", "period", "x") if point.get(k) is not None), "")).strip()
            if label:
                labels.add(label)
            value = next((point[k] for k in ("value", "y", "amount") if k in point), None)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            forms = {f"{number:g}", f"{number:.1f}", f"{number:.2f}"}
            # One-digit integers ("1", "5") match almost any text; require a more specific form.
            values.update(f for f in forms if len(f.replace("-", "").replace(".", "")) >= 2)
    return labels, values


def charts_explained(report: str, charts: list) -> tuple[int, int]:
    """(charts whose adjacent prose interprets them, charts with an explicit 'Figure/Chart N' reference).

    Interpreting = the paragraphs around the placeholder refer to the figure, or name a plotted
    label together with one of its plotted values. Sharing topic words is not enough.
    """
    explained = referenced = 0
    text = report or ""
    for i, chart in enumerate(charts or [], 1):
        token = f"[CHART_{i}]"
        pos = text.find(token)
        if pos < 0:
            continue
        raw_window = (text[max(0, pos - 700): pos] + " " + text[pos + len(token): pos + len(token) + 700])
        window = raw_window.casefold()
        mentions_figure = bool(re.search(rf"\b(?:figure|chart|exhibit)\s+{i}\b", window))
        labels, values = _chart_labels_and_values(chart)

        def label_in_window(label: str) -> bool:
            # Short labels ("IT", "PSU") are matched case-sensitively so the pronoun "it" is not a hit.
            hay, needle = (raw_window, label) if len(label) <= 4 else (window, label.casefold())
            return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])", hay))

        label_hit = any(label_in_window(lbl) for lbl in labels if len(lbl) >= 2)
        value_hit = any(re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", window) for v in values)
        if mentions_figure:
            referenced += 1
        if mentions_figure or (label_hit and value_hit):
            explained += 1
    return explained, referenced


def numbers_removed(capture: dict | None, log_text: str) -> int | None:
    stage = ((capture or {}).get("stages") or {}).get("evidence_guard") or {}
    data = stage.get("data") if isinstance(stage, dict) else None
    if isinstance(data, dict) and data.get("redactedValues") is not None:
        return int(data.get("redactedValues") or 0)
    found = re.findall(r"evidence guard redacted unsupported financial/percentage value\(s\)=(\d+)", log_text or "")
    return sum(int(x) for x in found) if found else None


def pdf_metrics(pdf_bytes: bytes | None) -> dict:
    if not pdf_bytes:
        return {}
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    texts = [p.get_text() for p in doc]
    words = [len(WORD_RE.findall(t)) for t in texts]
    joined = "\n".join(texts)
    raw = sorted(set(re.findall(r"\[(?:CHART|TABLE|WEB_IMG|FILE_IMG|IMAGE)_\d+\]|\[\[[A-Z ]+|```|NO_ADDITIONAL_EVIDENCE", joined)))
    sparse = [i + 1 for i, w in enumerate(words) if w < 60 and 0 < i < len(words) - 1]
    return {"pages": len(doc), "pdf_words": sum(words), "raw_markers_in_pdf": raw, "sparse_pages": sparse}


async def _rerender(result: dict, question: str, tier: str) -> tuple[bytes | None, str | None]:
    from starlette.requests import Request
    from routes.pdf import generate_pdf

    body = {
        "report": result.get("report"), "title": result.get("title"), "charts": result.get("charts") or [],
        "images": result.get("images") or [], "question": question, "keyStats": result.get("keyStats") or [],
        "summary": result.get("summary") or "", "fileImages": [], "sources": result.get("sources") or [],
        "theme": result.get("theme"), "presentation": result.get("presentation"), "reportTier": result.get("reportTier") or tier,
        "reportStyle": result.get("reportStyle") or "market_currents", "kicker": result.get("kicker") or "",
        "draftExport": result.get("publicationOutcome") not in (None, "published"),
        "publicationOutcome": result.get("publicationOutcome"),
    }
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    response = await generate_pdf(Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive))
    content_type = response.headers.get("content-type", "")
    if "pdf" in content_type:
        return bytes(response.body), None
    return None, f"HTTP {response.status_code}: {bytes(response.body)[:200]!r}"


def retrieval_replay(capture: dict) -> dict:
    """Re-run current retrieval scoring on the captured (pre-gate) search results."""
    from routes import report as R
    stages = capture.get("stages") or {}
    question = str(capture.get("question") or "")
    tier = str(capture.get("reportTier") or "detailed")
    # Stage lists in the capture are truncated (60 items); the per-query search
    # results are complete, so replay from those (deduped by URL, first wins).
    before, seen = [], set()
    for call in capture.get("searchCalls") or []:
        for query in call.get("perQuery") or []:
            for row in query.get("results") or []:
                if isinstance(row, dict) and row.get("url") and row["url"] not in seen:
                    seen.add(row["url"])
                    before.append(dict(row))
    if not before:
        before = [s for s in (((stages.get("sources_before_relevance_filter") or {}).get("data") or {}).get("sources") or []) if isinstance(s, dict)]
    intent = None
    kept = R._filter_relevant_sources([dict(s) for s in before], question, intent, report_tier=tier)
    kept = R._dedupe_enriched_sources(kept)
    out = {"replay_candidates": len(before), "replay_kept_after_relevance": len(kept)}
    try:
        from utils.source_quality import assess_sources
        assessed, quality = assess_sources(kept, question=question, report_tier=tier)
        out.update({"replay_kept_after_quality": len(assessed), **{f"replay_{k}": v for k, v in quality.items()}})
        kept = assessed
    except ImportError:
        out["replay_quality"] = "n/a (utils.source_quality not present)"
    try:
        from utils.evidence_store import build_evidence_store
        store = build_evidence_store(kept, question=question)
        out.update({"replay_passages": len(store.passages), "replay_passage_sources": len(store.source_ids()),
                    "replay_mean_reliability": round(store.mean_reliability(), 3)})
    except ImportError:
        out["replay_passages"] = "n/a (utils.evidence_store not present)"
    return out


# ── per-run evaluation ────────────────────────────────────────────────────────
def evaluate_run(run_dir: Path, rerender: bool = False, retrieval: bool = False) -> dict:
    capture = _load(run_dir / "capture.json") or {}
    status = _load(run_dir / "status_final.json") or {}
    result = status.get("result") or {}
    report = str(result.get("report") or "")
    charts = result.get("charts") or []
    sources = result.get("sources") or []
    stages = capture.get("stages") or {}
    log_text = (run_dir / "server.log").read_text(encoding="utf-8", errors="replace") if (run_dir / "server.log").exists() else ""
    tier = str(capture.get("reportTier") or result.get("reportTier") or "?")
    question = str(capture.get("question") or "")

    fetch = (stages.get("sources_after_article_fetch") or {}).get("data") or {}
    writer = (stages.get("writer_source_evidence") or {}).get("data") or {}
    blocks = {str(b.get("sourceId")) for b in writer.get("sourceBlocks") or []}
    cited = _cited_ids(report)
    manifest_ids = {str(s.get("id")) for s in sources}
    headings = [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(report)]
    explained, referenced = charts_explained(report, charts)
    figure_refs = {int(n) for n in re.findall(r"(?i)\b(?:figure|exhibit)\s+(\d+)\b", report)}

    metrics: dict = {
        "run": f"{run_dir.parent.name}/{run_dir.name}",
        "tier": tier,
        "sources_fetched": fetch.get("publisherPagesVerified"),
        "sources_in_appendix": len(sources),
        "sources_cited": len(cited),
        "pct_evidence_cited": round(100 * len(cited & blocks) / len(blocks), 1) if blocks else None,
        "report_words": len(WORD_RE.findall(CITE_RE.sub(" ", MARKER_RE.sub(" ", report)))),
        "h2_sections": sum(1 for lvl, _ in headings if lvl == 2),
        "subsections": sum(1 for lvl, _ in headings if lvl > 2),
        "charts": len(charts),
        "pct_charts_explained": round(100 * explained / len(charts), 1) if charts else None,
        "pct_charts_figure_ref": round(100 * referenced / len(charts), 1) if charts else None,
        "numbers_removed": numbers_removed(capture, log_text),
        "husk_callouts": len(husk_blocks(report)),
        "sentence_fragments": len(sentence_fragments(report)),
        "empty_headings": len(empty_headings(report)),
        "duplicate_headings": len(duplicate_headings(headings)),
        "misplaced_sections": len(misplaced_sections(headings)),
        "dangling_refs": (
            sum(1 for n in re.findall(r"\[CHART_(\d+)\]", report) if int(n) > len(charts))
            + len(cited - manifest_ids)
            + sum(1 for n in figure_refs if n > len(charts))
        ),
        "_detail": {
            "husks": husk_blocks(report)[:10],
            "fragments": sentence_fragments(report)[:10],
            "empty_headings": empty_headings(report)[:10],
            "duplicate_headings": duplicate_headings(headings)[:10],
            "misplaced_sections": misplaced_sections(headings),
        },
    }
    pdf_bytes = (run_dir / "report.pdf").read_bytes() if (run_dir / "report.pdf").exists() else None
    render_error = None
    if rerender and report:
        pdf_bytes, render_error = asyncio.run(_rerender(result, question, tier))
        if pdf_bytes:
            (run_dir / "report.rerendered.pdf").write_bytes(pdf_bytes)
    pm = pdf_metrics(pdf_bytes)
    metrics["pages"] = pm.get("pages")
    metrics["pdf_words"] = pm.get("pdf_words")
    metrics["render_errors"] = (1 if render_error else 0) + len(pm.get("raw_markers_in_pdf") or [])
    metrics["_detail"]["render"] = {"error": render_error, "raw_markers": pm.get("raw_markers_in_pdf"), "sparse_pages": pm.get("sparse_pages")}
    if retrieval and capture:
        metrics.update(retrieval_replay(capture))
    return metrics


ORDER = ["tier", "pages", "pdf_words", "report_words", "h2_sections", "subsections", "sources_fetched",
         "sources_in_appendix", "sources_cited", "pct_evidence_cited", "charts", "pct_charts_explained",
         "pct_charts_figure_ref", "numbers_removed", "husk_callouts", "sentence_fragments", "empty_headings",
         "duplicate_headings", "misplaced_sections", "dangling_refs", "render_errors"]


def print_table(rows: list[dict], baseline: dict | None = None) -> None:
    keys = ORDER + sorted({k for r in rows for k in r if k.startswith("replay_")})
    names = [r["run"] for r in rows]
    width = max(24, *(len(k) for k in keys))
    colw = max(18, *(len(n) for n in names)) if names else 18
    print("metric".ljust(width), *[n.rjust(colw) for n in names])
    for key in keys:
        cells = []
        for r in rows:
            value = r.get(key)
            if isinstance(value, list):
                unique = list(dict.fromkeys(str(v).split(":")[0] for v in value))
                text = f"{len(value)}: {', '.join(unique)[:40]}" if value else "0"
            else:
                text = "—" if value is None else str(value)
            if baseline and key != "tier":
                base = (baseline.get(r["run"]) or baseline.get(r.get("tier", "")) or {}).get(key)
                if base is not None and base != value:
                    text = f"{base} → {text}"
            cells.append(text.rjust(colw))
        print(key.ljust(width), *cells)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="*", help="run directories (default: every run under diagnostics/eval_runs)")
    parser.add_argument("--rerender", action="store_true", help="rebuild PDFs from saved results with the current renderer")
    parser.add_argument("--retrieval", action="store_true", help="replay current retrieval scoring on captured search results")
    parser.add_argument("--baseline", help="JSON baseline to show before → after")
    parser.add_argument("--save-baseline", help="write metrics to this JSON file")
    parser.add_argument("--details", action="store_true", help="print the offending headings/husks/fragments")
    args = parser.parse_args(argv)

    run_dirs = [Path(p) for p in args.runs] or sorted(p.parent for p in DEFAULT_RUNS.glob("*/*/capture.json"))
    if not run_dirs:
        print(f"No runs found under {DEFAULT_RUNS}", file=sys.stderr)
        return 2
    rows = [evaluate_run(d, rerender=args.rerender, retrieval=args.retrieval) for d in run_dirs]
    baseline = None
    if args.baseline:
        raw = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        baseline = {**{r["run"]: r for r in raw.get("runs", [])}, **{r["tier"]: r for r in raw.get("runs", [])}}
    print_table(rows, baseline)
    if args.details:
        for r in rows:
            print(f"\n[{r['run']}]")
            for k, v in r["_detail"].items():
                print(f"  {k}: {v}")
    if args.save_baseline:
        payload = {"note": "Metrics only; artefacts stay local under diagnostics/eval_runs.",
                   "runs": [{k: v for k, v in r.items() if k != "_detail"} for r in rows]}
        Path(args.save_baseline).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save_baseline).write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\nSaved baseline: {args.save_baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
