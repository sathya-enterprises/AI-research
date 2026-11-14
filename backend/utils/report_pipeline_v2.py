"""Section-by-section report pipeline (REPORT_PIPELINE_V2=1).

outline (thesis, entities, sections, evidence budgets)
  -> charts built from stored data and assigned to sections (unverifiable ones dropped)
  -> sections written in waves (own passages + running summary + other sections' goals)
  -> executive summary from the section summaries
  -> methodology and appendices generated from the run's own provenance (no LLM)
  -> assembled into the old writer's JSON schema (title/report/charts/keyStats/summary)

The old path stays the fallback: any exception, or too few usable sections,
raises PipelineV2Error and report.py falls back to the single-call writer.
Passage citations ([S12.3]) are validated per section, kept in the metadata,
and converted to source-level citations ([S12]) for the existing renderer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter

from utils.chart_titles import text_chart_mismatch, unique_titles
from utils.editorial_pass import EditedSection, STRAY_LABEL_RE, editorial_pass
from utils.figure_refs import rewrite_removed_figure_refs  # noqa: F401  (re-exported for callers)
from utils.llm_gateway import LLMGateway
from utils.number_policy import apply_number_policy
from utils.report_outline import WORDS_PER_PAGE, OutlineError, build_outline, estimate_pages
from utils.section_charts import assign_charts, build_chart_candidates
from utils.section_writer import CITE_RE, FIGURE_MARKER_RE, FIGURE_REF_RE, SUMMARY_MARKER, _number_set, unsupported_numbers, write_section

log = logging.getLogger("report_pipeline_v2")

def blank_dash_cells(markdown: str) -> str:
    """A table cell holding only a dash means "no value"; the chart checks read an em dash as
    "explicitly not verified", so such cells are left empty (Stage E false alarm)."""
    return re.sub(r"(?m)(?<=\|)[ \t]*[—–-][ \t]*(?=\|)", " ", markdown)


MAX_CHARTS = {"detailed": 10, "in_depth": 36}
CHARTS_PER_SECTION = {"detailed": 2, "in_depth": 3}
# All charts in the report (fetched-dataset + table charts): a detailed report has a 32-page cap,
# and its layout fills ~28 pages without charts, so it carries at most 6 (Stage F target >= 5).
MAX_TOTAL_CHARTS = {"detailed": 10, "in_depth": 36}  # Stage L: detailed raised from 6 (reports read text-heavy)
# Stage L: the chart count a report should reach (recorded per run; deep-dives without a chart are listed
# as data gaps - the input of the topic connectors / gap agent).
CHART_TARGET = {"detailed": 8, "in_depth": 18}
DATASET_SIDE_CHARTS = {"detailed": 2, "in_depth": 4}


class PipelineV2Error(RuntimeError):
    pass


def _apply_section_body_transform(draft, section, transform, *, store, charts: list, covered: list[str]) -> bool:
    """Apply a narrowly scoped post-write cleanup and refresh stale number diagnostics."""
    if not callable(transform) or not getattr(draft, "ok", False):
        return False
    before_body, before_summary = draft.body, draft.summary
    draft.body = str(transform(draft.body, section) or "")
    draft.summary = str(transform(draft.summary, section) or "")
    if draft.body == before_body and draft.summary == before_summary:
        return False

    from utils.section_writer import unsupported_numbers as _unsupported_numbers
    from utils.section_charts import plain_number as _plain_number

    def citation_passages(text: str) -> set[str]:
        out = set()
        for group in CITE_RE.findall(str(text or "")):
            for token in re.split(r"\s*[,;]\s*", group):
                token = token.strip().upper()
                if "." in token:
                    out.add(token)
        return out

    passage_ids = set(section.passage_ids)
    for leaf in section.leaves():
        passage_ids.update(leaf.passage_ids)
    for chart in charts:
        if chart.section_id == section.section_id:
            passage_ids.update(chart.passage_ids)
    passage_ids.update(citation_passages(draft.body))
    passage_ids.update(citation_passages(draft.summary))
    for line in covered:
        passage_ids.update(citation_passages(line))
    passage_texts = {
        pid: passage.text for pid in passage_ids
        if (passage := store.get(pid)) is not None
    }
    allowed_numbers = _number_set(passage_texts.values())
    for chart in charts:
        if chart.section_id != section.section_id:
            continue
        allowed_numbers |= _number_set(
            f"{value:g} {_plain_number(value)}"
            for _series, _label, value in chart.points
        )
    draft.cited_passages = sorted(citation_passages(draft.body))
    draft.unsupported_numbers = _unsupported_numbers(draft.body, allowed_numbers, passage_texts)
    draft.words = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", draft.body)))
    return True


def enabled() -> bool:
    # Default since Stage E: V2 writes both modes. REPORT_PIPELINE_V2=0 rolls back to the single-call writer
    # (which also remains the automatic fallback when V2 fails, and the path for file-only reports).
    return os.getenv("REPORT_PIPELINE_V2", "1").strip().lower() in {"1", "true", "yes", "on"}


EXEC_SYSTEM = """You write the executive summary of a research report from the section summaries provided.
Rules: use only facts and numbers that appear in the section summaries (copy numbers exactly); keep their passage
citations in square brackets (e.g. [S12.3]); open with the answer to the question and the thesis, then the 4-7 most
important findings in order of importance, then what it means for the reader. Analytical prose, no bullet-only text,
no headings. Do not introduce anything not in the summaries."""


def _covered_line(draft) -> str:
    return f"{draft.title}: {draft.summary}" if draft.summary else draft.title


def _renumber_figures(body: str, mapping: dict[int, int]) -> str:
    """Renumber figure markers/references; references to figures not in the report lose their figure framing."""
    referenced = {int(n) for n in FIGURE_REF_RE.findall(body)}
    body = rewrite_removed_figure_refs(body, referenced - set(mapping))
    body = FIGURE_MARKER_RE.sub(lambda m: f"[[FIGURE §{mapping[int(m.group(1))]}]]" if int(m.group(1)) in mapping else "", body)
    body = FIGURE_REF_RE.sub(lambda m: f"Figure §{mapping[int(m.group(1))]}" if int(m.group(1)) in mapping else m.group(0), body)
    return body.replace("§", "")


_TITLE_FIGURE_RE = re.compile(r"^\s*Figure\s+(\d+)\s*:\s*")


def finalize_figures(report_text: str, charts: list) -> tuple[str, list, dict]:
    """Renumber V2 figures after all later gates, in document order.

    Later gates (e.g. the requested-window chart gate) can remove charts and the
    chart registry is re-canonicalised so [CHART_k] refers to charts[k-1]. Chart
    titles carry the pipeline's "Figure N:" prefix and the prose says "Figure N";
    both are renumbered to the surviving order. References to removed figures
    become neutral wording (Stage C rewrites those sentences properly).
    """
    text = str(report_text or "")
    charts = list(charts or [])
    # Number figures in the order their markers appear in the text: reorder the chart
    # list to first-appearance order and rewrite the [CHART_k] markers to match.
    order: list[int] = []
    for n in re.findall(r"\[CHART_(\d+)\]", text):
        if 1 <= int(n) <= len(charts) and int(n) not in order:
            order.append(int(n))
    order += [i for i in range(1, len(charts) + 1) if i not in order]
    if order != list(range(1, len(charts) + 1)):
        remap = {old: new for new, old in enumerate(order, 1)}
        text = re.sub(r"\[CHART_(\d+)\]", lambda m: f"[CHART_§{remap.get(int(m.group(1)), int(m.group(1)))}]", text).replace("[CHART_§", "[CHART_")
        charts = [charts[i - 1] for i in order]
    present = {int(n) for n in re.findall(r"\[CHART_(\d+)\]", text)}
    mapping: dict[int, int] = {}
    out = []
    for index, chart in enumerate(charts, 1):
        if not isinstance(chart, dict):
            out.append(chart)
            continue
        chart = dict(chart)
        match = _TITLE_FIGURE_RE.match(str(chart.get("title") or ""))
        if match:
            rest = str(chart["title"])[match.end():]
            if index in present:
                mapping[int(match.group(1))] = index
                chart["title"] = f"Figure {index}: {rest}"
            else:
                chart["title"] = rest
        out.append(chart)
    refs = [int(n) for n in FIGURE_REF_RE.findall(text)]
    dangling = sum(1 for n in refs if n not in mapping)
    text = _renumber_figures(text, mapping)
    for chart in out:
        if isinstance(chart, dict):
            for key in ("takeaway", "rationale"):
                if chart.get(key):
                    chart[key] = _renumber_figures(str(chart[key]), mapping)
    return text, out, {"figures": len(mapping), "danglingRefsNeutralized": dangling,
                       "mapping": {str(k): v for k, v in mapping.items()}, "reordered": order != sorted(order)}


def _apply_section_layout(edited, *, store, outline, analysis, good, today: str, asks=None, window_start=None,
                          charted: set[str] | None = None) -> dict:
    """Evidence-built blocks per section (utils/section_layout.py); returns stats and the scorecard section."""
    from utils.number_policy import apply_number_policy
    from utils.section_charts import _is_qtd, qtd_start_month
    from utils.section_layout import (entity_columns, entity_table, how_to_read, move_watch_items,
                                      place_inline_tables, stat_strip, tables_in, takeaways)
    from utils.subsection_planner import _set_key

    start = qtd_start_month(today)
    qtd = (lambda text: _is_qtd(text, start)) if start else None
    tables = tables_in(store, window_start)  # Stage I: in-window, dated tables only
    by_id = {t.passage_id: t for t in tables}
    summaries = {d.section_id: d.summary for d in good}
    sections = {s.section_id: s for s in analysis}
    stats = {"statStrips": 0, "takeaways": 0, "watch": 0, "inlineTables": 0, "comparisons": 0,
             # Stage N: a table already drawn as a figure is not printed again beside it or in the appendix
             "placedTables": set(charted or ()), "scorecard": "", "scorecardRows": 0, "headingsRemoved": 0}
    for e in edited:
        sec = sections.get(e.section_id)
        cited = {t.strip().upper() for g in CITE_RE.findall(e.body) for t in re.split(r"\s*[,;]\s*", g)}
        top, body, watch = [], e.body, ""
        if e.role == "deep_dive" and e.entity:
            strip = stat_strip(tables, e.entity, cited, qtd=qtd)
            if strip:
                top.append(strip)
                stats["statStrips"] += 1
            summary = summaries.get(e.section_id, "")
            ids = {t.strip().upper() for g in CITE_RE.findall(summary) for t in re.split(r"\s*[,;]\s*", g)}
            texts = {pid: store.get(pid).text for pid in ids if store.get(pid)}
            checked = apply_number_policy(summary, passage_texts=texts, allowed_numbers=_number_set(texts.values()),
                                          section_id=e.section_id).body if texts else ""
            line = takeaways(checked)
            if line:
                top.append(line)
                stats["takeaways"] += 1
            body, watch = move_watch_items(body)
        elif e.role == "compact" and sec is not None and len(sec.entities) >= 3:
            columns = entity_columns(tables, list(sec.entities), qtd=qtd)
            table = entity_table(columns, list(sec.entities), caption=f"{e.title}: key metrics by sector")
            if table:
                top.append(table)
                stats["comparisons"] += 1
                stats["placedTables"].update(c.table.passage_id for c in columns[:4])
        else:
            if re.search(r"(?i)outlook|risks?|scenarios?", e.title):
                body, watch = move_watch_items(body)
            # A cross-cutting section that discusses three or more sectors with data gets a comparison
            # of those sectors, from the tables it cites first (evidence-store cells only).
            from utils.section_layout import entities_mentioned, insert_after_first_paragraph
            discussed = entities_mentioned(body)
            if len(discussed) >= 3:
                columns = entity_columns(tables, discussed, qtd=qtd, preferred=cited)
                comparison = entity_table(columns, discussed, caption=f"{e.title}: the sectors discussed, compared",
                                          max_cols=3)
                if comparison:
                    body = insert_after_first_paragraph(body, comparison)
                    stats["comparisons"] += 1
                    # Its source tables are now shown: not placed again inline (same numbers twice
                    # fail the PDF export audit as a repeated observation).
                    stats["placedTables"].update(c.table.passage_id for c in columns[:3])
        agreement_question = bool(asks is not None and re.search(r"(?i)\b(?:fta|agreement|cepa|treat)", asks.items or ""))
        # Agreement questions take their timeline from the agreement ledger (dated signing / entry into
        # force); a section's other dated sentences (e.g. administrative notices) are not a timeline of it.
        if (asks is not None and (asks.per_item or asks.items) and not agreement_question
                and not any(b.startswith("> [!STATS]") for b in top)):
            # List / why-what-how questions (agreements, deals): a timeline of the section's own dated events.
            from utils.section_layout import insert_after_first_paragraph as _after_first, timeline_from_body
            timeline = timeline_from_body(body)
            if timeline:
                body = _after_first(body, timeline)
                stats["timelines"] = stats.get("timelines", 0) + 1
        before = set(stats["placedTables"])
        # Long sections may carry a third small evidence table; short ones at most two.
        long_section = len(re.findall(r"[A-Za-z]+", body)) > 1500
        body = place_inline_tables(body, by_id, stats["placedTables"], max_tables=3 if long_section else 2)
        stats["inlineTables"] += len(stats["placedTables"] - before)
        if watch:
            stats["watch"] += 1
        e.body = "\n\n".join(x for x in [*top, body, watch] if x).strip()
    # Sector scorecard + how to read, right after the executive summary.
    names = (outline.meta or {}).get("entityTiers") or {}
    sectors = list(names.get("deep_dive") or []) + list(names.get("compact") or [])
    if not sectors:
        from utils.entity_tiers import SYNONYMS, discover_entities
        sectors = [e.name for e in discover_entities(store) if e.name in SYNONYMS][:20]
    columns = entity_columns(tables, sectors, qtd=qtd)
    scorecard = entity_table(columns, sectors, caption="Sector scorecard: verified metrics by sector", min_values=2)
    if scorecard:
        stats["scorecardRows"] = scorecard.count("\n| ") - 1
        in_depth = str((outline.meta or {}).get("tier") or "") == "in_depth"
        # Counts of what exists: entity deep-dives and compact multi-sector comparisons (in-depth only).
        note = how_to_read(deep_dives=sum(1 for e in edited if in_depth and e.role == "deep_dive" and e.entity),
                           compact=sum(1 for e in edited if in_depth and e.role == "compact"
                                       and len(getattr(sections.get(e.section_id), "entities", []) or []) >= 3),
                           has_qtd="QTD, partial" in scorecard or any("QTD" in e.body for e in edited),
                           has_calculated=any("(calculated:" in e.body for e in edited))
        # The table opens the section page (it is one unbreakable block); the reading note follows it.
        stats["scorecard"] = f"## Sector Scorecard\n\n{scorecard}\n\n{note}"
    stats["distinctDeepDiveSubsectionSets"] = len({_set_key(s) for s in analysis if getattr(s, "role", "") == "deep_dive"})
    return stats


def _figure_paragraph(body: str, number: int) -> str:
    """The paragraph that interprets figure ``number`` (the one with "Figure N", else the one after its marker)."""
    paragraphs = re.split(r"\n\s*\n", str(body or ""))
    for i, para in enumerate(paragraphs):
        if re.search(rf"\bFigure\s+{number}\b", para):
            return para
        if re.search(rf"\[\[\s*FIGURE\s+{number}\s*\]\]", para, re.I) and i + 1 < len(paragraphs):
            return paragraphs[i + 1]
    return ""


def _source_level(body: str) -> str:
    def repl(match: re.Match) -> str:
        ids = []
        for token in re.split(r"\s*[,;]\s*", match.group(1)):
            sid = token.strip().upper().split(".")[0]
            if sid and sid not in ids:
                ids.append(sid)
        return f"[{', '.join(ids)}]" if ids else ""
    return CITE_RE.sub(repl, body)


def methodology_markdown(*, store, manifest: list[dict], outline, charts: list, drafts: list, gateway_stats: dict,
                         question: str, window_note: str, failed: list[str]) -> str:
    tiers = Counter(str(s.get("authority_tier") or "unknown") for s in manifest)
    dated = sum(1 for s in manifest if str(s.get("publication_date") or "").strip())
    kinds = Counter(p.kind for p in store.passages)
    cited_sources = {pid.split(".")[0] for d in drafts for pid in d.cited_passages}
    lines = [
        f"**Evidence base.** {len(manifest)} sources were retained after relevance and quality screening "
        f"({', '.join(f'{n} {t.replace(chr(95), chr(32))}' for t, n in tiers.most_common())}); {dated} carry a publication date. "
        f"They were split into {len(store.passages)} evidence passages ({', '.join(f'{n} {k.replace(chr(95), chr(32))}' for k, n in kinds.most_common())}). "
        "{{CITED_SOURCE_COUNT}} sources are cited in the analysis and listed in Data Sources.",
        "",
        "**Reliability scoring.** Each source has a reliability score (0–1): 55% authority tier (regulator/exchange data highest; "
        "personal blogs and pay-to-publish journals lowest; look-alike domains excluded), 25% recency relative to the requested "
        "period, and 20% evidence strength (a fully read publisher page with tables scores higher than a search excerpt). "
        "Sections draw their evidence in order of relevance × reliability.",
        "",
        "**How the report was built.** An outline fixed the thesis, the sections and the evidence each section could use. "
        "Each section was written only from its own evidence passages, with a running summary of earlier sections to avoid "
        "repetition. Charts were built directly from data tables in the evidence; each chart is cited to the passage it was "
        "copied from and is interpreted in the text beside it.",
    ]
    tier_counts = (outline.meta or {}).get("tierCounts")
    if tier_counts:
        names = (outline.meta or {}).get("entityTiers") or {}
        lines += ["", f"**Entity coverage by evidence.** {tier_counts.get('deep_dive', 0)} entities had substantive text from "
                      f"three or more reliable sources and receive a full section; {tier_counts.get('compact', 0)} had data "
                      f"tables but thin commentary and are covered in compact comparisons; "
                      f"{tier_counts.get('coverage_note', 0)} had neither."]
        if names.get("coverage_note"):
            lines += [f"Not analysed for lack of evidence: {', '.join(names['coverage_note'])}."]
    written = {d.section_id for d in drafts}
    roles = {s.section_id: s for s in outline.sections}
    own = [roles[sid].entity for sid in written if sid in roles and roles[sid].role == "deep_dive" and roles[sid].entity]
    compact = [sid for sid in written if sid in roles and roles[sid].role == "compact"]
    if own or compact:
        lines += ["", "**Entity coverage.** " + ("; ".join(x for x in [
            f"{len(own)} sector(s) have their own section ({', '.join(sorted(own)[:12])})" if own else "",
            f"{len(compact)} compact comparison section(s) cover further sectors" if compact else ""] if x)) + "."]
    if outline.decisions or failed:
        lines += ["", "**Scope decisions and limitations.**"]
        lines += [f"- {d}" for d in outline.decisions[:20]]
        lines += [f"- Section '{f}' could not be generated and is omitted." for f in failed]
    from utils.page_policy import shortfall_note
    note = shortfall_note(getattr(outline, "tier", "") or (outline.meta or {}).get("tier", ""),
                          int((outline.meta or {}).get("evidenceSupportedWords") or 0))
    if note:
        lines += ["", f"**Length.** {note}"]
    dropped = [c for c in charts if c.status == "dropped"]
    if dropped:
        lines += ["", f"{len(dropped)} candidate charts were not used: their data could not be verified or the text did not interpret them."]
    return "\n".join(lines).strip()


def appendix_sources_markdown(manifest: list[dict], cited: set[str]) -> str:
    rows = [s for s in manifest if str(s.get("id") or "").upper() in cited]
    if not rows:
        return "No sources were cited."
    rows.sort(key=lambda s: int(re.sub(r"\D", "", str(s.get("id") or "0")) or 0))
    out = ["Sources cited in this report, with the reliability assessment used to rank their evidence.", "",
           "| ID | Publisher | Authority tier | Date | Reliability | Basis |", "| --- | --- | --- | --- | --- | --- |"]
    for s in rows:
        note = re.sub(r"\s*\|\s*", "/", str(s.get("reliability_note") or ""))[:160]
        score = s.get("reliability_score")
        out.append(f"| [{s.get('id')}] | {str(s.get('publisher') or s.get('domain') or '-')[:40]} | "
                   f"{str(s.get('authority_tier') or 'unknown').replace('_', ' ')} | {str(s.get('publication_date') or 'undated')[:10]} | "
                   f"{'' if score is None else f'{float(score):.2f}'} | {note} |")
    return "\n".join(out)


def appendix_data_markdown(store, used_passages: set[str], max_tables: int = 10, max_rows: int = 30,
                           window_start=None, max_historical: int = 2) -> str:
    """Stage I (FIX 2): a table whose period ends before the question's window is never mixed with the
    in-window tables; at most ``max_historical`` such tables appear, in a labelled historical block."""
    from utils.section_charts import merge_continuations

    tables = merge_continuations([p for p in store.passages if p.kind in {"table", "calculated"} and p.passage_id in used_passages])
    if not tables:
        return ""
    out = ["Data tables used in the analysis, reproduced from the cited evidence passages."]
    historical: list[str] = []
    seen_headings: set[str] = set()
    shown = 0
    for p in tables:
        target = out
        if shown >= max_tables:
            break
        lines = [ln for ln in p.text.splitlines() if ln.strip()]
        heading = next((ln for ln in lines if " | " not in ln), getattr(p, "title", "") or p.passage_id)[:160]
        # One table per heading: a source page that repeats a table (or splits it) is shown once.
        key = re.sub(r"^TABLE\s+\d+\s*[—-]\s*", "", heading).replace("(continued)", "").strip().casefold()
        if key in seen_headings:
            continue
        seen_headings.add(key)
        rows = [ln for ln in lines if " | " in ln][: max_rows + 1]
        if len(rows) < 2:
            continue
        width = len(rows[0].split("|"))
        body_rows = [r for r in rows[1:] if len(r.split("|")) == width]
        if not body_rows:
            continue  # rows that do not fit the header would render an empty table under a bare caption
        from utils.table_quality import clean_rows, table_problem
        body_rows = clean_rows(rows[0], body_rows)
        _problem = table_problem(rows[0], body_rows)
        if _problem:  # Stage L: an unreadable table is not reproduced
            log.info("V2 appendix: table %s left out (%s)", p.passage_id, _problem)
            continue
        if window_start is not None:
            from utils.period_hygiene import in_window, table_period
            _label, _end = table_period(heading, [c.strip() for c in rows[0].split("|")], getattr(p, "published", "") or "")
            if _end is not None and not in_window(_end, window_start):
                if len([x for x in historical if x.startswith("### ")]) >= max_historical:
                    log.info("V2 appendix: out-of-window table skipped (%s, ends %s)", heading[:60], _end)
                    continue
                target = historical
        if target is out:
            shown += 1
        ids = ", ".join(getattr(p, "merged_ids", None) or [p.passage_id])
        # A clear caption: the source's table title without its "TABLE n —" numbering; a
        # too-short title is described from the table's own columns.
        caption = re.sub(r"^TABLE\s+\d+\b\s*(?:\(continued\))?\s*(?:[—-]\s*)?", "", heading).replace("(continued)", "").strip(" :")
        cols = [c.strip() for c in rows[0].strip().strip("|").split("|") if c.strip()]
        row_labels = [r.strip().strip("|").split("|")[0].strip() for r in body_rows]
        from utils.chart_titles import data_title, foreign_entities
        if len(caption) < 12 or foreign_entities(caption, row_labels):
            # A page heading that names other entities than the table holds (the C2 title problem)
            # is replaced by a caption built from the table's own columns and rows.
            caption = data_title(metric=cols[1] if len(cols) > 1 else "Values", labels=row_labels[:15])
        # A table wider than the page makes Chromium shrink the WHOLE PDF to fit it: show at
        # most 8 columns and say so (the full table is in the cited source).
        max_cols = 8

        def cells(line: str) -> list[str]:
            return [c.strip() for c in line.strip().strip("|").split("|")][:max_cols]

        head = cells(rows[0])
        target += ["", f"### {caption} [{ids}]", "", "| " + " | ".join(head) + " |",
                   "| " + " | ".join(["---"] * len(head)) + " |"]
        target += ["| " + " | ".join(cells(r)) + " |" for r in body_rows]
        total_cols = len(rows[0].strip().strip("|").split("|"))
        if total_cols > max_cols:
            target += ["", f"Note: the first {max_cols} of {total_cols} columns are shown; the full table is in the cited source."]
    if historical:
        out += ["", f"**Historical reference (before {window_start:%b %Y}; outside the question's window, for context only).**"]
        out += historical
    return "\n".join(out)


def _chart_target(tier: str, outline, used: list) -> dict:
    """Stage L: chart count against the tier target, and the deep-dive sections that carry no chart."""
    target = CHART_TARGET.get(tier, 8)
    with_chart = {c.section_id for c in used if getattr(c, "section_id", "")}
    gaps = [s.title for s in outline.sections if getattr(s, "role", "") == "deep_dive" and s.section_id not in with_chart]
    return {"target": target, "used": len(used), "met": len(used) >= target, "deepDivesWithoutChart": gaps}


async def run_pipeline_v2(*, runtime, question: str, tier: str, store, manifest: list[dict], today: str,
                          window_note: str = "", progress=None, gateway=None, tiers: dict | None = None,
                          chart_verifier=None, entity_search_stats: dict | None = None,
                          evidence_auditor=None, deadline: tuple[float, float] | None = None,
                          section_body_transform=None) -> dict:
    """``deadline``: (target, hard stop) as time.monotonic() values for the whole report job (in-depth:
    40 / 50 minutes). V2 keeps ~6 minutes for report.py's checks and the render after it."""
    started = time.perf_counter()
    deadline_log: dict = {"target": None, "hardStop": None, "waves": [], "extensionsSkipped": 0, "concise": []}
    if store is None or len(store.passages) < 10:
        raise PipelineV2Error(f"evidence store too small ({0 if store is None else len(store.passages)} passages)")
    gateway = gateway or LLMGateway(runtime, tier)
    # Stage F: what the question asks for (window, why/what/how, lists, audience, comparisons).
    from datetime import date as _asks_date
    from utils.question_asks import parse_asks
    asks = parse_asks(question, _asks_date.fromisoformat(today[:10]))
    if asks.window_note and asks.window_note not in window_note:
        window_note = f"{window_note}\n{asks.window_note}".strip()
    if tiers is None and tier == "in_depth":
        from utils.entity_tiers import entity_tiers
        tiers = entity_tiers(store, tier=tier, question=question)
    # Stage I (FIX 4): the market regime is computed in code from the fetched dataset and given to the
    # outline and the executive summary as fixed facts; the thesis and rotation claims are checked against it.
    regime, regime_log = None, {"computed": False, "thesisRewritten": False, "claims": [], "mislabels": []}
    try:
        from utils.dataset_charts import find_quarterly_dataset
        from utils.regime import compute_regime
        regime = compute_regime(find_quarterly_dataset(store))
    except Exception as exc:  # never block the report on the regime summary
        log.warning("V2 regime summary skipped: %s", type(exc).__name__)
    if regime is not None:
        regime_log.update(computed=True, firstHalf=regime.first_half, secondHalf=regime.second_half,
                          compounded=regime.compounded, leaders=regime.leaders, laggards=regime.laggards)
        window_note = f"{window_note}\n{regime.facts_block()}".strip()
    try:
        outline = await build_outline(store, gateway, question=question, tier=tier, today=today, window_note=window_note,
                                      asks=asks,
                                      tiers=tiers)
    except OutlineError as exc:
        raise PipelineV2Error(str(exc)) from exc
    if regime is not None:
        from utils.regime import thesis_needs_rewrite
        regime_log["dispersion"] = regime.dispersion_sentence()
        reason = thesis_needs_rewrite(outline.thesis or "", regime)
        if reason:
            log.warning("V2 regime: outline thesis rewritten (%s)", reason)
            regime_log.update(thesisRewritten=True, thesisReason=reason, originalThesis=(outline.thesis or "")[:400])
            outline.thesis = regime.thesis_sentence()
    analysis = outline.analysis_sections()
    if len(analysis) < 3:
        raise PipelineV2Error(f"only {len(analysis)} analysis sections have enough evidence")

    # Charts: build from stored data, assign to sections, number in section order.
    try:
        from datetime import date as _date
        from utils.data_angles import requested_year_bounds
        min_year, max_year = requested_year_bounds(question, _date.fromisoformat(today[:10]))
    except Exception:
        min_year, max_year = None, None
    from utils.table_focus import focus_entities as _focus_entities
    candidates, chart_drops = build_chart_candidates(store, max_charts=MAX_CHARTS.get(tier, 10) * 2, min_year=min_year,
                                                     max_year=max_year, today=today, focus=_focus_entities(question))
    table_chart_count = len(candidates) + len(chart_drops)
    series_warnings: list[dict] = []  # Stage M: sudden breaks in otherwise stable series (kept, said out loud)
    # Data first (Stage F): charts planned from fetched datasets before writing; they go through
    # the same verification below and are placed in their own sector's deep-dive.
    from utils.dataset_charts import build_dataset_charts, preassign
    from utils.section_charts import clip_to_window, verify_chart
    dataset_charts, dataset_note = build_dataset_charts(store, question=question)
    # Stage J: domain-generic datasets - the model proposes series from data-dense passages (PDF chart text,
    # flattened tables), code verifies every value and label against the passage.
    extracted_ids: set[str] = set()
    try:  # Stage K: official open datasets (World Bank / IMF / OWID) are data-first charts
        from utils.official_data import official_charts
        _official = official_charts(store, question)
        if _official:
            dataset_charts = list(dataset_charts) + _official
            extracted_ids |= {c.chart_id for c in _official}
            dataset_note = {**(dataset_note if isinstance(dataset_note, dict) else {}), "official": len(_official)}
    except Exception as exc:
        log.warning("V2 official-data charts skipped: %s: %s", type(exc).__name__, str(exc)[:160])
    if os.getenv("GG_V2_DATASET_EXTRACT", "1") != "0":
        try:
            from utils.dataset_extractor import extract_dataset_charts
            extracted, extract_note = await extract_dataset_charts(
                # Stage N: a 60-page live report got 3 charts; more data-dense passages are read (each call is
                # small, ~2.5k tokens, and every proposed point is still verified against its passage)
                store, gateway, question=question, today=today, limit=16 if tier == "in_depth" else 6,
                queries=[f"{s.title} {s.goal}" for s in analysis])
            dataset_note = {**(dataset_note if isinstance(dataset_note, dict) else {}), "extracted": extract_note}
            extracted_ids |= {c.chart_id for c in extracted}
            dataset_charts = list(dataset_charts) + extracted
        except Exception as exc:  # extraction must never abort a report
            log.warning("V2 dataset extraction skipped: %s: %s", type(exc).__name__, str(exc)[:160])
    for chart in dataset_charts:
        reason = clip_to_window(chart, min_year, max_year)
        if not reason:
            reason = verify_chart(chart, "\n".join(store.get(pid).text for pid in chart.passage_ids if store.get(pid)))
        if reason:
            chart.status, chart.drop_reason = "dropped", reason
            chart_drops.append({"chart": chart.chart_id, "title": chart.spec.get("title"), "passage": ",".join(chart.passage_ids),
                                "reason": reason, "provenance": "fetched dataset"})
    active_dataset = [c for c in dataset_charts if c.status != "dropped"]
    dataset_values = {c.chart_id: sorted(round(v, 4) for _, _, v in c.points) for c in active_dataset}
    # Table charts set aside in favour of a dataset chart, with the dataset chart(s) they deferred to.
    # They are only a duplicate view while that dataset chart survives the checks below; if it is
    # dropped, they take its place instead of the data disappearing from the report.
    deferred: list[tuple[object, set[str]]] = []
    deduped = []
    for chart in candidates:
        values = sorted(round(v, 4) for _, _, v in chart.points)
        owners = {chart_id for chart_id, dv in dataset_values.items() if values == dv}
        if owners:
            chart.status, chart.drop_reason = "dropped", "same values as a fetched-dataset chart (duplicate view)"
            chart_drops.append({"chart": chart.chart_id, "title": chart.spec.get("title"), "passage": ",".join(chart.passage_ids),
                                "reason": chart.drop_reason})
            deferred.append((chart, owners))
            continue
        deduped.append(chart)
    try:  # Stage K: an official dataset already has its data-first chart; its table is not re-charted per column
        from utils.official_data import is_official_passage
        official_ids = {c.chart_id for c in active_dataset if c.chart_id in extracted_ids}
        kept_tables = []
        for c in deduped:
            if any(store.get(pid) is not None and is_official_passage(store.get(pid)) for pid in c.passage_ids):
                owners = {d.chart_id for d in active_dataset if set(d.passage_ids) & set(c.passage_ids)} or official_ids
                deferred.append((c, owners))
            else:
                kept_tables.append(c)
        deduped = kept_tables
    except Exception as exc:
        log.warning("V2 official-table filter skipped: %s", type(exc).__name__)
    candidates = active_dataset + deduped
    # Stage J: the chart quality gate also runs BEFORE writing (no text is written around a chart that
    # will be dropped: year-valued attributes, list-number labels, all-equal values, Total + components).
    _earlier: list = []  # Stage L: the same data from a second publisher is not a second chart
    _tables_seen: list = []
    _quality_memory: dict[str, tuple] = {}  # chart id -> (its _earlier entry, its _tables_seen entry or None)

    def _quality_pass(charts: list) -> list:
        try:
            from utils.chart_lint import quality_reason
            _kept = []
            for chart in charts:
                _reason = quality_reason(chart.spec, chr(10).join(
                    store.get(pid).text for pid in chart.passage_ids if store.get(pid))[:400], _earlier)
                if not _reason:  # Stage M: shape checks (place, unit) - any topic
                    from utils.series_checks import chart_data_problem
                    _reason = chart_data_problem(chart.spec, question)
                if not _reason:
                    # the same TABLE from another publisher (sorted differently, so the plotted rows differ)
                    from collections import Counter as _Counter
                    _nums = _Counter(f"{float(x):g}" for x in re.findall(r"\d+(?:\.\d+)?", chr(10).join(
                        store.get(pid).text for pid in chart.passage_ids if store.get(pid)).replace(",", "")))
                    _srcs = set(chart.source_ids or [])
                    _table_entry = None
                    if sum(_nums.values()) >= 8 and any(
                            not (_srcs & other_srcs)
                            and sum((_nums & other).values()) >= 0.8 * min(sum(_nums.values()), sum(other.values()))
                            for other_srcs, other in _tables_seen):
                        _reason = "the same table as an earlier chart, from another source"
                    else:
                        _table_entry = (_srcs, _nums)
                        _tables_seen.append(_table_entry)
                if not _reason:
                    _values = sorted(round(v, 4) for s_ in chart.spec.get("series") or [] for v in
                                     [float(p_.get("value") or 0) for p_ in s_.get("data") or []])
                    _earlier.append(_values)
                    _quality_memory[chart.chart_id] = (_values, _table_entry)
                if _reason:
                    chart.status, chart.drop_reason = "dropped", f"quality gate before writing: {_reason}"
                    chart_drops.append({"chart": chart.chart_id, "title": chart.spec.get("title"), "passage": ",".join(chart.passage_ids),
                                        "reason": chart.drop_reason})
                else:
                    _kept.append(chart)
            return _kept
        except Exception as exc:
            log.warning("V2 early chart quality gate skipped: %s", type(exc).__name__)
            return charts

    def _verify_pass(charts: list) -> list:
        # Run the same chart verification the export gates use BEFORE writing, so the
        # writer only interprets charts that will survive to the PDF.
        if not (chart_verifier and charts):
            return charts
        try:
            reasons = chart_verifier([dict(c.spec, evidencePassages=c.passage_ids) for c in charts])
        except Exception as exc:  # verification problems must not abort the report
            log.warning("V2 chart pre-verification failed: %s", type(exc).__name__)
            reasons = [""] * len(charts)
        kept = []
        for chart, reason in zip(charts, reasons):
            if reason:
                chart.status, chart.drop_reason = "dropped", f"failed chart verification before writing: {reason}"
                chart_drops.append({"chart": chart.chart_id, "title": chart.spec.get("title"),
                                    "passage": ",".join(chart.passage_ids), "reason": chart.drop_reason})
            else:
                kept.append(chart)
        return kept

    candidates = _verify_pass(_quality_pass(candidates))
    surviving_ids = {c.chart_id for c in candidates}
    replacements = [chart for chart, owners in deferred if owners and not (owners & surviving_ids)]
    if replacements:
        restored_ids = {c.chart_id for c in replacements}
        chart_drops[:] = [d for d in chart_drops if not (d.get("chart") in restored_ids
                                                         and "duplicate view" in str(d.get("reason") or ""))]
        for chart in replacements:
            chart.status, chart.drop_reason = "planned", ""
        # The gate must compare against charts that will be published, not ones verification just dropped
        # (their values would make the replacement look like "the same values as an earlier chart").
        _earlier[:] = [memory[0] for chart_id, memory in _quality_memory.items() if chart_id in surviving_ids]
        _tables_seen[:] = [memory[1] for chart_id, memory in _quality_memory.items()
                           if chart_id in surviving_ids and memory[1] is not None]
        restored = _verify_pass(_quality_pass(replacements))
        if restored:
            log.info("V2: %d table chart(s) restored because the dataset chart they duplicated was dropped",
                     len(restored))
        candidates = candidates + restored
    # Stage L: every verified chart carries a reading of its own data, so a chart the writer does not
    # mention is placed with that sentence instead of being dropped (charts are the report's backbone).
    try:
        from utils.chart_story import interpret_chart
        from utils.series_checks import jump_warnings
        for chart in candidates:
            if not chart.interpretation and not chart.reading:
                chart.reading = interpret_chart(chart.spec, chart.passage_ids)
            # Stage M: a sudden break in an otherwise stable series is kept but said out loud
            _warnings = jump_warnings(chart.spec)
            if _warnings:
                chart.spec["dataWarnings"] = _warnings
                series_warnings.append({"title": chart.spec.get("title"), "warnings": _warnings})
                note = "Note: " + "; ".join(_warnings) + "."
                if chart.reading:
                    chart.reading = f"{chart.reading} {note}"
                if chart.interpretation:
                    chart.interpretation = f"{chart.interpretation} {note}"
    except Exception as exc:
        log.warning("V2 chart interpretation skipped: %s", type(exc).__name__)
    # Fetched-dataset charts are placed first (a deep-dive gets its own sector's chart) and come on
    # top of the per-section limit for table charts; they are numbered from 101 until the final
    # renumbering in document order, so table charts keep the numbers the writer is given.
    data_first = [c for c in candidates if c.interpretation]
    preassign(data_first, analysis, side_charts=DATASET_SIDE_CHARTS.get(tier, 4))
    if extracted_ids:
        from utils.dataset_extractor import assign_to_sections
        assign_to_sections([c for c in data_first if c.chart_id in extracted_ids], analysis)
    table_charts = [c for c in candidates if not c.interpretation]
    assign_charts(table_charts, analysis, per_section=CHARTS_PER_SECTION.get(tier, 2))
    unassigned = [c for c in candidates if not c.section_id]
    planned = [c for c in candidates if c.section_id]
    figure_no = 0
    for sec in analysis:
        for chart in [c for c in table_charts if c.section_id == sec.section_id][:CHARTS_PER_SECTION.get(tier, 2)]:
            figure_no += 1
            chart.figure_number = figure_no
    data_no = 100
    for sec in analysis:
        for chart in [c for c in data_first if c.section_id == sec.section_id]:
            data_no += 1
            chart.figure_number = data_no
    planned_data = [c for c in planned if c.figure_number and c.interpretation][: MAX_TOTAL_CHARTS.get(tier, 36)]
    table_quota = max(0, min(MAX_CHARTS.get(tier, 10), MAX_TOTAL_CHARTS.get(tier, 36) - len(planned_data)))
    planned_table = [c for c in planned if c.figure_number and not c.interpretation]
    for chart in planned_table[table_quota:]:
        chart.section_id, chart.figure_number = "", 0  # over the report's chart budget (page cap)
        unassigned.append(chart)
    planned = planned_table[:table_quota] + planned_data

    # Sections in waves: each wave sees summaries of completed sections.
    # In-depth: waves of 6 (fewer barriers; each wave still sees summaries of all earlier waves).
    wave_size = max(1, int(os.getenv("GG_V2_WAVE_SIZE", "6" if tier == "in_depth" else "3") or 3))
    drafts = []
    goals = {s.section_id: f"{s.title}: {s.goal}" for s in analysis}
    import asyncio
    reserve = 6 * 60  # report.py checks, self-healing gates and the render run after V2
    wave_seconds: list[float] = []
    section_transform_log: list[str] = []
    from utils.net_recovery import wait_budget as _wait_budget
    network_wait_left = _wait_budget()
    for start in range(0, len(analysis), wave_size):
        wave = analysis[start:start + wave_size]
        covered = [_covered_line(d) for d in drafts if d.ok]
        allow_extension, budget_scale, mode = True, 1.0, "full"
        if deadline:
            now = time.monotonic()
            expected = (sum(wave_seconds) / len(wave_seconds)) if wave_seconds else 0.0
            if now + expected * 0.6 > deadline[1] - reserve:
                allow_extension, budget_scale, mode = False, 0.5, "concise (hard stop near)"
            elif now + expected > deadline[0] - reserve:
                allow_extension, mode = False, "no extension calls (target near)"
            if mode != "full":
                log.warning("V2 deadline: sections %d-%d written with %s", start + 1, start + len(wave), mode)
                if not allow_extension:
                    deadline_log["extensionsSkipped"] += len(wave)
                if budget_scale < 1:
                    deadline_log["concise"] += [s.title for s in wave]
        if progress:
            progress(f"Writing sections {start + 1}-{start + len(wave)} of {len(analysis)}…")
        wave_started = time.monotonic()

        def _write(sec, covered=covered, allow_extension=allow_extension, budget_scale=budget_scale):
            return write_section(gateway, store, sec, [c for c in planned if c.section_id == sec.section_id],
                                 question=question, thesis=outline.thesis, already_covered=covered,
                                 other_sections=[g for sid, g in goals.items() if sid != sec.section_id],
                                 allow_extension=allow_extension, budget_scale=budget_scale)
        results = list(await asyncio.gather(*[_write(sec) for sec in wave]))
        # Stage N: sections lost only to a network outage are written again once the network is back
        # (a DNS outage of a few minutes used to cost every section and drop the report to the fallback writer).
        from utils.net_recovery import network_failure, wait_budget, wait_for_network
        lost = [i for i, d in enumerate(results) if not d.ok and network_failure(getattr(d, "error", ""))]
        if lost and network_wait_left > 0 and not (deadline and time.monotonic() > deadline[1] - reserve):
            if progress:
                progress(f"Network unreachable; waiting to rewrite {len(lost)} section(s)…")
            waited, back = await wait_for_network(network_wait_left)
            network_wait_left -= waited
            deadline_log.setdefault("networkWaits", []).append(
                {"sections": [wave[i].title for i in lost], "waitedSeconds": round(waited), "networkBack": back})
            if back:
                retried = await asyncio.gather(*[_write(wave[i]) for i in lost])
                for i, draft in zip(lost, retried):
                    results[i] = draft
                log.warning("V2: rewrote %d section(s) after a network outage; %d recovered", len(lost),
                            sum(1 for d in retried if d.ok))
        if callable(section_body_transform):
            for section, draft in zip(wave, results):
                try:
                    transformed = _apply_section_body_transform(
                        draft, section, section_body_transform, store=store, charts=planned, covered=covered,
                    )
                except Exception as exc:
                    log.warning("V2 section transform skipped for '%s': %s", section.title[:100], type(exc).__name__)
                    continue
                if transformed:
                    section_transform_log.append(section.title[:160])
        wave_seconds.append(time.monotonic() - wave_started)
        deadline_log["waves"].append({"sections": f"{start + 1}-{start + len(wave)}", "mode": mode,
                                      "seconds": round(wave_seconds[-1])})
        drafts.extend(results)
    # Chart/text coherence: the paragraph that interprets a figure must be about the
    # chart's own entities and values; otherwise the chart is dropped and the text's
    # figure framing is removed at assembly (the sentence keeps its own citation).
    chart_text_mismatches = []
    body_of = {d.section_id: d.body for d in drafts if d.ok}
    for chart in planned:
        if chart.status != "referenced":
            continue
        paragraph = _figure_paragraph(body_of.get(chart.section_id, ""), chart.figure_number) or chart.claim
        reason = text_chart_mismatch(paragraph, dict(chart.spec))
        if reason and (chart.interpretation or chart.reading):
            # A fetched-dataset chart is not dropped: its own interpretation (from the plotted rows)
            # is placed right after the figure, so the chart sits next to a paragraph about it.
            draft = next((d for d in drafts if d.section_id == chart.section_id), None)
            marker = f"[[FIGURE {chart.figure_number}]]"
            if draft is not None and marker in draft.body:
                _text = chart.interpretation or chart.reading
                if chart.reading and not chart.interpretation and _text[:2] != _text[:2].upper():
                    _text = _text[0].lower() + _text[1:]
                draft.body = draft.body.replace(marker, f"{marker}\n\nAs Figure {chart.figure_number} shows, {_text}", 1)
                body_of[chart.section_id] = draft.body
                chart_text_mismatches.append({"title": chart.spec.get("title"), "reason": reason, "claim": chart.claim[:200],
                                              "action": "kept; dataset interpretation placed next to the figure"})
                continue
        if reason:
            chart.status, chart.drop_reason = "dropped", f"chart/text mismatch: {reason}"
            chart_text_mismatches.append({"title": chart.spec.get("title"), "reason": reason, "claim": chart.claim[:200]})
    good = [d for d in drafts if d.ok]
    failed = [d.title for d in drafts if not d.ok]
    if len(good) < max(3, len(analysis) // 2):
        raise PipelineV2Error(f"only {len(good)} of {len(analysis)} sections were generated ({'; '.join(d.error for d in drafts if not d.ok)[:300]})")

    # Executive summary from section summaries.
    exec_section = next(s for s in outline.sections if s.kind == "executive_summary")
    summaries = "\n\n".join(f"## {d.title}\n{d.summary}" for d in good if d.summary)
    exec_prompt = (f"QUESTION: {question}\nTHESIS: {outline.thesis}\nLENGTH: about {exec_section.word_budget} words.\n\n"
                   + (f"{regime.facts_block()}\n\n" if regime is not None else "")
                   + f"SECTION SUMMARIES:\n{summaries}")
    exec_result = await gateway.generate(purpose="executive_summary", section_id="exec", system=EXEC_SYSTEM,
                                         prompt=exec_prompt, max_tokens=4096, temperature=0.2, timeout=120)
    exec_body = (exec_result.text.split(SUMMARY_MARKER)[0].strip() if exec_result.ok else "")
    exec_body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", exec_body).strip()
    summary_passages = {t.strip().upper() for d in good for g in CITE_RE.findall(d.summary) for t in re.split(r"\s*[,;]\s*", g)}
    exec_body = CITE_RE.sub(lambda m: "[" + ", ".join(t for t in re.split(r"\s*[,;]\s*", m.group(1)) if t.strip().upper() in summary_passages) + "]", exec_body)
    exec_body = exec_body.replace("[]", "")
    exec_texts = {pid: store.get(pid).text for pid in summary_passages if store.get(pid)}
    # Point each number at the summary passage that actually holds it before the (strict) policy runs.
    from utils.section_writer import repair_citations
    exec_body, _ = repair_citations(exec_body, {pid: _number_set([text]) for pid, text in exec_texts.items()})
    exec_policy = apply_number_policy(
        exec_body, passage_texts=exec_texts,
        allowed_numbers=_number_set(d.summary for d in good), section_id="exec")
    exec_body = exec_policy.body
    exec_unsupported = unsupported_numbers(exec_body, _number_set(d.summary for d in good))
    try:  # Stage I (FIX 9): headline claims backed only by low-reliability sources are qualified
        from utils.stage_i_gates import qualify_low_reliability
        _reliability = {}
        for _m in manifest or []:
            try:
                _reliability[str(_m.get("id") or "").upper()] = float(_m.get("reliability_score") or _m.get("reliabilityScore"))
            except (TypeError, ValueError):
                continue
        exec_body, regime_log["lowReliabilityQualified"] = qualify_low_reliability(exec_body, _reliability)
    except Exception as exc:
        log.warning("V2 low-reliability qualifier skipped: %s", type(exc).__name__)
    # Stage I (FIX 4 + dispersion): the executive summary opens with the computed regime statement when
    # its own opening contradicts the regime or omits a sector-specific dispersion finding.
    if regime is not None and exec_body:
        from utils.regime import thesis_needs_rewrite
        opening = exec_body.split("\n\n", 1)[0]
        reason = thesis_needs_rewrite(opening, regime)
        if reason:
            try:
                from utils.dataset_charts import find_quarterly_dataset
                data = find_quarterly_dataset(store)
                cite = next(iter(data.rows.values())).passage_id if data and data.rows else ""
            except Exception:
                cite = ""
            exec_body = regime.thesis_sentence().rstrip(".") + (f" [{cite}]." if cite else ".") + "\n\n" + exec_body
            regime_log.update(execThesisInserted=True, execThesisReason=reason)
            log.warning("V2 regime: executive summary opens with the computed regime statement (%s)", reason[:160])

    # Figure renumbering in document order (dropped figures leave no gaps).
    used = [c for c in planned if c.status == "referenced"]
    # Document order: section order, then where the figure's marker sits in the section text.
    section_order = {s.section_id: i for i, s in enumerate(analysis)}
    body_by_section = {d.section_id: d.body for d in drafts}

    def _doc_position(c):
        body = body_by_section.get(c.section_id, "")
        at = body.find(f"[[FIGURE {c.figure_number}]]")
        return (section_order.get(c.section_id, 999), at if at >= 0 else len(body), c.figure_number)

    used.sort(key=_doc_position)
    mapping = {c.figure_number: i for i, c in enumerate(used, 1)}
    charts_out = []
    claims_seen: set[str] = set()
    for c in used:
        c.claim = _renumber_figures(c.claim, mapping)  # the claim must use the final figure number
        c.figure_number = mapping[c.figure_number]
        # One sentence about two figures ("Figure 21 and Figure 22 compare ...") must not caption both:
        # the export audit reads the repeated caption as a repeated observation. The second figure
        # gets its own deterministic interpretation, or a caption naming its own data.
        key = re.sub(r"\W+", " ", c.claim).strip().casefold()
        if key and key in claims_seen:
            c.claim = (f"Figure {c.figure_number} shows {c.interpretation[0].lower()}{c.interpretation[1:]}"
                       if c.interpretation else f"Figure {c.figure_number} shows {c.spec.get('title')}.")
            c.claim = re.sub(r"\s+", " ", CITE_RE.sub("", c.claim)).strip()[:400]
        claims_seen.add(re.sub(r"\W+", " ", c.claim).strip().casefold())
        spec = dict(c.spec)
        spec["title"] = f"Figure {c.figure_number}: {spec.get('title')}"
        # Stage I (FIX 6): the takeaway comes from the plotted values, never the body sentence that
        # interprets the figure; there is no CONTEXT box ("Supports: <the same sentence>").
        from utils.repetition import data_takeaway
        spec["takeaway"] = data_takeaway(spec)
        spec["rationale"] = ""
        spec["evidencePassages"] = c.passage_ids
        charts_out.append(spec)
    unique_titles(charts_out)

    cited_passages = sorted({pid for d in good for pid in d.cited_passages} | {t.strip().upper() for g in CITE_RE.findall(exec_body) for t in re.split(r"\s*[,;]\s*", g) if "." in t})
    cited_sources = {pid.split(".")[0] for pid in cited_passages}
    data_passages = set(cited_passages) | {pid for c in used for pid in c.passage_ids}

    parts = []
    if exec_body:
        parts.append(f"## {exec_section.title}\n\n{exec_body}")
    entity_of = {s.section_id: (s.entity if getattr(s, "role", "") == "deep_dive" else "") for s in analysis}
    role_of = {s.section_id: getattr(s, "role", "cross_cutting") or "cross_cutting" for s in analysis}
    edited = []
    for d in drafts:
        if d.ok:
            body = _renumber_figures(d.body, mapping)
            entity = entity_of.get(d.section_id) or ""
            if entity:
                # "Performance and momentum" repeats in every sector deep-dive; name the sector.
                body = re.sub(r"(?m)^(###\s+)(?!.*" + re.escape(entity) + r")(.+)$", lambda m: f"{m.group(1)}{entity}: {m.group(2)}", body)
            edited.append(EditedSection(section_id=d.section_id, title=d.title, body=body, role=role_of.get(d.section_id, ""),
                                        entity=entity, summary=d.summary))
    if progress:
        progress("Editing the full report…")
    # Past the hard stop the optional LLM transitions call is skipped (deterministic edits still run).
    skip_transitions = bool(deadline and time.monotonic() > deadline[1] - 6 * 60)
    if skip_transitions:
        deadline_log["transitionsSkipped"] = True
        log.warning("V2 deadline: editorial transitions call skipped")
    edited, editorial = await editorial_pass(edited, gateway=None if skip_transitions else gateway)
    try:
        from utils.source_quality import window_start as _window_start
        layout_window = _window_start(question, _asks_date.fromisoformat(today[:10]))
    except Exception:
        layout_window = None
    layout = _apply_section_layout(edited, store=store, outline=outline, analysis=analysis, good=good, today=today,
                                   asks=asks, window_start=layout_window,
                                   charted={pid for c in used for pid in c.passage_ids})
    # Agreement list questions ("all the FTAs signed in the past 1 year"): the dated ledger from the
    # evidence (signing kept apart from entry into force; what official sources verify), placed in the
    # section that answers the list ask.
    ledger_meta: dict = {}
    from utils.question_asks import month_window
    span = month_window(question, _asks_date.fromisoformat(today[:10]))
    if span and re.search(r"(?i)\b(?:fta|agreement|cepa|treat)", asks.items or "") and edited:
        from utils.agreement_ledger import ledger_block
        from utils.section_layout import insert_after_first_paragraph as _ledger_after
        block, ledger_meta = ledger_block(store, manifest, span[0], span[1], items=asks.items)
        from utils.agreement_ledger import ledger_timeline
        timeline = ledger_timeline(store, manifest, span[0], span[1])
        if block and timeline:
            block = f"{block}\n\n{timeline}"
        if block:
            list_titles = [t for a in asks.asks if a.kind == "deliverable" for t in a.sections]
            target = next((e for e in edited if e.title in list_titles), edited[0])
            target.body = _ledger_after(target.body, block)
            ledger_meta["section"] = target.title
    if layout["scorecard"]:
        parts.append(layout["scorecard"])
    parts += [f"## {e.title}\n\n{e.body}" for e in edited]
    method = methodology_markdown(store=store, manifest=manifest, outline=outline, charts=candidates, drafts=good,
                                  gateway_stats=gateway.stats.to_dict(), question=question, window_note=window_note, failed=failed)
    # Coverage note: each ask of the question and the section that answers it (sections that failed
    # to generate no longer count); "exhaustive" is never claimed.
    from utils.question_asks import coverage_note, guard_exhaustive_claims
    written = {d.title for d in good}
    for ask in asks.asks:
        if ask.kind != "window":
            ask.sections = [t for t in ask.sections if t in written]
            if not ask.sections and ask.status in {"mapped", "added"}:
                ask.status = "unmapped"
    note = coverage_note(asks)
    if note:
        method = f"**{note.split(':', 1)[0]}.** {note.split(':', 1)[1].strip()}\n\n{method}"
    try:  # Stage M: figures that different sources give differently for the same measure and period
        from utils.disagreements import disagreement_note, find_disagreements
        disagreements = find_disagreements("\n\n".join(parts))
        if disagreements:
            method = f"{method}\n\n{disagreement_note(disagreements)}"
    except Exception as exc:
        disagreements = []
        log.warning("V2 disagreement check skipped: %s", type(exc).__name__)
    parts.append(f"## Methodology and Evidence Base\n\n{method}")
    if tier == "in_depth":
        # Sources and their reliability are listed once, by the renderer ("Appendix B — Data Sources").
        # Tables placed in the text that discusses them are not repeated in the appendix.
        try:
            from datetime import date as _ad
            from utils.source_quality import window_start as _ws
            _appendix_window = _ws(question, _ad.fromisoformat(today[:10]))
        except Exception:
            _appendix_window = None
        data_md = appendix_data_markdown(store, set(data_passages) - layout["placedTables"], window_start=_appendix_window)
        if data_md:
            parts.append(f"## Appendix A — Data Tables\n\n{data_md}")
    report_md = STRAY_LABEL_RE.sub("", "\n\n".join(parts))
    # A table cell that is only a dash means "no value"; the chart checks read an em dash as
    # "explicitly not verified" and then reject charts of the same entity. Leave such cells empty.
    report_md = blank_dash_cells(report_md)
    from utils.section_layout import fix_heading_runs
    report_md, layout["headingsRemoved"] = fix_heading_runs(report_md)
    report_md = FIGURE_MARKER_RE.sub(lambda m: f"[CHART_{int(m.group(1))}]" if 1 <= int(m.group(1)) <= len(charts_out) else "", report_md)
    report_md = _source_level(report_md)
    report_md, exhaustive_rewrites = guard_exhaustive_claims(report_md, asks)
    from utils.leak_gate import remove_leaks
    report_md, leak_actions = remove_leaks(report_md)  # Stage I: no writer-prompt text reaches the report
    try:
        from utils.regime import fix_mislabels, heal_rotation_claims
        report_md, regime_log["mislabels"] = fix_mislabels(report_md)
        if regime is not None:
            report_md, regime_log["claims"] = heal_rotation_claims(report_md, regime)
    except Exception as exc:
        log.warning("V2 regime checks skipped: %s", type(exc).__name__)
    # Stage I (FIX 6): each dataset once, a statistic at most twice in the prose, figure boxes not
    # repeating the body.
    repetition_log: dict = {}
    try:
        from utils.repetition import cap_headline_stats, dedupe_tables, echoes_body, repetition_metrics
        repetition_log["before"] = repetition_metrics(report_md, charts_out)
        report_md, repetition_log["tablesRemoved"] = dedupe_tables(report_md, charts_out)
        report_md, repetition_log["statCap"] = cap_headline_stats(report_md)
        from utils.repetition import drop_caption_restatements
        report_md, repetition_log["captionRestatements"] = drop_caption_restatements(report_md, charts_out)
        for chart in charts_out:
            if chart.get("takeaway") and echoes_body(chart["takeaway"], report_md):
                repetition_log.setdefault("takeawaysDropped", []).append(chart.get("title"))
                chart["takeaway"] = ""
        repetition_log["after"] = repetition_metrics(report_md, charts_out)
        from utils.stage_i_gates import fix_small_items
        report_md, repetition_log["smallItems"] = fix_small_items(report_md)
    except Exception as exc:
        log.warning("V2 repetition control skipped: %s", type(exc).__name__)
    report_md = re.sub(r"\n{3,}", "\n\n", report_md).strip()
    report_md, charts_out, _ = finalize_figures(report_md, charts_out)
    # Some chart checks depend on the finished text (e.g. a report table that marks
    # an entity/period unavailable). Run the verifier again on the assembled report
    # and drop failing charts here, rewriting their references, so later gates do
    # not remove charts the prose depends on.
    dropped_after_assembly = []
    if chart_verifier and charts_out:
        try:
            reasons = chart_verifier(charts_out, report_md)
        except TypeError:
            reasons = [""] * len(charts_out)
        except Exception as exc:
            log.warning("V2 post-assembly chart verification failed: %s", type(exc).__name__)
            reasons = [""] * len(charts_out)
        failing = {i + 1 for i, reason in enumerate(reasons) if reason}
        if failing:
            dropped_after_assembly = [{"title": charts_out[i - 1].get("title"), "reason": reasons[i - 1]} for i in sorted(failing)]
            keep = [i for i in range(1, len(charts_out) + 1) if i not in failing]
            remap = {old: new for new, old in enumerate(keep, 1)}
            report_md = re.sub(r"\[CHART_(\d+)\]", lambda m: f"[CHART_§{remap[int(m.group(1))]}]" if int(m.group(1)) in remap else "", report_md).replace("§", "")
            charts_out = [charts_out[i - 1] for i in keep]
            report_md, charts_out, _ = finalize_figures(report_md, charts_out)
            report_md = re.sub(r"\n{3,}", "\n\n", report_md).strip()

    # Stage I (FIX 8): chart quality gate + CHART LINT (auto-fix titles, axes, units, types, precision,
    # source lines) before render; a dropped chart's references are rewritten by finalize_figures.
    chart_lint_log: dict = {}
    if charts_out:
        try:
            from utils.chart_lint import lint_all, run_chart_lint
            manifest_by_id = {str(m.get("id") or "").upper(): m for m in manifest or [] if isinstance(m, dict)}

            def _passage_text(spec):
                return "\n".join(store.get(pid).text for pid in spec.get("evidencePassages") or [] if store.get(pid))

            linted, lint_dropped, lint_fix = run_chart_lint(charts_out, passage_text=_passage_text,
                                                            manifest=manifest_by_id, today=today)
            if lint_dropped:
                gone = {d["index"] for d in lint_dropped}
                keep = [i for i in range(1, len(charts_out) + 1) if i not in gone]
                remap = {old: new for new, old in enumerate(keep, 1)}
                report_md = re.sub(r"\[CHART_(\d+)\]", lambda m: f"[CHART_§{remap[int(m.group(1))]}]"
                                   if int(m.group(1)) in remap else "", report_md).replace("§", "")
            charts_out = [c for c in linted]
            report_md, charts_out, _ = finalize_figures(report_md, charts_out)
            report_md = re.sub(r"\n{3,}", "\n\n", report_md).strip()
            inventory, failing = lint_all(charts_out)
            chart_lint_log = {"dropped": [{"title": d["title"], "reason": d["reason"]} for d in lint_dropped],
                              "fixed": lint_fix["fixed"], "inventory": inventory, "failing": failing}
        except Exception as exc:  # the lint must never abort a report
            log.warning("V2 chart lint skipped: %s: %s", type(exc).__name__, str(exc)[:160])
    try:  # Stage I (FIX 8): tables have their own "Table N" sequence, a caption and a source
        from utils.chart_lint import number_tables
        report_md, table_actions, table_inventory = number_tables(report_md)
        chart_lint_log.update(tableActions=table_actions, tableInventory=table_inventory)
    except Exception as exc:
        log.warning("V2 table numbering skipped: %s: %s", type(exc).__name__, str(exc)[:160])

    # Number check = report.py's source-evidence check (value, unit and metric): claims it
    # flags are dropped here, with a log line, before report.py's own check runs. Whatever is
    # still flagged stays flagged (the report is then a draft); nothing is hidden.
    evidence_removed, evidence_flagged, fact_overrides = [], [], []
    if evidence_auditor is not None:
        from utils.evidence_check import agreeing_auditor, fact_rewriter, fix_flagged_claims
        report_md, evidence_removed, evidence_flagged = fix_flagged_claims(
            report_md, agreeing_auditor(evidence_auditor, store, fact_overrides), rewriter=fact_rewriter(store))
        report_md = re.sub(r"\n{3,}", "\n\n", report_md).strip()
    words = len(re.findall(r"[A-Za-z][A-Za-z'’-]*", CITE_RE.sub(" ", report_md)))
    est_pages = estimate_pages(sum(d.words for d in good), len(exec_body.split()), len(charts_out),
                               len(cited_sources) if tier == "in_depth" else 0,
                               len([p for p in data_passages if (store.get(p) or None) and store.get(p).kind in {"table", "calculated"}]) if tier == "in_depth" else 0)
    meta = {
        "pipeline": "v2", "elapsedSeconds": round(time.perf_counter() - started, 1),
        "outline": outline.to_dict(), "llm": gateway.stats.to_dict(),
        "sections": [{"id": d.section_id, "title": d.title, "ok": d.ok, "error": d.error, "words": d.words, "calls": d.calls,
                      "citedPassages": len(d.cited_passages), "invalidCitations": d.invalid_citations,
                      "repairedCitations": d.repaired_citations,
                      "unsupportedNumbers": d.unsupported_numbers[:30], "figuresReferenced": d.figures_referenced,
                      "figuresDropped": d.figures_dropped} for d in drafts],
        "executiveSummary": {"ok": exec_result.ok, "words": len(exec_body.split()), "unsupportedNumbers": exec_unsupported[:30]},
        "charts": {"candidates": len(candidates) + len(chart_drops), "droppedBeforeWriting": chart_drops,
                   "planned": len(planned), "used": len(charts_out),
                   "droppedAfterWriting": [{"title": c.spec.get("title"), "reason": c.drop_reason} for c in planned if c.status != "referenced"],
                   "droppedAfterAssembly": dropped_after_assembly,
                   "claims": [{"figure": c.figure_number, "claim": c.claim, "passages": c.passage_ids} for c in used]},
        # Runtime deadline (Stage H): per wave, what was skipped to stay inside it.
        "deadline": {**deadline_log, "target": round(deadline[0] - time.monotonic()) if deadline else None,
                     "hardStop": round(deadline[1] - time.monotonic()) if deadline else None},
        # Question asks (Stage F): each ask, the section answering it, and the methodology coverage note.
        "leakGate": leak_actions,
        "regime": regime_log,
        "repetition": repetition_log,
        "chartLint": chart_lint_log,
        "coverage": {**asks.to_dict(), "note": note, "exhaustiveClaimsRewritten": exhaustive_rewrites,
                     "agreementLedger": ledger_meta},
        "chartTarget": _chart_target(tier, outline, used),
        "seriesWarnings": series_warnings,
        "sourceDisagreements": disagreements,
        # Chart funnel (Stage F): every chart from candidate to PDF, with the stage and reason it left.
        "chartFunnel": {
            "fromFetchedDatasets": len(dataset_charts), "fromTables": table_chart_count, "dataset": dataset_note,
            "verifiedBeforeWriting": len(candidates), "placedInSections": len(planned) + len([c for c in candidates if c.section_id and not c.figure_number]),
            "unplaced": len(unassigned), "interpretedAfterWriting": len([c for c in planned if c.status == "referenced"]),
            "droppedAfterAssembly": len(dropped_after_assembly), "used": len(charts_out),
            "usedFromFetchedDatasets": len([c for c in used if c.interpretation]),
            "drops": ([{"stage": "before writing", "title": d.get("title"), "reason": d.get("reason")} for d in chart_drops]
                      + [{"stage": "not placed", "title": c.spec.get("title"), "reason": "no section fits it, its section already has the most charts, or the report's chart budget (page cap) is used"} for c in unassigned]
                      + [{"stage": "after writing", "title": c.spec.get("title"), "reason": c.drop_reason} for c in planned if c.status != "referenced"]
                      + [{"stage": "after assembly", "title": d.get("title"), "reason": d.get("reason")} for d in dropped_after_assembly]),
        },
        "citations": {"passagesProvided": len({pid for s in analysis for pid in s.passage_ids} | {pid for s in analysis for l in s.leaves() for pid in l.passage_ids}),
                      "passagesCited": len(cited_passages), "sourcesCited": len(cited_sources), "citedPassageIds": cited_passages},
        "unsupportedNumbers": sum(len(d.unsupported_numbers) for d in good) + len(exec_unsupported),
        "numberPolicy": {
            "calculatedLabelled": [c for d in good for c in d.calculated_numbers] + exec_policy.calculated,
            "sentencesRemoved": [r for d in good for r in d.removed_sentences] + exec_policy.removed,
            "numbersRewritten": [r for d in good for r in d.rewritten_numbers] + list(exec_policy.rewritten),
            "unresolvedTableValues": [t for d in good for t in d.unsupported_numbers] + exec_unsupported,
        },
        "sectionBodyTransform": section_transform_log,
        "editorial": editorial.to_dict(),
        "evidenceCheck": {"removedClaims": [a for a in evidence_removed if a.get("action") != "rewritten"],
                          "rewrittenClaims": [a for a in evidence_removed if a.get("action") == "rewritten"],
                          "stillFlagged": [{"claim": str(g.get("claim") or "")[:200],
                                            "values": list(g.get("unmatchedNumericClaims") or [])[:6]}
                                           for g in evidence_flagged],
                          # report.py flags the fact sheet overruled (unit read from a table header etc.).
                          "factSheetOverrides": list({o["claim"]: o for o in fact_overrides}.values())},
        "layout": {k: (sorted(v) if isinstance(v, set) else v) for k, v in layout.items() if k != "scorecard"},
        "chartTextMismatches": chart_text_mismatches,
        "failedSections": failed, "words": words, "estimatedPages": est_pages,
        "entitySearch": entity_search_stats or {},
        "evidenceSupportedPages": round(outline.meta.get("evidenceSupportedWords", 0) / WORDS_PER_PAGE, 1),
    }
    log.info("V2 pipeline: sections=%d/%d words=%d charts=%d calls=%d tokens_in=%d tokens_out=%d est_pages=%.1f in %.1fs",
             len(good), len(drafts), words, len(charts_out), gateway.stats.calls, gateway.stats.tokens_in,
             gateway.stats.tokens_out, est_pages, meta["elapsedSeconds"])
    raw = json.dumps({
        "title": outline.title or question[:120],
        "report": report_md,
        "charts": charts_out,
        "images": [],
        "keyStats": [],
        "summary": re.sub(r"\[[^\]]*\]", "", exec_body.split("\n\n")[0])[:600] if exec_body else outline.thesis,
    }, ensure_ascii=False)
    return {"raw": raw, "model": "v2:" + ",".join(sorted(gateway.stats.models)) if gateway.stats.models else "v2", "meta": meta}
