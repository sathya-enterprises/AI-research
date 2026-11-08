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


_PERIOD_IN_HEADING_RE = re.compile(r"(?i)\b(?:Q[1-4]\s*(?:FY|CY)?\s*'?\d{2,4}|FY\s*'?\d{2,4}|(?:19|20)\d{2})\b")


def duplicate_headings(headings: list[tuple[int, str]]) -> list[tuple[str, str]]:
    """Pairs of headings that cover the same topic (token Jaccard >= 0.5, >=2 shared tokens).

    Not duplicates (C2): an H3 and the H2 it sits under (a parent naming its parts); an
    entity-scoped heading ("Bank: Drivers, Risks") and an unscoped heading of another level
    ("Macro Drivers, Risks"); headings naming different periods; an appendix heading and a
    body heading (Appendix B reproduces tables the body discusses). Appendix headings are
    still compared with each other.
    """
    pairs = []
    norm = [(lvl, h, _norm_heading(h)) for lvl, h in headings]
    parent: list[int] = []
    zone: list[str] = []
    current_h2, in_appendix = -1, False
    for index, (lvl, h, _) in enumerate(norm):
        if lvl == 2:
            current_h2 = index
            in_appendix = bool(re.match(r"(?i)\s*appendix\b", h))
        parent.append(current_h2 if lvl > 2 else -1)
        zone.append("appendix" if in_appendix else "body")

    def prefix(heading: str) -> str:
        match = re.match(r"^\s*([^:]{2,40}):\s+\S", heading)
        return match.group(1).strip().casefold() if match else ""

    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            a, b = norm[i][2], norm[j][2]
            if not a or not b:
                continue
            pa, pb = prefix(norm[i][1]), prefix(norm[j][1])
            if pa and pb and pa != pb:
                continue  # "Bank: Performance" vs "IT: Performance" are different entity sections
            if parent[j] == i or parent[i] == j:
                continue
            if bool(pa) != bool(pb) and norm[i][0] != norm[j][0]:
                continue
            if zone[i] != zone[j]:
                continue
            periods_i = {p.casefold().replace(" ", "") for p in _PERIOD_IN_HEADING_RE.findall(norm[i][1])}
            periods_j = {p.casefold().replace(" ", "") for p in _PERIOD_IN_HEADING_RE.findall(norm[j][1])}
            if periods_i and periods_j and periods_i != periods_j:
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


def _figure_paragraph_in_report(report: str, index: int) -> str:
    """The paragraph that interprets chart ``index``: the one saying "Figure N", else the one after its marker."""
    paragraphs = re.split(r"\n\s*\n", report or "")
    for para in paragraphs:
        if re.search(rf"\bFigure\s+{index}\b", para) and not para.lstrip().startswith("|"):
            return para
    for i, para in enumerate(paragraphs):
        if f"[CHART_{index}]" in para:
            rest = para.replace(f"[CHART_{index}]", "").strip()
            return rest or (paragraphs[i + 1] if i + 1 < len(paragraphs) else "")
    return ""


def chart_title_mismatches(report: str, charts: list) -> list[str]:
    """Charts whose title names entities the chart does not plot, or whose interpreting
    paragraph is about other entities/values than the chart's own data."""
    from utils.chart_titles import text_chart_mismatch, title_data_mismatch

    out = []
    for i, chart in enumerate(charts or [], 1):
        if not isinstance(chart, dict) or f"[CHART_{i}]" not in (report or ""):
            continue
        reason = title_data_mismatch(chart)
        if not reason:
            paragraph = _figure_paragraph_in_report(report, i)
            reason = text_chart_mismatch(paragraph, chart) if paragraph else ""
        if reason:
            out.append(f"Figure {i}: {reason} ({str(chart.get('title') or '')[:70]})")
    return out


def figure_numbering_issues(report: str, charts: list) -> list[str]:
    """Figures must be numbered 1..n in the order they appear; every 'Figure N' must exist; no number twice."""
    issues = []
    markers = [int(n) for n in re.findall(r"\[CHART_(\d+)\]", report or "")]
    first = list(dict.fromkeys(markers))
    if first != sorted(first):
        issues.append(f"figures out of order: {first[:12]}")
    numbered = []
    for i, chart in enumerate(charts or [], 1):
        match = re.match(r"\s*Figure\s+(\d+)\s*:", str((chart or {}).get("title") or ""))
        if match:
            numbered.append(int(match.group(1)))
            if int(match.group(1)) != i:
                issues.append(f"chart {i} titled Figure {match.group(1)}")
    duplicates = [n for n, c in Counter(numbered).items() if c > 1]
    issues += [f"two charts titled Figure {n}" for n in duplicates]
    captions = Counter(int(n) for n in re.findall(r"(?m)(?:^|\|\s*|\*\*)Figure\s+(\d+)\s*:", report or ""))
    # Chart titles are rendered from the chart list, never written in the body: a "Figure N:" caption in
    # the text is the writer labelling its own table, which duplicates (or invents) a figure number.
    issues += [f"text caption 'Figure {n}:' duplicates a figure number" for n in captions]
    present = set(markers)
    for n in sorted({int(n) for n in re.findall(r"\bFigure\s+(\d+)\b", report or "")}):
        if n not in present:
            issues.append(f"text refers to Figure {n}, which is not in the report")
    return issues


def repeated_chart_titles(charts: list) -> list[str]:
    titles = [re.sub(r"^\s*Figure\s+\d+\s*:\s*", "", str((c or {}).get("title") or "")).casefold() for c in charts or []]
    return [t for t, n in Counter(titles).items() if t and n > 1]


def malformed_after_strip(report: str) -> list[str]:
    """Sentences / cells left empty or malformed when inline citations are stripped (renderer rule)."""
    from utils.citation_display import MARKER_RE, only_citations, strip_citation_markers

    bad = []
    for block in re.split(r"\n\s*\n|\n(?=\s*[-*|>])", report or ""):
        if not MARKER_RE.search(block) or only_citations(block):
            continue  # untouched, or a citation-only line the renderer drops
        out = strip_citation_markers(block)
        if re.search(r"\(\s*\)|\s[.,;:](?:\s|$)|[,;]\s*[.]|\.\s*\.|^\s*[-*]\s*$|\|\s*\|", out):
            bad.append(out.strip()[:160])
    return bad


def takeaway_figure_refs(report: str) -> list[str]:
    return [ln[:160] for ln in re.findall(r"(?m)^>\s*\[!TAKEAWAYS\].*$", report or "")
            if re.search(r"\b(?:Figure|Fig\.)\s*\d+", ln)]


def layout_metrics(pdf_bytes: bytes | None, report: str, prefix: str = "") -> tuple[dict, dict]:
    """Stage D layout checks from the PDF geometry (PyMuPDF) and the report text."""
    from utils.pdf_layout_metrics import distinct_deep_dive_sets, pdf_layout_metrics

    distinct, deep_dives = distinct_deep_dive_sets(report)
    metrics = {f"{prefix}distinct_subsection_sets": f"{distinct}/{deep_dives}" if deep_dives else None,
               f"{prefix}malformed_after_citation_strip": len(malformed_after_strip(report)),
               f"{prefix}takeaway_figure_refs": len(takeaway_figure_refs(report))}
    if not pdf_bytes:
        return metrics, {}
    m = pdf_layout_metrics(pdf_bytes)
    metrics.update({
        f"{prefix}source_lists": m["source_lists"],
        f"{prefix}prose_only_streak": m["prose_only_streak"],
        f"{prefix}pct_body_pages_non_prose": m["pct_body_pages_non_prose"],
        f"{prefix}stranded_headings": len(m["stranded_headings"]),
        f"{prefix}heading_then_heading": len(m["heading_then_heading"]),
        f"{prefix}appendix_pages": m["appendix_pages"],
        f"{prefix}near_empty_pages": len(m["near_empty_pages"]),
        f"{prefix}table_overflow_lines": len(m["table_overflow"]),
        f"{prefix}prompt_text_in_headings": len(m["prompt_leak_headings"]),
        f"{prefix}code_fences_in_pdf": len(m["code_fences"]),
        # Below ~9pt means Chromium shrank the whole document to fit something too wide.
        f"{prefix}body_font_pt": m["body_font_size"],
        f"{prefix}citation_markers_in_pdf": m["citation_markers"],
        f"{prefix}malformed_lines_in_pdf": len(m["malformed_pdf_lines"]),
        f"{prefix}sections_without_sources_line": max(0, m["sections"] - m["sections_with_sources_line"]),
    })
    details = {k: m[k] for k in ("source_list_headings", "stranded_headings", "heading_then_heading", "near_empty_pages",
                                 "table_overflow", "prompt_leak_headings", "code_fences", "citation_marker_pages", "malformed_pdf_lines")}
    details["malformed_after_citation_strip"] = malformed_after_strip(report)[:10]
    details["takeaway_figure_refs"] = takeaway_figure_refs(report)
    return metrics, details


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


def _removed_not_in_live_prompt(capture: dict, removed: list[dict]) -> int:
    from utils.number_policy import normalize, number_set

    prompts: dict[str, set[str]] = {}
    for call in capture.get("llmCalls") or []:
        sid = str(call.get("sectionId") or "").split("#")[0]
        prompts.setdefault(sid, set()).update(number_set([str(call.get("llmPrompt") or "")]))
    return sum(1 for r in removed
               if normalize(str(r.get("number") or "")).lstrip("-") not in prompts.get(str(r.get("section") or ""), set()))


def _replay_auditor(store, manifest_rows: list, enriched_rows: list):
    """report.py's source-evidence check against the captured fetched pages + passages (stricter than live:
    the live check also sees pages fetched after the capture point)."""
    from utils.evidence_check import store_auditor
    return store_auditor(store, manifest_rows, enriched_rows)


def _captured_tiers(stages: dict, store):
    """The entity tiers the live run used (replaying recomputed tiers would change the outline prompt)."""
    summary = ((stages.get("entity_tiers") or {}).get("data")) or {}
    if not summary.get("entities"):
        return None
    from utils.entity_tiers import tiers_from_summary
    return tiers_from_summary(store, summary)


def restore_fetched_dataset(store, stages: dict, manifest: list) -> bool:
    """Captures from before Stage F lost the fetched quarterly dataset on the way into the evidence
    store (the registry renamed it). A live run now keeps it, so the replay rebuilds it from the
    captured dataset rows (exactly the rows the live run fetched)."""
    qrows = ((stages.get("calculated_sector_quarterly_dataset") or {}).get("data") or {}).get("rows") or []
    if not qrows or any(p.kind == "calculated" and "Sector | Q" in p.text for p in store.passages):
        return False
    from utils.market_data import format_sector_quarterly_returns_as_source
    source = format_sector_quarterly_returns_as_source(qrows)
    sid = next((str(m.get("id")).upper() for m in manifest if isinstance(m, dict)
                and "by calendar quarter" in str(m.get("title") or "").casefold()), "")
    if not source or not sid:
        return False
    for stale in [p for p in store.passages if p.source_id == sid]:
        store.passages.remove(stale)
        store._by_id.pop(stale.passage_id, None)
    store.add_source(sid, source)
    store.finalize()
    return True


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
    restore_fetched_dataset(store, stages, manifest)
    gateway = ReplayGateway(capture)
    # Reproduce the live outline: the subsection planner is deterministic, so it runs in the replay
    # exactly when it ran live (recorded in the outline decisions); older captures predate it.
    _decisions = (((stages.get("pipeline_v2") or {}).get("data") or {}).get("outline") or {}).get("decisions") or []
    os.environ["GG_V2_SUBSECTION_PLANNER"] = "1" if any(str(d).startswith("Deep-dive subsections chosen") for d in _decisions) else "0"
    manifest_rows = [m for m in manifest if isinstance(m, dict)]
    enriched_rows = [r for r in (((stages.get("sources_after_article_fetch") or {}).get("data") or {}).get("sources") or [])
                     if isinstance(r, dict)]
    question = str(capture.get("question") or "")
    # Captures redact long page text; live, the verifier sees the full fetched page. Offline the
    # source text is the captured page text plus the evidence-store passages (as store_auditor does).
    _texts: dict[str, list[str]] = {}
    for _p in store.passages:
        _texts.setdefault(_p.source_id, []).append(str(_p.text or ""))
    _by_url = {str(r.get("url") or "").rstrip("/"): r for r in enriched_rows}
    verifier_rows = []
    for _m in manifest_rows:
        _sid = str(_m.get("id") or "").upper()
        _fetched = _by_url.get(str(_m.get("url") or "").rstrip("/"), {})
        _body = "\n\n".join([str(_fetched.get("fullContent") or ""), *_texts.get(_sid, [])]).strip()
        if _body:
            verifier_rows.append({**_fetched, **_m, "id": _sid, "fullContent": _body, "pageContent": _body,
                                  "snippet": str(_fetched.get("snippet") or _body[:600]), "pageFetchStatus": "ok"})

    def chart_verifier(specs, report_text=""):
        """Offline twin of report.py's _v2_chart_preverify (same audit functions, captured evidence)."""
        from routes import report as R
        R._register_v2_chart_evidence(specs, store)
        public = R._merge_source_evidence_statuses(manifest_rows, R._attach_audit_source_evidence(manifest_rows, verifier_rows, report_text))
        res = R.audit_chart_evidence_consistency(report_text, specs, public, question=question,
                                                 require_publisher_verification=True)
        reasons = [""] * len(specs)
        for failure in list(res.get("chartEvidenceFailures") or []) + list(res.get("chartTableVerificationConflicts") or []):
            index = int(failure.get("chartIndex") or 0) - 1
            if 0 <= index < len(specs) and not reasons[index]:
                reasons[index] = str(failure.get("reason") or "failed")[:200]
        return reasons

    result = asyncio.run(run_pipeline_v2(runtime=None, question=question,
                                         tier=str(capture.get("reportTier") or "detailed"), store=store,
                                         manifest=manifest_rows,
                                         today=str(capture.get("startedAt") or "")[:10], gateway=gateway,
                                         chart_verifier=chart_verifier, tiers=_captured_tiers(stages, store),
                                         evidence_auditor=_replay_auditor(store, manifest_rows, enriched_rows)))
    out = json.loads(result["raw"])
    # The PDF route resolves [S#] citations against the source manifest.
    # The PDF route resolves [S#] citations and re-checks charts against the manifest;
    # attach each chart's source rows the way report.py registers them live.
    sources = [dict(m) for m in manifest if isinstance(m, dict)]
    by_id = {str(m.get("id") or "").upper(): m for m in sources}
    for chart in out.get("charts") or []:
        for pid in chart.get("evidencePassages") or []:
            passage = store.get(pid)
            if passage is None or passage.source_id not in by_id:
                continue
            rows = "\n".join(ln.strip() for ln in passage.text.splitlines() if " | " in ln)
            entry = by_id[passage.source_id]
            entry["chartEvidenceExcerpt"] = (str(entry.get("chartEvidenceExcerpt") or "") + "\n" + rows).strip()[-16000:]
            if passage.kind == "table":
                entry["publisher_table_chart_evidence_verified"] = True
    out.setdefault("sources", sources)
    # Replayed V2 output has not been through report.py's publication checks; render it
    # as a labelled draft, like a live run whose checks found open issues.
    out.setdefault("publicationOutcome", "draft")
    out.setdefault("reportTier", str(capture.get("reportTier") or "detailed"))
    (run_dir / "v2_replay_result.json").write_text(json.dumps({"result": out, "meta": result["meta"]}, ensure_ascii=False, indent=1), encoding="utf-8")
    # Publication report for the replay: the number gates are the ones V2 ran here (report.py's
    # source-evidence check via the store auditor); report.py's chart/structure gates did not run.
    from utils.publication_report import build_publication_report, render_markdown
    v2meta = result["meta"]
    number_ok = not (v2meta.get("numberPolicy") or {}).get("unresolvedTableValues") and not (v2meta.get("evidenceCheck") or {}).get("stillFlagged")
    pub = build_publication_report(
        outcome={"outcome": "would publish (number gates)" if number_ok else "draft (number gates)"},
        audits={"sourceEvidenceAudit": {"mismatchedCitationGroups": (v2meta.get("evidenceCheck") or {}).get("stillFlagged") or []}},
        v2_meta=v2meta, question=str(capture.get("question") or ""), tier=str(capture.get("reportTier") or ""),
        report_id=str(capture.get("reportId") or run_dir.name))
    for gate in pub["gates"]:
        if gate["gate"] in {"Chart evidence", "Report structure", "Consistency", "Scope coverage"}:
            gate["status"], gate["note"] = "NOT RUN OFFLINE", "report.py runs this gate on the live response only"
    (run_dir / "v2_replay_publication_report.json").write_text(json.dumps(pub, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "v2_replay_publication_report.md").write_text(render_markdown(pub), encoding="utf-8")
    report = out.get("report") or ""
    headings = [(len(m.group(1)), m.group(2)) for m in HEADING_RE.finditer(report)]
    explained, referenced = charts_explained(report, out.get("charts") or [])
    charts = out.get("charts") or []
    metrics = {
        "v2replay_calls_replayed": gateway.stats.calls, "v2replay_missing_calls": len(gateway.missing),
        "v2replay_prompts_changed": len(gateway.prompt_changed),
        "v2replay_words": len(WORD_RE.findall(CITE_RE.sub(" ", MARKER_RE.sub(" ", report)))),
        "v2replay_charts": len(charts),
        "v2replay_charts_from_datasets": (result["meta"].get("chartFunnel") or {}).get("usedFromFetchedDatasets", 0),
        "v2replay_charts_dropped_after_assembly": len(result["meta"].get("charts", {}).get("droppedAfterAssembly") or []),
        "v2replay_sections_ok": sum(1 for x in result["meta"].get("sections") or [] if x.get("ok")),
        "v2replay_pct_charts_explained": round(100 * explained / len(charts), 1) if charts else None,
        "v2replay_unsupported_numbers": result["meta"].get("unsupportedNumbers"),
        "v2replay_husk_callouts": len(husk_blocks(report)),
        "v2replay_duplicate_headings": len(duplicate_headings(headings)),
        "v2replay_chart_title_mismatch": len(chart_title_mismatches(report, charts)),
        "v2replay_figure_numbering_issues": len(figure_numbering_issues(report, charts)),
        "v2replay_repeated_chart_titles": len(repeated_chart_titles(charts)),
        "v2replay_charts_dropped_text_mismatch": len(result["meta"].get("chartTextMismatches") or []),
        "v2replay_numbers_calculated_labelled": len((result["meta"].get("numberPolicy") or {}).get("calculatedLabelled") or []),
        "v2replay_sentences_removed_unsupported": len((result["meta"].get("numberPolicy") or {}).get("sentencesRemoved") or []),
        # A replay re-uses the live writer's text: a number the live writer was given (it is in the
        # live prompt) but the current pipeline no longer gives it is a replay artefact, not a
        # writer error. Only the others would also be removed in a live run with the current code.
        "v2replay_sentences_removed_not_in_live_prompt": _removed_not_in_live_prompt(
            capture, (result["meta"].get("numberPolicy") or {}).get("sentencesRemoved") or []),
        "v2replay_unresolved_numbers": len((result["meta"].get("numberPolicy") or {}).get("unresolvedTableValues") or []),
        "v2replay_evidence_check_removed": len((result["meta"].get("evidenceCheck") or {}).get("removedClaims") or []),
        "v2replay_evidence_check_rewritten": len((result["meta"].get("evidenceCheck") or {}).get("rewrittenClaims") or []),
        "v2replay_evidence_check_fact_overrides": len((result["meta"].get("evidenceCheck") or {}).get("factSheetOverrides") or []),
        "v2replay_policy_numbers_rewritten": len((result["meta"].get("numberPolicy") or {}).get("numbersRewritten") or []),
        "v2replay_evidence_check_still_flagged": len((result["meta"].get("evidenceCheck") or {}).get("stillFlagged") or []),
        "v2replay_repeated_sentences_removed": (result["meta"].get("editorial") or {}).get("repeatedSentencesRemoved"),
        "v2replay_sections_merged": len((result["meta"].get("editorial") or {}).get("mergedSections") or []),
        "v2replay_contradictions_flagged": len((result["meta"].get("editorial") or {}).get("contradictions") or []),
        "v2replay_sentence_fragments": len(sentence_fragments(report)),
    }
    details = {"chart_title_mismatch": chart_title_mismatches(report, charts),
               "figure_numbering": figure_numbering_issues(report, charts),
               "duplicate_headings": duplicate_headings(headings)[:12]}
    (run_dir / "v2_replay_details.json").write_text(json.dumps(details, ensure_ascii=False, indent=1), encoding="utf-8")
    if rerender:
        pdf_bytes, error = asyncio.run(_rerender(out, str(capture.get("question") or ""), str(capture.get("reportTier") or "detailed")))
        if pdf_bytes:
            (run_dir / "v2_replay.pdf").write_bytes(pdf_bytes)
        metrics["v2replay_pages"] = pdf_metrics(pdf_bytes).get("pages") if pdf_bytes else f"render error: {error}"
        layout, layout_details = layout_metrics(pdf_bytes, report, prefix="v2replay_")
        metrics.update(layout)
        (run_dir / "v2_replay_layout.json").write_text(json.dumps(layout_details, ensure_ascii=False, indent=1), encoding="utf-8")
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
        "chart_title_mismatch": len(chart_title_mismatches(report, charts)),
        "figure_numbering_issues": len(figure_numbering_issues(report, charts)),
        "repeated_chart_titles": len(repeated_chart_titles(charts)),
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
            "chart_title_mismatch": chart_title_mismatches(report, charts),
            "figure_numbering": figure_numbering_issues(report, charts),
            "repeated_chart_titles": repeated_chart_titles(charts),
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
    layout, layout_details = layout_metrics(pdf_bytes, report)
    metrics.update(layout)
    metrics["_detail"]["layout"] = layout_details
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
         "duplicate_headings", "misplaced_sections", "chart_title_mismatch", "figure_numbering_issues",
         "repeated_chart_titles", "source_lists", "prose_only_streak", "pct_body_pages_non_prose",
         "distinct_subsection_sets", "stranded_headings", "heading_then_heading", "appendix_pages", "near_empty_pages",
         "table_overflow_lines", "prompt_text_in_headings", "code_fences_in_pdf", "citation_markers_in_pdf",
         "sections_without_sources_line", "malformed_after_citation_strip", "takeaway_figure_refs",
         "dangling_refs", "render_errors",
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
