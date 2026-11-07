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


def refetch_replay(capture: dict) -> dict:
    """Re-fetch the pages this run queued for enrichment with the *current* fetcher
    (+ browser fallback). Network only — no LLM/search API credits."""
    from routes import report as R
    from utils.browser_fallback import apply_browser_fallback
    from utils.source_extractor import fetch_source_document

    stages = capture.get("stages") or {}
    question = str(capture.get("question") or "")
    tier = str(capture.get("reportTier") or "detailed")
    queued = [r for r in (((stages.get("sources_queued_for_enrichment") or {}).get("data") or {}).get("sources") or [])
              if isinstance(r, dict) and str(r.get("url") or "").startswith("http")]
    # "Before" = the live run's fetch status for exactly these queued URLs (the
    # stage-level counts also include crawl-added pages, which are not re-run).
    fetched_rows = (((stages.get("sources_after_article_fetch") or {}).get("data") or {}).get("sources") or [])
    status_by_url = {str(r.get("url")): str(r.get("pageFetchStatus") or "unknown") for r in fetched_rows if isinstance(r, dict)}
    before = Counter(status_by_url.get(str(r.get("url")), "dropped_before_fetch_record") for r in queued)
    max_chars = 20000 if tier == "in_depth" else 15000

    async def run() -> list[dict]:
        sem = asyncio.Semaphore(8)

        async def one(row: dict) -> dict:
            async with sem:
                res = await fetch_source_document(row["url"], max_chars=max_chars, include_script_data=True)
            text = str(res.get("text") or "")
            out = dict(row)
            if res.get("status") == "ok" and R._page_text_matches_source(text, out, question, None):
                out.update(pageFetchStatus="ok", pageContent=text)
            elif res.get("status") == "ok":
                out["pageFetchStatus"] = "rejected_by_relevance_gate" if text.strip() else "empty_extraction"
            else:
                out["pageFetchStatus"] = str(res.get("status"))
            return out

        rows = await asyncio.gather(*(one(r) for r in queued))
        stats = await apply_browser_fallback(rows, report_tier=tier, max_chars=max_chars,
                                             matches_source=lambda t, r: R._page_text_matches_source(t, r, question, None))
        return rows, stats

    rows, browser = asyncio.run(run())
    after = Counter(str(r.get("pageFetchStatus")) for r in rows)
    return {
        "refetch_queued": len(queued),
        "refetch_ok_before": int(before.get("ok", 0)),
        "refetch_ok_after": after.get("ok", 0),
        "refetch_empty_before": int(before.get("empty_extraction", 0)),
        "refetch_empty_after": after.get("empty_extraction", 0),
        "refetch_browser_recovered": browser.get("recovered", 0),
    }


_LLM_LOG_RE = re.compile(r"HTTP Request: POST https://(?:generativelanguage\.googleapis\.com|api\.groq\.com|openrouter\.ai)\S*")


def llm_metrics(capture: dict, log_text: str) -> dict:
    """LLM calls and tokens. V2 records every call in the capture; for the old path
    the server log's provider HTTP requests are counted (tokens unavailable)."""
    calls = [c for c in (capture.get("llmCalls") or []) if isinstance(c, dict)]
    log_calls = len(_LLM_LOG_RE.findall(log_text or "")) or None
    if calls:
        return {"llm_calls": len(calls), "llm_calls_all_providers": log_calls,
                "llm_tokens_in": sum(int(c.get("tokensIn") or 0) for c in calls),
                "llm_tokens_out": sum(int(c.get("tokensOut") or 0) for c in calls),
                "llm_failed_calls": sum(1 for c in calls if not c.get("ok"))}
    return {"llm_calls": None, "llm_calls_all_providers": log_calls, "llm_tokens_in": None, "llm_tokens_out": None}


def v2_metrics(capture: dict) -> dict:
    stage = ((capture.get("stages") or {}).get("pipeline_v2") or {}).get("data") or {}
    if not stage:
        return {"pipeline": "v1"}
    if stage.get("fallback"):
        return {"pipeline": "v1 (v2 failed: " + str(stage.get("error") or "")[:60] + ")"}
    cit = stage.get("citations") or {}
    outline = stage.get("outline") or {}
    coverage = outline.get("entityCoverage") or []
    return {
        "pipeline": "v2",
        "v2_sections_ok": f"{sum(1 for s in stage.get('sections') or [] if s.get('ok'))}/{len(stage.get('sections') or [])}",
        "v2_passages_provided": cit.get("passagesProvided"),
        "v2_passages_cited": cit.get("passagesCited"),
        "v2_pct_passages_cited": round(100 * (cit.get("passagesCited") or 0) / cit["passagesProvided"], 1) if cit.get("passagesProvided") else None,
        "v2_unsupported_numbers": stage.get("unsupportedNumbers"),
        "v2_invalid_citations": sum(int(s.get("invalidCitations") or 0) for s in stage.get("sections") or []),
        "v2_entities_covered": f"{sum(1 for r in coverage if r.get('status') == 'covered')}/{len(coverage)}" if coverage else None,
        "v2_scope_decisions": len(outline.get("decisions") or []),
        "v2_estimated_pages": stage.get("estimatedPages"),
        "v2_evidence_supported_pages": stage.get("evidenceSupportedPages"),
    }


def replay_v2(capture: dict, run_dir: Path, rerender: bool) -> dict:
    """Re-run V2 assembly offline from the captured passages and LLM responses."""
    stages = capture.get("stages") or {}
    passages = (((stages.get("evidence_store") or {}).get("data") or {}).get("passages")) or {}
    if not capture.get("llmCalls") or not passages:
        return {"v2replay": "n/a (capture has no V2 llmCalls / passages)"}
    from utils.evidence_store import store_from_passages
    from utils.llm_replay import ReplayGateway
    from utils.report_pipeline_v2 import run_pipeline_v2

    store = store_from_passages(passages)
    manifest = (((stages.get("canonical_source_manifest") or {}).get("data") or {}).get("sources")) or []
    gateway = ReplayGateway(capture)
    result = asyncio.run(run_pipeline_v2(runtime=None, question=str(capture.get("question") or ""),
                                         tier=str(capture.get("reportTier") or "detailed"), store=store,
                                         manifest=[m for m in manifest if isinstance(m, dict)],
                                         today=str(capture.get("startedAt") or "")[:10], gateway=gateway))
    out = json.loads(result["raw"])
    (run_dir / "v2_replay_result.json").write_text(json.dumps({"result": out, "meta": result["meta"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    report = out.get("report") or ""
    headings = [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(report)]
    explained, referenced = charts_explained(report, out.get("charts") or [])
    charts = out.get("charts") or []
    metrics = {
        "v2replay_calls_replayed": gateway.stats.calls, "v2replay_missing_calls": len(gateway.missing),
        "v2replay_prompts_changed": len(gateway.prompt_changed),
        "v2replay_words": len(WORD_RE.findall(CITE_RE.sub(" ", MARKER_RE.sub(" ", report)))),
        "v2replay_charts": len(charts),
        "v2replay_pct_charts_explained": round(100 * explained / len(charts), 1) if charts else None,
        "v2replay_unsupported_numbers": result["meta"].get("unsupportedNumbers"),
        "v2replay_husk_callouts": len(husk_blocks(report)),
        "v2replay_duplicate_headings": len(duplicate_headings(headings)),
    }
    if rerender:
        pdf_bytes, error = asyncio.run(_rerender(out, str(capture.get("question") or ""), str(capture.get("reportTier") or "detailed")))
        if pdf_bytes:
            (run_dir / "v2_replay.pdf").write_bytes(pdf_bytes)
        metrics["v2replay_pages"] = pdf_metrics(pdf_bytes).get("pages") if pdf_bytes else f"render error: {error}"
    return metrics


# ── per-run evaluation ────────────────────────────────────────────────────────
def evaluate_run(run_dir: Path, rerender: bool = False, retrieval: bool = False, refetch: bool = False,
                 replay: bool = False) -> dict:
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
    metrics.update(llm_metrics(capture, log_text))
    metrics.update(v2_metrics(capture))
    elapsed = status.get("elapsedMs") or 0
    metrics["time_s"] = round(elapsed / 1000, 1) if elapsed else None
    if retrieval and capture:
        metrics.update(retrieval_replay(capture))
    if refetch and capture:
        metrics.update(refetch_replay(capture))
    if replay and capture:
        metrics.update(replay_v2(capture, run_dir, rerender))
    return metrics


ORDER = ["tier", "pages", "pdf_words", "report_words", "h2_sections", "subsections", "sources_fetched",
         "sources_in_appendix", "sources_cited", "pct_evidence_cited", "charts", "pct_charts_explained",
         "pct_charts_figure_ref", "numbers_removed", "husk_callouts", "sentence_fragments", "empty_headings",
         "duplicate_headings", "misplaced_sections", "dangling_refs", "render_errors",
         "pipeline", "llm_calls", "llm_calls_all_providers", "llm_tokens_in", "llm_tokens_out", "llm_failed_calls", "time_s",
         "v2_sections_ok", "v2_passages_provided", "v2_passages_cited", "v2_pct_passages_cited", "v2_unsupported_numbers",
         "v2_invalid_citations", "v2_entities_covered", "v2_scope_decisions", "v2_estimated_pages", "v2_evidence_supported_pages"]


def print_table(rows: list[dict], baseline: dict | None = None) -> None:
    keys = [k for k in ORDER if any(k in r for r in rows)] + sorted({k for r in rows for k in r if k.startswith(("replay_", "refetch_", "v2replay"))})
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
    parser.add_argument("--refetch", action="store_true", help="re-fetch queued pages with the current fetcher (network only, no API credits)")
    parser.add_argument("--replay-v2", action="store_true", help="re-run V2 assembly offline from captured passages and LLM responses")
    parser.add_argument("--baseline", help="JSON baseline to show before → after")
    parser.add_argument("--save-baseline", help="write metrics to this JSON file")
    parser.add_argument("--details", action="store_true", help="print the offending headings/husks/fragments")
    args = parser.parse_args(argv)

    run_dirs = [Path(p) for p in args.runs] or sorted(p.parent for p in DEFAULT_RUNS.glob("*/*/capture.json"))
    if not run_dirs:
        print(f"No runs found under {DEFAULT_RUNS}", file=sys.stderr)
        return 2
    rows = [evaluate_run(d, rerender=args.rerender, retrieval=args.retrieval, refetch=args.refetch, replay=args.replay_v2)
            for d in run_dirs]
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
