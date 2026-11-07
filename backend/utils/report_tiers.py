"""Dynamic report-depth profiles shared by the planner, writer and renderers.

The three user-facing toggles are *generation profiles*, not fixed templates.
They control depth, evidence density, visual density, appendix tolerance and
whether generation may be modular.  The actual section names/order are chosen
by the report planner from the user's request and the evidence found.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from utils.chart_catalog import chart_density_contract


class ReportTier(str, Enum):
    BRIEF = "brief"
    DETAILED = "detailed"
    IN_DEPTH = "in_depth"


@dataclass(frozen=True)
class PageTarget:
    # Page ranges are metadata only. minimum/target are populated only for an
    # explicit user page request; the tier's maximum is a renderer warning cap.
    minimum: int | None
    target: int | None
    maximum: int


@dataclass(frozen=True)
class ReportTierSpec:
    tier: ReportTier
    label: str
    objective: str
    page_target: PageTarget
    research_breadth: str
    evidence_intensity: str
    allow_cover: bool
    allow_toc: bool
    allow_appendix: bool
    allow_scenarios: bool
    allow_risk_matrix: bool
    modular_generation: bool
    chunked_generation: bool
    chunk_size_sections: int
    max_output_tokens: int
    paragraph_style: str
    table_depth: str
    citation_density: str
    component_guidance: tuple[str, ...]


BRIEF_SPEC = ReportTierSpec(
    tier=ReportTier.BRIEF,
    label="Brief Report",
    objective="High-level, decision-oriented executive overview for rapid decision-making.",
    page_target=PageTarget(None, None, 8),
    research_breadth="focused",
    evidence_intensity="selective",
    allow_cover=True,
    allow_toc=False,
    allow_appendix=True,
    allow_scenarios=False,
    allow_risk_matrix=True,
    modular_generation=False,
    chunked_generation=False,
    chunk_size_sections=4,
    max_output_tokens=12288,
    paragraph_style="concise",
    table_depth="compact",
    citation_density="material_claims",
    component_guidance=(
        "Keep the report concise and substantive; use page count only as a final-rendering measurement, never as a content target.",
        "Prioritize the findings, KPIs, comparisons and risks that materially answer the request rather than repeating background.",
        "An executive summary, KPI treatment, market/financial view, or risk/action section may be used when the evidence and question justify it.",
        "Prefer compact visual blocks, short single-idea paragraphs, bullets, and small evidence-backed tables/charts over long prose blocks.",
        "When the evidence contains several distinct findings, drivers, comparisons, or KPIs, surface them as scannable points or compact visual structures instead of stacking paragraphs.",
    ),
)

DETAILED_SPEC = ReportTierSpec(
    tier=ReportTier.DETAILED,
    label="In-Detail Report",
    objective="Comprehensive business and technical analysis for managers and analysts.",
    page_target=PageTarget(None, None, 30),
    research_breadth="expanded",
    evidence_intensity="broad",
    allow_cover=True,
    allow_toc=True,
    allow_appendix=True,
    allow_scenarios=True,
    allow_risk_matrix=True,
    modular_generation=True,
    chunked_generation=True,
    chunk_size_sections=3,
    max_output_tokens=65536,
    paragraph_style="analytical",
    table_depth="full",
    citation_density="claim_level",
    component_guidance=(
        "Preserve the core question coverage a Brief would answer and add material analytical depth rather than replacing it with a narrower report.",
        "Add source-backed detail, comparisons, historical evidence, implementation implications, alternative explanations and forward-looking assumptions that materially answer the request.",
        "Use detailed KPI benchmarking, tables, multi-variable charts, structured risk analysis, comparison blocks and appendices when the evidence supports them; favor mixed section treatments over long uninterrupted prose.",
        "Keep analytical paragraphs short and single-purpose; use bullets/callouts/tables/charts to expose distinct findings rather than burying them in prose.",
        "The exact section names and order remain request-driven rather than template-driven.",
    ),
)

DEEP_SPEC = ReportTierSpec(
    tier=ReportTier.IN_DEPTH,
    label="Deep Research Report",
    objective="Institutional-grade, exhaustive research paper or prospectus-level analysis.",
    page_target=PageTarget(None, None, 100),
    research_breadth="comprehensive",
    evidence_intensity="exhaustive",
    allow_cover=True,
    allow_toc=True,
    allow_appendix=True,
    allow_scenarios=True,
    allow_risk_matrix=True,
    modular_generation=True,
    chunked_generation=True,
    chunk_size_sections=2,
    max_output_tokens=65536,
    paragraph_style="institutional",
    table_depth="granular",
    citation_density="claim_level",
    component_guidance=(
        "Seek broader, relevant evidence and verify key claims through triangulation; stop when the evidence no longer supports distinct analysis.",
        "Preserve the core coverage from a shorter report and extend it with granular historical/segment/entity analysis, alternatives, contradictions, evidence gaps and scenario modeling where the research supports it.",
        "Formal document control, cover/TOC, macro or industry context, fundamentals, scenarios, regulatory/risk, competitor benchmarking and detailed appendices are available tools, not mandatory sections.",
        "Generate modularly so large reports can be assembled from independent analytical sections without repetition; consider Predictive Scenario Modeling and a Data Dictionary & Methodology section when the evidence warrants them.",
        "Deep Research should increase evidence density and analytical variety, not paragraph count: use short narrative bridges plus tables, comparisons, timelines, scenario/risk structures, charts and callouts wherever they make the evidence easier to inspect.",
    ),
)

REPORT_TIER_SPECS = {s.tier.value: s for s in (BRIEF_SPEC, DETAILED_SPEC, DEEP_SPEC)}


def requested_report_page_count(question: str = "", supplied: object = None) -> int | None:
    """Resolve an explicit page-count request without treating incidental numbers as targets.

    A report tier is normally chosen in the UI. An explicit phrasing such as
    "100-page report" is different: it is a clear delivery constraint and should
    override a stale picker. Only 6–100 pages are accepted here because those
    bounds fit the supported renderer/report tiers.
    """
    if supplied not in (None, ""):
        try:
            value = int(str(supplied).strip())
        except (TypeError, ValueError):
            value = 0
        if 6 <= value <= 100:
            return value
    text = str(question or "")
    patterns = (
        r"\b(?:about|around|approximately|roughly|exactly)?\s*(\d{1,3})\s*[- ]?page(?:s)?\s+(?:report|study|analysis|research|paper)\b",
        r"\b(?:report|study|analysis|research|paper)\s+(?:(?:of|with)\s+)?(?:about|around|approximately|roughly|exactly)?\s*(\d{1,3})\s*[- ]?pages?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 6 <= value <= 100:
                return value
    return None


def get_report_tier_spec(tier: str | None) -> ReportTierSpec:
    return REPORT_TIER_SPECS.get(str(tier or "detailed").lower(), DETAILED_SPEC)


def report_tier_contract(tier: str | None) -> str:
    spec = get_report_tier_spec(tier)
    guidance = "\n".join(f"- {item}" for item in spec.component_guidance)
    maximum = spec.page_target.maximum
    page_guidance = (
        "An explicit user page-count request may be used as a bounded delivery constraint."
        if spec.page_target.target is not None else
        f"Use {maximum} pages only as a renderer-measured upper warning threshold."
    )
    source_guidance = {
        "brief": "Prefer 8–12 distinct relevant sources where available.",
        "detailed": "Prefer 20–30 distinct relevant sources, with meaningful publisher-page extraction rather than search snippets alone.",
        "in_depth": "Prefer 35–50 distinct relevant sources and broader same-site crawling where available; prioritize independent, authoritative sources and triangulate key claims.",
    }.get(spec.tier.value, "Use relevant sources appropriate to the request.")
    return f"""SELECTED REPORT TIER: {spec.label}
Tier ID: {spec.tier.value}
Objective: {spec.objective}
Research breadth: {spec.research_breadth}; evidence intensity: {spec.evidence_intensity}
PAGINATION: {page_guidance} Do not target a page, word, or character count. Let the request and verified evidence determine report length; never pad or invent material.
SOURCE COVERAGE: {source_guidance} Do not keep irrelevant pages merely to meet a source count.
Structure: choose the actual headings, order, hierarchy and blocks from this request and the evidence. No fixed section template or minimum section count is required.
Quantitative callouts: include only material, source-supported values; do not invent a KPI to fill space.
Visualizations: include when they communicate evidence more clearly; omit visuals whose data cannot be verified.
{chart_density_contract(spec.tier.value)}
Cover may be used: {spec.allow_cover}; TOC may be used: {spec.allow_toc}; appendix may be used: {spec.allow_appendix}; scenarios may be used: {spec.allow_scenarios}; risk matrix may be used: {spec.allow_risk_matrix}
Generation: {'modular/chunked-capable' if spec.chunked_generation else 'single-pass'}
Writing style: {spec.paragraph_style}; table depth: {spec.table_depth}; citation density: {spec.citation_density}

DYNAMIC STRUCTURE GUIDANCE:
{guidance}

TIER RULES:
- The selected tier controls research breadth, evidence density and analytical depth; it is NOT a fixed section template.
- Choose the actual section names, order, hierarchy and component mix from THIS request and THIS evidence.
- Brief gives the concise core answer; In-Detail retains that core coverage and develops it materially further; Deep Research widens the source base and adds granular analysis and triangulation.
- Page count is a renderer measurement and warning cap, not a content target. Follow an explicit page request only when present and supported by distinct evidence; never pad.
- Use only evidence-supported charts and tables; state evidence gaps when information is unavailable.
- Use [S1], [S2], etc. inline for citations.
- The renderer owns the canonical Data Sources appendix. Do NOT create a duplicate standalone Sources list.
- For Deep Research, use modular/chunked generation when needed to cover distinct evidence angles; modules must add distinct coverage rather than repeat one another."""
