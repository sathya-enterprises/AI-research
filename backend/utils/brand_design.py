"""Growth Gradual brand design system + deterministic visual direction.

Two layers:

1. BRAND FOUNDATIONS (fixed, shared by every report) — the identity:
   typography, neutrals, page geometry, print-safe defaults. These never
   vary between reports, so every export reads as Growth Gradual.

2. APPROVED ACCENT FAMILIES + RESOLUTION — the finite, curated set of
   accent palettes a report may be art-directed into (gold, teal, indigo,
   green, muted_red, amber, slate). A report's palette is resolved
   DETERMINISTICALLY: the primary accent family is chosen from a stable
   hash of the report's own research question/title — never a
   domain->palette rule like "banking is gold" — and the presentation
   treatments (density, section/chart/table/callout treatment, opening,
   ending, source style) are derived from the report's actual structural
   characteristics (section mix, chart/table counts, emphasis signals).
   Same evidence in => same design out; nothing here uses randomness.

This module is intentionally standalone (stdlib only). Renderers and the
planning layer all consume the same ``resolve_report_visual`` result, so the
browser HTML path and the Chromium PDF path agree on one resolved identity.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Brand foundations (fixed)
# ---------------------------------------------------------------------------

BRAND_NAVY = "#202522"
BRAND_INK = "#202522"
BRAND_INK_SOFT = "#4D5751"
BRAND_PAPER = "#FCFBF8"
BRAND_PAPER_ALT = "#F3F4F0"
BRAND_RULE = "#D9DDD7"
BRAND_MUTED = "#5A6A7E"       # captions, meta, footnotes
BRAND_SECONDARY = "#56635B"

BRAND_POSITIVE = "#157A4D"    # good/direction-up semantics
BRAND_NEGATIVE = "#B42318"    # bad/downw/risk semantics
BRAND_WARNING = "#9A6818"

BRAND_CHART_PALETTE = ("#16734A", "#56635B", "#929B93", "#B98532", "#A94B43")

# Public, renderer-neutral token registry.  Keep this stable: renderers may
# map these tokens to CSS/PDF primitives, but reports must not invent tokens.
BRAND_TOKENS: Dict[str, Any] = {
    "typography_family": FONT_STACK if "FONT_STACK" in globals() else "system-ui, sans-serif",
    "heading_family": FONT_STACK_HEADING if "FONT_STACK_HEADING" in globals() else "system-ui, sans-serif",
    "text": BRAND_INK,
    "text_muted": BRAND_MUTED,
    "navy": BRAND_NAVY,
    "charcoal": BRAND_INK,
    "paper": BRAND_PAPER,
    "paper_alt": BRAND_PAPER_ALT,
    "border": BRAND_RULE,
    "spacing": {"xs": "4px", "sm": "8px", "md": "14px", "lg": "22px", "xl": "32px"},
    "radius": {"sharp": "0px", "soft": "8px", "rounded": "16px"},
    "footer": "Growth Gradual | page counter",
    "print": {"page_size": PAGE_SIZE if "PAGE_SIZE" in globals() else "A4", "margin": PAGE_MARGIN_MM if "PAGE_MARGIN_MM" in globals() else "13mm 12mm 17mm 12mm"},
}

# Print-safe brand typography. System stacks keep Chromium rendering identical
# to the browser without network font fetches inside the PDF path.
FONT_STACK = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
FONT_STACK_HEADING = FONT_STACK

PAGE_SIZE = "A4"
PAGE_MARGIN_MM = "13mm 12mm 17mm 12mm"
CONTENT_MAX_PX = 1120

# Rebind the public token registry after all print-safe constants exist.
BRAND_TOKENS.update({
    "typography_family": FONT_STACK,
    "heading_family": FONT_STACK_HEADING,
    "print": {"page_size": PAGE_SIZE, "margin": PAGE_MARGIN_MM},
})

# ---------------------------------------------------------------------------
# Approved accent families (controlled, accredited)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccentFamily:
    """One accredited accent family. All hex values are hand-picked to stay
    inside the Growth Gradual brand while giving each report a distinct hue.

    ``accent`` is the primary accent color used for eyebrow labels, rules
    and emphasis; ``secondary`` is the pairing tone; ``deep`` is the darker
    primary used for table headers / metric values / section headings;
    ``soft`` is the light tint used for panels and alt surfaces;
    ``on_accent`` is the foreground that keeps readable contrast when text
    sits on ``accent``.
    """

    key: str
    accent: str
    secondary: str
    deep: str
    soft: str
    on_accent: str
    chart: Tuple[str, ...]
    positive: str = BRAND_POSITIVE
    negative: str = BRAND_NEGATIVE
    warning: str = BRAND_WARNING
    on_soft: str = BRAND_INK

    def palette(self) -> List[str]:
        return list(self.chart)

    def semantic_colors(self) -> Dict[str, str]:
        return {"positive": self.positive, "negative": self.negative, "warning": self.warning,
                "on_accent": self.on_accent, "on_soft": self.on_soft}


ACCENT_FAMILIES: Dict[str, AccentFamily] = {
    "gold": AccentFamily(
        key="gold",
        accent="#A16207", secondary="#D97706", deep="#713F12", soft="#FBF3DF", on_accent="#FFFFFF",
        chart=("#A16207", "#D97706", "#F0B429", "#8A6416", "#4E3B12", "#C79A3E", "#E8C15A", "#7A5A10"),
    ),
    "teal": AccentFamily(
        key="teal",
        accent="#0F766E", secondary="#0E8D84", deep="#11555A", soft="#E6F6F3", on_accent="#FFFFFF",
        chart=("#0F766E", "#12A696", "#2DD4BF", "#155A63", "#0E5056", "#3BA989", "#5EBFA9", "#15857E"),
    ),
    "indigo": AccentFamily(
        key="indigo",
        accent="#3730A3", secondary="#4F46E5", deep="#2A2A7E", soft="#EDF0FC", on_accent="#FFFFFF",
        chart=("#3730A3", "#6366F1", "#7D83F4", "#3B3B98", "#27288A", "#5A5FCE", "#7C82E0", "#443BC3"),
    ),
    "green": AccentFamily(
        key="green",
        accent="#15803D", secondary="#16A34A", deep="#14532D", soft="#EAF7EF", on_accent="#FFFFFF",
        chart=("#15803D", "#22C55E", "#4ADE80", "#1E7A45", "#15703A", "#3DA965", "#6ABF8A", "#1F9D57"),
    ),
    "muted_red": AccentFamily(
        key="muted_red",
        accent="#9F1239", secondary="#BE123C", deep="#7F1B33", soft="#FBEAEF", on_accent="#FFFFFF",
        chart=("#9F1239", "#E11D48", "#FB7185", "#7F1D2B", "#97104A", "#C0405F", "#E88CA0", "#B00E3A"),
    ),
    "amber": AccentFamily(
        key="amber",
        accent="#92400E", secondary="#B45309", deep="#78350F", soft="#FBF0DC", on_accent="#FFFFFF",
        chart=("#92400E", "#D97706", "#F59E0B", "#7C4A12", "#6B3E05", "#C98626", "#E0A94E", "#A85A0C"),
    ),
    "slate": AccentFamily(
        key="slate",
        accent="#334155", secondary="#475569", deep="#1E293B", soft="#F0F4F8", on_accent="#FFFFFF",
        chart=("#334155", "#64748B", "#94A3B8", "#1E293B", "#475569", "#CDD6E2", "#0F172A", "#7C8BA1"),
    ),
}

FAMILY_KEYS: Tuple[str, ...] = tuple(sorted(ACCENT_FAMILIES.keys()))

# Canonical semantic sets the resolver accepts. Kept in one place so the
# schema enums, the planner prompt, and the renderers all stay in sync.
VISUAL_DIRECTIONS: Tuple[str, ...] = (
    "formal",        # restrained analyst register — tables, hairline rules
    "editorial",     # magazine storytelling — accent edges, pull quotes
    "data_forward",  # charts/metrics lead; tinted containers
    "sector_brief",  # clean business briefing — accent bars, framed visuals
    "flagship",      # bold premium — panel sections, banded background
    "quiet",         # minimal, neutral — flat surfaces, no decoration
)
SECTION_TREATMENTS: Tuple[str, ...] = ("plain", "ruled", "panel", "card", "editorial", "accented", "full_width", "banded")
CHART_TREATMENTS: Tuple[str, ...] = ("minimal", "plain", "open", "framed", "tinted", "inset", "hero", "dominant", "full_width", "inline", "compact_supporting")
TABLE_TREATMENTS: Tuple[str, ...] = ("plain", "striped", "clean", "compact", "emphasized", "minimal", "framed", "comparison_matrix", "scenario_matrix")
CALLOUT_TREATMENTS: Tuple[str, ...] = ("plain", "bordered", "accent_edge", "filled", "quote", "evidence_bar", "framed_insight", "warning", "neutral")
IMAGE_TREATMENTS: Tuple[str, ...] = ("plain", "framed", "rounded", "full_bleed")
OPENING_STYLES: Tuple[str, ...] = ("none", "cover", "condensed_cover", "summary_first", "data_first", "thesis_first", "narrative")
ENDING_STYLES: Tuple[str, ...] = ("none", "sources_only", "summary_and_sources", "outlook", "synthesis", "scenario_outlook", "monitoring_framework", "concise_close", "source_led")
SOURCE_STYLES: Tuple[str, ...] = ("none", "inline", "appendix", "compact", "two_column", "grouped", "integrated")
# Composition is a grammar, not a report/industry template.  These names are
# intentionally about evidence character so the same grammar can be reused by
# unrelated questions with the same analytical shape.
COMPOSITION_MODES: Tuple[str, ...] = (
    "comparison_led", "matrix_led", "metric_led", "driver_chain_led",
    "chart_led", "trajectory_led", "capital_stack", "cycle_led",
    "thesis_led", "evidence_led", "narrative_led", "mixed_analytical",
)
OPENING_MODES: Tuple[str, ...] = (
    "analytical", "kpi_strip", "comparison", "thesis", "evidence", "chart", "narrative", "none",
)
RHYTHMS: Tuple[str, ...] = (
    "dense_analytical", "compact", "spacious", "editorial", "alternating",
    "metric_pulse", "visual_breathing", "longform_analysis", "chart_interleave",
    "matrix_sequence", "evidence_dense", "narrative_flow",
)
WHITESPACE_PROFILES: Tuple[str, ...] = ("tight", "balanced", "generous")
EMPHASIS_PROFILES: Tuple[str, ...] = ("charts", "tables", "metrics", "comparisons", "evidence", "narrative")

DENSITIES: Tuple[str, ...] = ("compact", "balanced", "airy")
TYPEOGRAPHIES: Tuple[str, ...] = ("compact", "balanced", "dramatic")

_ACCENT_RE = re.compile(r"^[a-z_]{1,24}$")


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def _seed(*parts: Any) -> int:
    digest = hashlib.blake2s(digest_size=8)
    for part in parts:
        if part is None:
            digest.update(b"\x00")
        else:
            digest.update(str(part).encode("utf-8", "replace"))
            digest.update(b"\x1f")
    return int.from_bytes(digest.digest(), "big")


def _pick(values: Sequence[str], seed: int) -> str:
    """Deterministic, uniform pick from a tuple of allowed values."""
    if not values:
        return ""
    return values[seed % len(values)]


def _valid(value: Any, allowed: Sequence[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in allowed else None


def family_for(key: Optional[str]) -> Optional[AccentFamily]:
    if not key:
        return None
    return ACCENT_FAMILIES.get(str(key).strip().lower())


def _primary_family(question: str, title: str, requested: Optional[str]) -> AccentFamily:
    fam = family_for(requested)
    return ACCENT_FAMILIES["green"] if fam is None or fam.key != "green" else fam


def _secondary_key(primary: AccentFamily, question: str, title: str, structure_signature: str, requested: Optional[str]) -> str:
    return "slate"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Structure:
    section_count: int
    chart_count: int
    table_count: int
    has_metrics: bool
    emphasis_signals: Tuple[str, ...]
    section_types: Tuple[str, ...]
    source_count: int
    cover_enabled: bool
    has_summary: bool
    summary_at_end: bool
    question: str = ""
    title: str = ""
    comparison_count: int = 0
    time_series_density: float = 0.0
    company_focus: bool = False
    management_analyst: bool = False
    risk_count: int = 0
    financial_metric_density: int = 0
    qualitative_ratio: float = 0.5
    asset_count: int = 0
    unit_economics: bool = False
    project_economics: bool = False


def _composition_for(s: _Structure, semantic: Dict[str, Any]) -> str:
    requested = _valid(semantic.get("composition_mode"), COMPOSITION_MODES)
    # The schema's neutral fallback is narrative_led. Let strong evidence
    # signals override that fallback; an explicit non-neutral planner choice
    # remains authoritative.
    if requested and requested != "narrative_led":
        return requested
    text = f"{s.question} {s.title}".lower()
    section_types = set(s.section_types)
    comparison_words = ("compare", "versus", " vs ", "peer", "benchmark", "historical")
    capital_words = ("project economics", "project returns", "financing", "wacc", "return on capital", "storage", "transmission")
    cycle_words = ("cycle", "cyclical", "inventory", "turning point", "supply changes")

    # Structural evidence signals lead. Text cues are tie-breakers only, so a
    # report with a generic title can still resolve from its actual evidence.
    if s.unit_economics and s.financial_metric_density >= 4 and s.chart_count >= 2:
        return "driver_chain_led"
    if s.project_economics and s.chart_count >= 2:
        return "capital_stack"
    if s.company_focus and s.management_analyst and s.financial_metric_density >= 4:
        return "trajectory_led"
    if s.comparison_count >= 2 and s.table_count >= 3:
        return "matrix_led"
    if s.time_series_density >= 0.55 and s.chart_count >= 2:
        return "chart_led"
    if s.risk_count >= 2 and s.qualitative_ratio >= 0.62:
        return "evidence_led"
    if s.comparison_count >= 1 and s.table_count >= 1:
        return "comparison_led"
    if s.has_metrics and s.financial_metric_density >= 6:
        return "metric_led"
    if any(w in text for w in cycle_words) and s.chart_count:
        return "cycle_led"
    if any(w in text for w in capital_words) and (s.chart_count or s.table_count):
        return "capital_stack"
    if any(w in text for w in comparison_words) and s.table_count:
        return "comparison_led"
    if s.chart_count == 1 and s.qualitative_ratio < 0.7:
        return "thesis_led"
    if s.qualitative_ratio >= 0.72:
        return "narrative_led"
    return "mixed_analytical"


def _opening_for_composition(mode: str, s: _Structure) -> str:
    if mode in ("comparison_led", "matrix_led"): return "comparison"
    if mode in ("metric_led", "trajectory_led") and s.has_metrics: return "kpi_strip"
    if mode in ("chart_led", "cycle_led") and s.chart_count: return "chart"
    if mode in ("capital_stack", "driver_chain_led", "thesis_led"): return "thesis"
    if mode == "evidence_led": return "evidence"
    if mode == "narrative_led": return "narrative"
    return "analytical"


def _rhythm_for(mode: str, s: _Structure) -> str:
    if mode in ("comparison_led",): return "dense_analytical"
    if mode == "matrix_led": return "matrix_sequence"
    if mode == "metric_led": return "metric_pulse"
    if mode == "trajectory_led": return "visual_breathing"
    if mode == "chart_led": return "chart_interleave"
    if mode == "cycle_led": return "alternating"
    if mode == "capital_stack": return "spacious"
    if mode == "driver_chain_led": return "compact"
    if mode == "evidence_led": return "evidence_dense"
    if mode == "thesis_led": return "editorial"
    if mode == "narrative_led": return "longform_analysis"
    return "narrative_flow"


def _emphasis_for(mode: str, s: _Structure) -> str:
    if mode in ("comparison_led", "matrix_led"): return "comparisons"
    if mode == "driver_chain_led": return "metrics"
    if mode == "chart_led": return "charts"
    if mode in ("metric_led", "trajectory_led"): return "metrics" if s.has_metrics else "charts"
    if mode == "capital_stack": return "charts" if s.chart_count else "tables"
    if mode == "evidence_led": return "evidence"
    if s.table_count > s.chart_count: return "tables"
    if s.chart_count: return "charts"
    return "narrative"


def _derive_direction(s: _Structure) -> str:
    visual_count = s.chart_count + s.table_count
    if s.chart_count >= 2 and s.chart_count >= s.table_count:
        return "data_forward"
    if s.table_count >= 2 and s.table_count >= s.chart_count and s.section_count >= 5:
        return "formal"
    if any(k in s.emphasis_signals for k in ("critical", "high")) or s.source_count >= 12:
        return "sector_brief"
    if visual_count == 0 and s.section_count <= 5 and not s.has_metrics:
        return "quiet"
    if s.section_count <= 5 and (s.chart_count or s.has_metrics):
        return "editorial"
    return "sector_brief"


def _derive_density(s: _Structure) -> str:
    total = s.section_count + s.chart_count + s.table_count
    heavy = total >= 12 or (s.chart_count + s.table_count) >= 8
    light = total <= 5 and s.section_count <= 5
    if heavy:
        return "compact"
    if light:
        return "airy"
    return "balanced"


def _preferred_layouts(section_types: Sequence[str]) -> List[str]:
    """Map planned section roles to layout-variant affordances when the model
    gave no per-section layouts. Pure suggestion; renderers keep honoring the
    concrete per-section layouts on the plan."""
    chosen: List[str] = []
    for st in section_types:
        st = str(st or "").strip().lower()
        if st in ("metrics_dashboard", "comparison", "financials"):
            hint = "grid" if st == "metrics_dashboard" else "two_column"
        elif st in ("timeline", "risk_assessment"):
            hint = "grid" if st == "risk_assessment" else "single_column"
        elif st in ("compliance", "methodology"):
            hint = "sidebar_main"
        else:
            hint = "single_column"
        if hint not in chosen:
            chosen.append(hint)
    return chosen or ["single_column"]


def resolve_report_visual(
    semantic: Optional[Dict[str, Any]] = None,
    *,
    question: str = "",
    title: str = "",
    section_count: int = 0,
    chart_count: int = 0,
    table_count: int = 0,
    has_metrics: bool = False,
    emphasis_signals: Sequence[str] = (),
    section_types: Sequence[str] = (),
    source_count: int = 0,
    existing_visual: Optional[Dict[str, Any]] = None,
    cover_enabled: Optional[bool] = None,
    has_summary: bool = False,
    summary_at_end: bool = False,
    comparison_count: int = 0,
    time_series_density: float = 0.0,
    company_focus: bool = False,
    management_analyst: bool = False,
    risk_count: int = 0,
    financial_metric_density: int = 0,
    qualitative_ratio: float = 0.5,
    asset_count: int = 0,
    unit_economics: bool = False,
    project_economics: bool = False,
) -> Dict[str, Any]:
    """Resolve a report's complete, deterministic visual direction.

    ``semantic`` (``presentation.visual_spec``) is the planner's art-direction
    intent: accent families, treatments, etc. When a field is missing or
    invalid it is derived deterministically from the report's own identity
    (question/title hash) and structure. ``existing_visual`` (the legacy
    concrete 16-token ``presentation.visual``) is preserved verbatim ONLY when
    no semantic direction is supplied at all (back-compat with already-saved
    payloads); its chart palette is still resolved per-report.

    Returns a JSON-safe dict with:
      - "visual":         concrete 16-token visual spec (renderer contract)
      - "visual_spec":    semantic art-direction echo (renderer treatments)
      - "chart_palette":  ordered hex list for SVG chart series
      - "semantic_colors": positive/negative/warning hex map
    """
    semantic = semantic if isinstance(semantic, dict) else {}
    s = _Structure(
        section_count=int(section_count or 0),
        chart_count=int(chart_count or 0),
        table_count=int(table_count or 0),
        has_metrics=bool(has_metrics),
        emphasis_signals=tuple(str(x).strip().lower() for x in (emphasis_signals or ())),
        section_types=tuple(str(x) for x in (section_types or ())),
        source_count=int(source_count or 0),
        cover_enabled=bool(cover_enabled) if cover_enabled is not None else True,
        has_summary=bool(has_summary),
        summary_at_end=bool(summary_at_end),
        question=question or "",
        title=title or "",
        comparison_count=int(comparison_count or 0),
        time_series_density=float(time_series_density or 0.0),
        company_focus=bool(company_focus),
        management_analyst=bool(management_analyst),
        risk_count=int(risk_count or 0),
        financial_metric_density=int(financial_metric_density or 0),
        qualitative_ratio=max(0.0, min(1.0, float(qualitative_ratio or 0.0))),
        asset_count=int(asset_count or 0),
        unit_economics=bool(unit_economics),
        project_economics=bool(project_economics),
    )

    # -- Deterministic picks from the report's own identity ----------------
    primary = _primary_family(question, title, semantic.get("primary_accent"))
    structure_signature = "|".join(
        [
            str(s.section_count), str(s.chart_count), str(s.table_count),
            str(int(s.has_metrics)), ",".join(s.emphasis_signals),
            ",".join(sorted(set(s.section_types))), str(s.source_count),
        ]
    )
    secondary = ACCENT_FAMILIES[_secondary_key(primary, question, title, structure_signature, semantic.get("secondary_accent"))]

    direction = _valid(semantic.get("visual_direction"), VISUAL_DIRECTIONS) or _derive_direction(s)
    composition_mode = _composition_for(s, semantic)
    requested_opening = _valid(semantic.get("opening_mode"), OPENING_MODES)
    opening_mode = (requested_opening if requested_opening not in (None, "narrative") or composition_mode == "narrative_led"
                    else _opening_for_composition(composition_mode, s))
    rhythm = _valid(semantic.get("visual_rhythm"), RHYTHMS) or _rhythm_for(composition_mode, s)
    emphasis_profile = _valid(semantic.get("emphasis_profile"), EMPHASIS_PROFILES) or _emphasis_for(composition_mode, s)
    derived_density = "compact" if (s.financial_metric_density >= 18 or s.qualitative_ratio <= 0.35) else "airy" if s.qualitative_ratio >= 0.70 and s.financial_metric_density <= 15 else "balanced"
    density = _valid(semantic.get("density"), DENSITIES) or derived_density
    if semantic.get("density") in (None, "balanced"):
        density = derived_density
    whitespace = _valid(semantic.get("whitespace_profile"), WHITESPACE_PROFILES) or ("tight" if density == "compact" else "generous" if density == "airy" else "balanced")
    if semantic.get("whitespace_profile") in (None, "balanced"):
        whitespace = {"matrix_led": "tight", "comparison_led": "tight", "driver_chain_led": "tight", "capital_stack": "generous", "narrative_led": "generous", "thesis_led": "generous"}.get(composition_mode, whitespace)
    typography = _valid(semantic.get("typography_scale"), TYPEOGRAPHIES) or _typography_for(direction, s)
    shape = _valid(semantic.get("shape_style"), ("sharp", "soft", "rounded")) or _shape_for(direction, primary.key, s)
    strategy = _valid(semantic.get("accent_strategy"), ("single", "duotone", "contrast", "monochrome")) or _strategy_for(direction, s)
    chart_style = _valid(semantic.get("chart_style"), ("minimal", "editorial", "technical", "bold")) or _chart_style_for(direction, s)
    card_style = _valid(semantic.get("card_style"), ("flat", "outlined", "lifted", "filled")) or _card_style_for(direction, s)
    section_rule = _valid(semantic.get("section_rule"), ("none", "hairline", "accent_bar", "panel")) or _rule_for(direction, s)
    alignment = _valid(semantic.get("title_alignment"), ("left", "center")) or _align_for(direction, s)
    background = _valid(semantic.get("background_treatment"), ("plain", "tinted", "banded")) or _background_for(direction, s)

    # -- Treatments (the renderer-readable part of the semantic layer) -----
    section_treatment = _valid(semantic.get("section_treatment"), SECTION_TREATMENTS) or _section_treatment_for(direction, card_style, s)
    chart_treatment = _valid(semantic.get("chart_treatment"), CHART_TREATMENTS) or _chart_treatment_for(direction, card_style, background, s)
    table_treatment = _valid(semantic.get("table_treatment"), TABLE_TREATMENTS) or _table_treatment_for(direction, card_style, s)
    # Treat old neutral defaults as unspecified when evidence shape provides a
    # stronger macro grammar. Explicit non-neutral planner choices remain.
    if semantic.get("section_treatment") in (None, "ruled"):
        section_treatment = {
            "matrix_led": "banded", "driver_chain_led": "accented", "chart_led": "full_width",
            "capital_stack": "panel", "cycle_led": "banded", "trajectory_led": "accented",
            "thesis_led": "editorial", "evidence_led": "plain", "narrative_led": "plain",
        }.get(composition_mode, section_treatment)
    if semantic.get("chart_treatment") in (None, "framed", "minimal"):
        chart_treatment = {
            "chart_led": "hero", "cycle_led": "hero", "capital_stack": "hero",
            "driver_chain_led": "compact_supporting", "trajectory_led": "inset",
            "narrative_led": "open", "thesis_led": "dominant",
        }.get(composition_mode, chart_treatment)
    if semantic.get("table_treatment") in (None, "striped"):
        table_treatment = {"matrix_led": "comparison_matrix", "comparison_led": "comparison_matrix", "cycle_led": "scenario_matrix", "capital_stack": "scenario_matrix", "driver_chain_led": "compact"}.get(composition_mode, table_treatment)
    callout_treatment = _valid(semantic.get("callout_treatment"), CALLOUT_TREATMENTS) or _callout_treatment_for(direction, strategy, s)
    image_treatment = _valid(semantic.get("image_treatment"), IMAGE_TREATMENTS) or _image_treatment_for(direction, shape, card_style, s)
    opening_style = _valid(semantic.get("opening_style"), OPENING_STYLES) or _opening_for(direction, s)
    ending_style = _valid(semantic.get("ending_style"), ENDING_STYLES) or _ending_for(s)
    title_treatment = _valid(semantic.get("title_treatment"), ("minimal", "bold_banner", "classic", "data_driven")) or _title_treatment_for(direction, s)
    if not semantic.get("title_treatment") or semantic.get("title_treatment") == "minimal":
        if composition_mode == "metric_led": title_treatment = "data_driven"
        elif composition_mode == "comparison_led": title_treatment = "classic"
    source_style = _valid(semantic.get("source_style"), SOURCE_STYLES) or _source_style_for(s.source_count)

    preferred = isinstance(semantic.get("preferred_layouts"), list) and [str(x) for x in semantic["preferred_layouts"] if str(x).strip()]
    preferred = preferred or _preferred_layouts(s.section_types)

    visual_spec: Dict[str, Any] = {
        "visual_direction": direction,
        "composition_mode": composition_mode,
        "opening_mode": opening_mode,
        "visual_rhythm": rhythm,
        "whitespace_profile": whitespace,
        "emphasis_profile": emphasis_profile,
        "primary_accent": primary.key,
        "secondary_accent": secondary.key,
        "background_style": background,
        "density": density,
        "typography_scale": typography,
        "title_treatment": title_treatment,
        "section_treatment": section_treatment,
        "chart_treatment": chart_treatment,
        "table_treatment": table_treatment,
        "callout_treatment": callout_treatment,
        "image_treatment": image_treatment,
        "preferred_layouts": preferred,
        "opening_style": opening_style,
        "ending_style": ending_style,
        "source_style": source_style,
        "metrics_treatment": _valid(semantic.get("metrics_treatment"), ("cards", "inline", "plain_grid", "emphasized", "none")) or ("plain_grid" if density == "compact" else "cards"),
    }

    # -- Concrete tokens ---------------------------------------------------
    if not semantic and existing_visual:
        # Back-compat: honor a stored concrete visual as-is when the planner
        # supplied no semantic direction at all. Treatments/chart palette are
        # still resolved per-report so replays stay deterministic and dynamic.
        visual = _clean_existing_visual(existing_visual)
    else:
        visual = {
            "mode": "light",
            "primary_color": BRAND_NAVY,
            "secondary_color": BRAND_SECONDARY,
            "accent_color": primary.accent,
            "surface_color": BRAND_PAPER,
            "surface_alt_color": BRAND_PAPER_ALT,
            "text_color": BRAND_INK,
            "muted_color": BRAND_INK_SOFT,
            "border_color": BRAND_RULE,
            "typography_scale": typography,
            "shape_style": shape,
            "accent_strategy": strategy,
            "chart_style": chart_style,
            "spacing_scale": density,
            "card_style": card_style,
            "section_rule": section_rule,
            "title_alignment": alignment,
            "background_treatment": background,
        }

    return {
        "visual": visual,
        "visual_spec": visual_spec,
        "chart_palette": list(BRAND_CHART_PALETTE),
        "semantic_colors": {
            **primary.semantic_colors(),
        },
    }


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_VISUAL_ENUM_KEYS = (
    ("typography_scale", TYPEOGRAPHIES),
    ("mode", ("light", "dark", "editorial", "technical", "immersive", "split")),
    ("shape_style", ("sharp", "soft", "rounded")),
    ("accent_strategy", ("single", "duotone", "contrast", "monochrome")),
    ("chart_style", ("minimal", "editorial", "technical", "bold")),
    ("spacing_scale", DENSITIES),
    ("card_style", ("flat", "outlined", "lifted", "filled")),
    ("section_rule", ("none", "hairline", "accent_bar", "panel")),
    ("title_alignment", ("left", "center")),
    ("background_treatment", ("plain", "tinted", "banded")),
)
_VISUAL_HEX_KEYS = (
    "primary_color", "secondary_color", "accent_color", "surface_color",
    "surface_alt_color", "text_color", "muted_color", "border_color",
)


def _clean_existing_visual(existing_visual: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    raw = existing_visual if isinstance(existing_visual, dict) else {}
    for key in _VISUAL_HEX_KEYS:
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and _HEX_RE.match(value) else BRAND_PAPER if key == "surface_color" else BRAND_NAVY
    for key, allowed in _VISUAL_ENUM_KEYS:
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and value.strip().lower() in allowed else "light" if key == "mode" else _pick(allowed, _seed(key))
    if out.get("surface_alt_color") and out["surface_alt_color"].lower() == BRAND_PAPER.lower():
        out["surface_alt_color"] = BRAND_PAPER_ALT
    if out.get("muted_color") and out["muted_color"].lower() == BRAND_NAVY.lower():
        out["muted_color"] = BRAND_MUTED
    if out.get("border_color") and out["border_color"].lower() == BRAND_NAVY.lower():
        out["border_color"] = BRAND_RULE
    return out


def _chart_palette_from_hash(question: str, title: str) -> List[str]:
    fam = ACCENT_FAMILIES[FAMILY_KEYS[_seed(question, title) % len(FAMILY_KEYS)]]
    return list(fam.chart)


# -- Small deterministic derivations -----------------------------------------


def _typography_for(direction: str, s: _Structure) -> str:
    if direction in ("formal", "data_forward"):
        return "compact" if (s.chart_count + s.table_count) >= 5 else "balanced"
    if direction in ("flagship", "editorial") and s.section_count <= 5:
        return "dramatic"
    return "balanced"


def _shape_for(direction: str, family_key: str, s: _Structure) -> str:
    if direction == "formal":
        return "sharp"
    if direction == "flagship" or (family_key in ("gold", "amber") and direction != "quiet"):
        return "rounded"
    if direction == "quiet":
        return "soft"
    return "soft"


def _strategy_for(direction: str, s: _Structure) -> str:
    if direction == "formal" and s.table_count >= s.chart_count:
        return "single"
    if direction in ("data_forward", "flagship"):
        return "contrast"
    if direction == "quiet":
        return "monochrome"
    return "duotone"


def _chart_style_for(direction: str, s: _Structure) -> str:
    if direction == "formal" and s.chart_count >= 1:
        return "technical"
    if direction == "data_forward" and (s.chart_count + s.table_count) >= 4:
        return "editorial"
    if direction == "flagship":
        return "bold"
    if direction == "quiet":
        return "minimal"
    return "editorial"


def _card_style_for(direction: str, s: _Structure) -> str:
    if direction == "flagship" or (direction == "data_forward" and s.section_count <= 6):
        return "filled"
    if direction in ("formal", "quiet"):
        return "flat"
    return "outlined"


def _rule_for(direction: str, s: _Structure) -> str:
    if direction == "quiet":
        return "none"
    if direction == "flagship":
        return "panel"
    if direction in ("editorial", "sector_brief"):
        return "accent_bar"
    return "hairline"


def _align_for(direction: str, s: _Structure) -> str:
    if direction == "flagship":
        return "center"
    return "left"


def _background_for(direction: str, s: _Structure) -> str:
    if direction == "flagship":
        return "banded"
    if direction in ("data_forward", "sector_brief"):
        return "tinted"
    return "plain"


def _section_treatment_for(direction: str, card_style: str, s: _Structure) -> str:
    if direction == "flagship" or (card_style == "filled" and s.section_count <= 6):
        return "panel"
    if card_style == "flat":
        return "plain"
    return "ruled"


def _chart_treatment_for(direction: str, card_style: str, background: str, s: _Structure) -> str:
    if direction == "quiet":
        return "minimal"
    if card_style == "flat":
        return "plain"
    if card_style == "filled" or background == "tinted" or direction == "sector_brief":
        return "tinted"
    return "framed"


def _table_treatment_for(direction: str, card_style: str, s: _Structure) -> str:
    if direction == "quiet" or card_style == "flat":
        return "plain"
    if direction == "formal" and card_style in ("outlined", "filled"):
        return "framed"
    return "striped"


def _callout_treatment_for(direction: str, strategy: str, s: _Structure) -> str:
    if direction == "quiet":
        return "plain"
    if direction == "flagship":
        return "filled"
    if direction in ("editorial", "sector_brief", "data_forward") and strategy != "single":
        return "accent_edge"
    return "bordered"


def _image_treatment_for(direction: str, shape: str, card_style: str, s: _Structure) -> str:
    if direction in ("flagship", "editorial"):
        return "full_bleed"
    if shape == "rounded":
        return "rounded"
    if card_style == "flat":
        return "plain"
    return "framed"


def _opening_for(direction: str, s: _Structure) -> str:
    if not s.cover_enabled:
        if s.has_metrics:
            return "data_first"
        if s.has_summary:
            return "summary_first"
        return "none"
    if direction == "formal":
        return "condensed_cover"
    return "cover"


def _ending_for(s: _Structure) -> str:
    if s.summary_at_end:
        return "summary_and_sources" if s.source_count else "none"
    if any(st in ("recommendations", "outlook") for st in s.section_types):
        return "outlook"
    if s.source_count:
        return "sources_only"
    return "none"


def _source_style_for(source_count: int) -> str:
    if source_count <= 0:
        return "none"
    if source_count <= 3:
        return "inline"
    if source_count > 10:
        return "compact"
    return "appendix"


def _title_treatment_for(direction: str, s: _Structure) -> str:
    if direction == "flagship":
        return "bold_banner"
    if direction == "formal":
        return "classic"
    if s.has_metrics:
        return "data_driven"
    return "minimal"


def apply_semantic_defaults(semantic: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce an optional model semantic dict to a valid, complete visual_spec
    using neutral defaults (structure-independent). Renderers call this when
    no planner-resolved direction is attached to a payload, so every render
    still has well-formed treatment values."""
    if not isinstance(semantic, dict):
        semantic = {}

    def cv(key: str, allowed: Sequence[str], default: str) -> str:
        value = semantic.get(key)
        return value if isinstance(value, str) and value.strip().lower() in allowed else default

    return {
        "visual_direction": cv("visual_direction", VISUAL_DIRECTIONS, "sector_brief"),
        "composition_mode": cv("composition_mode", COMPOSITION_MODES, "narrative_led"),
        "opening_mode": cv("opening_mode", OPENING_MODES, "narrative"),
        "visual_rhythm": cv("visual_rhythm", RHYTHMS, "narrative_flow"),
        "whitespace_profile": cv("whitespace_profile", WHITESPACE_PROFILES, "balanced"),
        "emphasis_profile": cv("emphasis_profile", EMPHASIS_PROFILES, "narrative"),
        "primary_accent": _safe_family(semantic.get("primary_accent")),
        "secondary_accent": _safe_family(semantic.get("secondary_accent")),
        "background_style": cv("background_style", ("plain", "tinted", "banded"), "plain"),
        "density": cv("density", DENSITIES, "balanced"),
        "typography_scale": cv("typography_scale", TYPEOGRAPHIES, "balanced"),
        "title_treatment": cv("title_treatment", ("minimal", "bold_banner", "classic", "data_driven"), "minimal"),
        "section_treatment": cv("section_treatment", SECTION_TREATMENTS, "ruled"),
        "chart_treatment": cv("chart_treatment", CHART_TREATMENTS, "framed"),
        "table_treatment": cv("table_treatment", TABLE_TREATMENTS, "striped"),
        "callout_treatment": cv("callout_treatment", CALLOUT_TREATMENTS, "bordered"),
        "image_treatment": cv("image_treatment", IMAGE_TREATMENTS, "framed"),
        "preferred_layouts": [str(x) for x in (semantic.get("preferred_layouts") or []) if str(x).strip()][:6] or ["single_column"],
        "opening_style": cv("opening_style", OPENING_STYLES, "cover"),
        "ending_style": cv("ending_style", ENDING_STYLES, "sources_only"),
        "source_style": cv("source_style", SOURCE_STYLES, "appendix"),
    }


def _safe_family(value: Any) -> str:
    fam = family_for(value)
    return fam.key if fam else ""


def resolve_chart_palette(presentation: Any) -> List[str]:
    """Small convenience for renderers: read the resolved chart palette off a
    presentation dict (or spec), falling back to a deterministic brand pick."""
    d = _presentation_dict(presentation)
    palette = d.get("chart_palette")
    if isinstance(palette, list) and palette and all(isinstance(c, str) and _HEX_RE.match(c) for c in palette):
        return palette
    vs = d.get("visual_spec") if isinstance(d.get("visual_spec"), dict) else {}
    fam = family_for(vs.get("primary_accent"))
    if fam is not None:
        return list(fam.chart)
    return list(ACCENT_FAMILIES["slate"].chart)


def resolve_treatments(presentation: Any) -> Dict[str, str]:
    """Read the resolved semantic treatments off a presentation, falling back
    to validated defaults so renderers never crash on old/partial payloads."""
    d = _presentation_dict(presentation)
    vs = d.get("visual_spec") if isinstance(d.get("visual_spec"), dict) else {}
    return apply_semantic_defaults(vs)


def _presentation_dict(presentation: Any) -> Dict[str, Any]:
    if hasattr(presentation, "to_dict"):
        try:
            return presentation.to_dict()
        except Exception:
            return {}
    if isinstance(presentation, dict):
        return presentation
    return {}


def _iter_sections(presentation: Any) -> Iterable[Dict[str, Any]]:
    d = _presentation_dict(presentation)
    for sec in d.get("sections") or []:
        if isinstance(sec, dict):
            yield sec


def structure_from_presentation(presentation: Any, *, source_count: int = 0) -> Dict[str, Any]:
    """Extract content-structure features from an already-composed presentation
    so renderers can re-derive treatments when a payload lacks one."""
    sections = list(_iter_sections(presentation))
    types = [str(sec.get("section_type") or "") for sec in sections]
    chart_count = 0
    table_count = 0
    has_metrics = False
    comparison_count = 0
    risk_count = 0
    prose_chars = 0
    signals: Set[str] = set()
    for sec in sections:
        emphasis = str(sec.get("emphasis") or "")
        if emphasis:
            signals.add(emphasis)
        for block in sec.get("blocks") or []:
            kind = str(block.get("kind") or "") if isinstance(block, dict) else ""
            if kind == "chart":
                chart_count += 1
            elif kind == "table":
                table_count += 1
            elif kind == "metrics":
                has_metrics = True
            elif kind in ("comparison", "table") or str(sec.get("section_type") or "") == "comparison":
                comparison_count += 1
            elif kind == "risk_matrix" or str(sec.get("section_type") or "") == "risk_assessment":
                risk_count += 1
            elif kind == "prose":
                prose_chars += len(str(block.get("body") or "")) if isinstance(block, dict) else 0
    return {
        "section_count": len(sections),
        "chart_count": chart_count,
        "table_count": table_count,
        "has_metrics": has_metrics,
        "emphasis_signals": sorted(signals),
        "section_types": types,
        "source_count": int(source_count or 0),
        "comparison_count": comparison_count,
        "risk_count": risk_count,
        "qualitative_ratio": min(1.0, prose_chars / max(1, prose_chars + (chart_count * 700) + (table_count * 400))),
    }


def evidence_shape_from_report(
    question: str = "", report: str = "", *, sections: Optional[Sequence[Dict[str, Any]]] = None,
    charts: Optional[Sequence[Dict[str, Any]]] = None, key_stats: Optional[Sequence[Dict[str, Any]]] = None,
    evidence_assets: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Extract renderer-neutral evidence-shape signals for art direction.

    This is intentionally based on data availability and content structure,
    not on report names or sector-specific branches.
    """
    charts = [c for c in (charts or []) if isinstance(c, dict)]
    sections = [s for s in (sections or []) if isinstance(s, dict)]
    types = [str(s.get("section_type") or "").lower() for s in sections]
    signal_text = f"{question} {' '.join(types)}".lower()
    table_count = sum(1 for c in charts if c.get("type") == "table")
    series_count = sum(len(c.get("series") or []) for c in charts)
    timeseries_count = sum(1 for c in charts if c.get("type") in ("line", "area"))
    comparison_count = sum(1 for s in sections if s.get("section_type") in ("comparison", "market_context"))
    if re.search(r"\b(compare|versus| vs |peer|benchmark|historical)\b", signal_text, re.I):
        comparison_count += min(2, table_count)
    risk_count = sum(1 for s in sections if s.get("section_type") in ("risk_assessment", "recommendations"))
    risk_count += len(re.findall(r"\b(risk|uncertainty|threshold|downside|sensitivity)\b", report or "", re.I)) // 3
    metric_count = len(key_stats or []) + series_count
    company_focus = any(t in types for t in ("financials", "valuation", "company_analysis")) or bool(re.search(r"\b(management|analyst|earnings|cash flow|company-specific|oem)\b", signal_text))
    management_analyst = bool(re.search(r"\b(management|analyst|sell[- ]side|buy[- ]side)\b", signal_text))
    unit_economics = bool(re.search(r"\b(unit economics|aov|cac|contribution margin|arpu|density per)\b", signal_text))
    project_economics = bool(re.search(r"\b(project economics|wacc|financing cost|storage|transmission|return on capital)\b", signal_text))
    qualitative_ratio = max(0.0, min(1.0, 1.0 - ((len(charts) * 700) + (table_count * 400) + (len(key_stats or []) * 80)) / max(1, len(report or ""))))
    return {
        "comparison_count": comparison_count,
        "time_series_density": timeseries_count / max(1, len(charts)),
        "company_focus": company_focus,
        "management_analyst": management_analyst,
        "risk_count": risk_count,
        "financial_metric_density": metric_count,
        "qualitative_ratio": qualitative_ratio,
        "asset_count": len(evidence_assets or []),
        "unit_economics": unit_economics,
        "project_economics": project_economics,
    }
