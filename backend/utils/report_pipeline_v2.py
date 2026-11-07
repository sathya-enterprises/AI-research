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

MAX_CHARTS = {"detailed": 10, "in_depth": 36}
CHARTS_PER_SECTION = {"detailed": 2, "in_depth": 3}


class PipelineV2Error(RuntimeError):
    pass


def enabled() -> bool:
    return os.getenv("REPORT_PIPELINE_V2", "0").strip().lower() in {"1", "true", "yes", "on"}


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
        f"This report answers: *{question}*. {window_note}".strip(),
        "",
        f"**Evidence base.** {len(manifest)} sources were retained after relevance and quality screening "
        f"({', '.join(f'{n} {t.replace(chr(95), chr(32))}' for t, n in tiers.most_common())}); {dated} carry a publication date. "
        f"They were split into {len(store.passages)} evidence passages ({', '.join(f'{n} {k.replace(chr(95), chr(32))}' for k, n in kinds.most_common())}). "
        f"{len(cited_sources)} sources are cited in the analysis.",
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
    if outline.entity_coverage:
        covered = [r for r in outline.entity_coverage if r["status"] == "covered"]
        lines += ["", f"**Coverage.** {len(covered)} of {len(outline.entity_coverage)} identified entities had enough evidence "
                      f"for dedicated analysis."]
    if outline.decisions or failed:
        lines += ["", "**Scope decisions and limitations.**"]
        lines += [f"- {d}" for d in outline.decisions[:20]]
        lines += [f"- Section '{f}' could not be generated and is omitted." for f in failed]
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


def appendix_data_markdown(store, used_passages: set[str], max_tables: int = 10, max_rows: int = 30) -> str:
    from utils.section_charts import merge_continuations

    tables = merge_continuations([p for p in store.passages if p.kind in {"table", "calculated"} and p.passage_id in used_passages])
    if not tables:
        return ""
    out = ["Data tables used in the analysis, reproduced from the cited evidence passages."]
    seen_headings: set[str] = set()
    shown = 0
    for p in tables:
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
        shown += 1
        ids = ", ".join(getattr(p, "merged_ids", None) or [p.passage_id])
        out += ["", f"### {heading} [{ids}]", "", "| " + rows[0].strip().strip("|") + " |",
                "| " + " | ".join(["---"] * width) + " |"]
        out += ["| " + r.strip().strip("|") + " |" for r in rows[1:] if len(r.split("|")) == width]
    return "\n".join(out)


async def run_pipeline_v2(*, runtime, question: str, tier: str, store, manifest: list[dict], today: str,
                          window_note: str = "", progress=None, gateway=None, tiers: dict | None = None,
                          chart_verifier=None, entity_search_stats: dict | None = None) -> dict:
    started = time.perf_counter()
    if store is None or len(store.passages) < 10:
        raise PipelineV2Error(f"evidence store too small ({0 if store is None else len(store.passages)} passages)")
    gateway = gateway or LLMGateway(runtime, tier)
    if tiers is None and tier == "in_depth":
        from utils.entity_tiers import entity_tiers
        tiers = entity_tiers(store, tier=tier)
    try:
        outline = await build_outline(store, gateway, question=question, tier=tier, today=today, window_note=window_note,
                                      tiers=tiers)
    except OutlineError as exc:
        raise PipelineV2Error(str(exc)) from exc
    analysis = outline.analysis_sections()
    if len(analysis) < 3:
        raise PipelineV2Error(f"only {len(analysis)} analysis sections have enough evidence")

    # Charts: build from stored data, assign to sections, number in section order.
    try:
        from datetime import date as _date
        from utils.source_quality import window_start
        min_year = window_start(question, _date.fromisoformat(today[:10])).year
    except Exception:
        min_year = None
    candidates, chart_drops = build_chart_candidates(store, max_charts=MAX_CHARTS.get(tier, 10) * 2, min_year=min_year)
    # Run the same chart verification the export gates use BEFORE writing, so the
    # writer only interprets charts that will survive to the PDF.
    if chart_verifier and candidates:
        try:
            reasons = chart_verifier([dict(c.spec, evidencePassages=c.passage_ids) for c in candidates])
        except Exception as exc:  # verification problems must not abort the report
            log.warning("V2 chart pre-verification failed: %s", type(exc).__name__)
            reasons = [""] * len(candidates)
        kept = []
        for chart, reason in zip(candidates, reasons):
            if reason:
                chart.status, chart.drop_reason = "dropped", f"failed chart verification before writing: {reason}"
                chart_drops.append({"chart": chart.chart_id, "title": chart.spec.get("title"),
                                    "passage": ",".join(chart.passage_ids), "reason": chart.drop_reason})
            else:
                kept.append(chart)
        candidates = kept
    assign_charts(candidates, analysis, per_section=CHARTS_PER_SECTION.get(tier, 2))
    planned = [c for c in candidates if c.section_id]
    figure_no = 0
    for sec in analysis:
        for chart in [c for c in planned if c.section_id == sec.section_id][:CHARTS_PER_SECTION.get(tier, 2)]:
            figure_no += 1
            chart.figure_number = figure_no
    planned = [c for c in planned if c.figure_number][: MAX_CHARTS.get(tier, 10)]

    # Sections in waves: each wave sees summaries of completed sections.
    wave_size = max(1, int(os.getenv("GG_V2_WAVE_SIZE", "3") or 3))
    drafts = []
    goals = {s.section_id: f"{s.title}: {s.goal}" for s in analysis}
    import asyncio
    for start in range(0, len(analysis), wave_size):
        wave = analysis[start:start + wave_size]
        covered = [_covered_line(d) for d in drafts if d.ok]
        if progress:
            progress(f"Writing sections {start + 1}-{start + len(wave)} of {len(analysis)}…")
        results = await asyncio.gather(*[
            write_section(gateway, store, sec, [c for c in planned if c.section_id == sec.section_id],
                          question=question, thesis=outline.thesis, already_covered=covered,
                          other_sections=[g for sid, g in goals.items() if sid != sec.section_id])
            for sec in wave
        ])
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
                   f"SECTION SUMMARIES:\n{summaries}")
    exec_result = await gateway.generate(purpose="executive_summary", section_id="exec", system=EXEC_SYSTEM,
                                         prompt=exec_prompt, max_tokens=4096, temperature=0.2, timeout=120)
    exec_body = (exec_result.text.split(SUMMARY_MARKER)[0].strip() if exec_result.ok else "")
    exec_body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", exec_body).strip()
    summary_passages = {t.strip().upper() for d in good for g in CITE_RE.findall(d.summary) for t in re.split(r"\s*[,;]\s*", g)}
    exec_body = CITE_RE.sub(lambda m: "[" + ", ".join(t for t in re.split(r"\s*[,;]\s*", m.group(1)) if t.strip().upper() in summary_passages) + "]", exec_body)
    exec_body = exec_body.replace("[]", "")
    exec_policy = apply_number_policy(
        exec_body, passage_texts={pid: store.get(pid).text for pid in summary_passages if store.get(pid)},
        allowed_numbers=_number_set(d.summary for d in good), section_id="exec")
    exec_body = exec_policy.body
    exec_unsupported = unsupported_numbers(exec_body, _number_set(d.summary for d in good))

    # Figure renumbering in document order (dropped figures leave no gaps).
    used = [c for c in planned if c.status == "referenced"]
    used.sort(key=lambda c: c.figure_number)
    mapping = {c.figure_number: i for i, c in enumerate(used, 1)}
    charts_out = []
    for c in used:
        c.claim = _renumber_figures(c.claim, mapping)  # the claim must use the final figure number
        c.figure_number = mapping[c.figure_number]
        spec = dict(c.spec)
        spec["title"] = f"Figure {c.figure_number}: {spec.get('title')}"
        spec["takeaway"] = c.claim
        spec["rationale"] = f"Supports: {c.claim}"[:400] if c.claim else ""
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
    edited, editorial = await editorial_pass(edited, gateway=gateway)
    parts += [f"## {e.title}\n\n{e.body}" for e in edited]
    method = methodology_markdown(store=store, manifest=manifest, outline=outline, charts=candidates, drafts=good,
                                  gateway_stats=gateway.stats.to_dict(), question=question, window_note=window_note, failed=failed)
    parts.append(f"## Methodology and Evidence Base\n\n{method}")
    if tier == "in_depth":
        parts.append(f"## Appendix A — Source Reliability\n\n{appendix_sources_markdown(manifest, cited_sources)}")
        data_md = appendix_data_markdown(store, data_passages)
        if data_md:
            parts.append(f"## Appendix B — Data Tables\n\n{data_md}")
    report_md = STRAY_LABEL_RE.sub("", "\n\n".join(parts))
    report_md = FIGURE_MARKER_RE.sub(lambda m: f"[CHART_{int(m.group(1))}]" if 1 <= int(m.group(1)) <= len(charts_out) else "", report_md)
    report_md = _source_level(report_md)
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
        "citations": {"passagesProvided": len({pid for s in analysis for pid in s.passage_ids} | {pid for s in analysis for l in s.leaves() for pid in l.passage_ids}),
                      "passagesCited": len(cited_passages), "sourcesCited": len(cited_sources), "citedPassageIds": cited_passages},
        "unsupportedNumbers": sum(len(d.unsupported_numbers) for d in good) + len(exec_unsupported),
        "numberPolicy": {
            "calculatedLabelled": [c for d in good for c in d.calculated_numbers] + exec_policy.calculated,
            "sentencesRemoved": [r for d in good for r in d.removed_sentences] + exec_policy.removed,
            "unresolvedTableValues": [t for d in good for t in d.unsupported_numbers] + exec_unsupported,
        },
        "editorial": editorial.to_dict(),
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
