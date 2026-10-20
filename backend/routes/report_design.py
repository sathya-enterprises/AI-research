"""Growth Gradual editorial design system (v93).

One source of truth for the report's visual identity, shared by
``routes/html_report.py`` (browser HTML + the HTML that becomes the PDF) and
``routes/pdf.py`` (print layer + static SVG charts).

Design intent: an institutional research publication — warm paper, near-black
ink, a serif display face, a highly legible sans for body and tables, hairline
dividers instead of card borders, and a deep forest accent used only as narrow
indicators (2px rails, short rules, citation colour).

Everything here is presentation only. Nothing in this module reads, changes or
filters report content, chart data, citations or publication-audit results.

Fonts
-----
Lora (display) and Inter (body/tables) are bundled as OFL-licensed WOFF2 files
in ``backend/assets/fonts`` and embedded as data URIs, so PDF generation never
waits on a network font request. If the files are missing the stacks fall back
to Georgia / system sans without breaking layout.
"""
from __future__ import annotations

import base64
import functools
from pathlib import Path

# ── tokens ──────────────────────────────────────────────────────────────────
TOKENS: dict[str, str] = {
    "paper": "#FAF9F5",        # warm executive canvas (screen)
    "surface": "#FFFFFF",      # crisp white panels / PDF page
    "ink": "#2A2A2A",          # primary text
    "ink_strong": "#1C1C1A",   # headings
    "ink_soft": "#4A4843",     # secondary text
    "muted": "#6F6B63",        # captions, overlines, axis text
    "rule": "#E1DDD5",         # hairline dividers
    "rule_soft": "#ECE8E0",    # chart grid lines
    "tint": "#F3EFE6",         # metric boxes, callouts
    "forest": "#162721",       # primary accent — narrow indicators only
    "navy": "#0B1B3D",         # alternative accent
    "positive": "#1D6B47",
    "negative": "#9A3B2E",
    "warning": "#8A5A12",
    "warning_tint": "#FBF3E4",
}

# Categorical chart palette, validated (lightness band, chroma floor, CVD and
# normal-vision separation, ≥3:1 contrast) against both #FAF9F5 and #FFFFFF.
# Fixed order — series 1 is always the green, never cycled by rank.
CHART_PALETTE: list[str] = ["#17845A", "#3461A8", "#C27C26", "#A64B63", "#0897AC", "#7E62B5"]

FONT_DISPLAY = "'Lora', 'Merriweather', Georgia, 'Times New Roman', serif"
FONT_BODY = "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
FONT_NUMERIC = FONT_BODY

# Static SVG chart ink (pdf.py) — text uses ink tokens, never series colours.
SVG_AXIS_INK = TOKENS["muted"]
SVG_LABEL_INK = TOKENS["ink_soft"]
SVG_TITLE_INK = TOKENS["ink_soft"]
SVG_VALUE_INK = TOKENS["ink"]
SVG_GRID = TOKENS["rule_soft"]
SVG_FONT = "Inter, system-ui, -apple-system, 'Segoe UI', Arial, sans-serif"

BODY_CLASS = "gg-editorial"


def editorial_render_theme() -> dict:
    """Validated theme tokens consumed by html_report's legacy theme pipeline."""
    return {
        "primaryColor": TOKENS["forest"],
        "accentColor": TOKENS["forest"],
        "surfaceColor": TOKENS["surface"],
        "surfaceAltColor": TOKENS["paper"],
        "textColor": TOKENS["ink"],
        "mutedColor": TOKENS["ink_soft"],
        "borderColor": TOKENS["rule"],
        "fontFamily": "",
        "chartColors": list(CHART_PALETTE),
    }


# ── fonts ───────────────────────────────────────────────────────────────────
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, "
          "U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD")
_LATIN_EXT = ("U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+1D00-1DBF, U+1E00-1E9F, "
              "U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, U+2C60-2C7F, U+A720-A7FF")
_FONT_FILES = [
    ("Lora", 400, "normal", "lora-latin-400-normal.woff2", _LATIN),
    ("Lora", 400, "italic", "lora-latin-400-italic.woff2", _LATIN),
    ("Lora", 500, "normal", "lora-latin-500-normal.woff2", _LATIN),
    ("Lora", 600, "normal", "lora-latin-600-normal.woff2", _LATIN),
    ("Inter", 400, "normal", "inter-latin-400-normal.woff2", _LATIN),
    ("Inter", 500, "normal", "inter-latin-500-normal.woff2", _LATIN),
    ("Inter", 600, "normal", "inter-latin-600-normal.woff2", _LATIN),
    ("Inter", 700, "normal", "inter-latin-700-normal.woff2", _LATIN),
    # latin-ext carries the rupee sign (U+20B9) used throughout Indian reports.
    ("Inter", 400, "normal", "inter-latin-ext-400-normal.woff2", _LATIN_EXT),
    ("Inter", 600, "normal", "inter-latin-ext-600-normal.woff2", _LATIN_EXT),
]


@functools.lru_cache(maxsize=1)
def font_face_css() -> str:
    """@font-face rules with embedded WOFF2 data (offline, deterministic).

    Missing files are skipped individually; the font stacks above fall back.
    """
    rules = []
    for family, weight, style, filename, urange in _FONT_FILES:
        path = _FONT_DIR / filename
        try:
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        rules.append(
            f"@font-face {{ font-family: '{family}'; font-style: {style}; font-weight: {weight}; font-display: swap; "
            f"src: url(data:font/woff2;base64,{data}) format('woff2'); unicode-range: {urange}; }}"
        )
    return "\n".join(rules)


def fonts_available() -> bool:
    return all((_FONT_DIR / f[3]).is_file() for f in _FONT_FILES)


# ── editorial stylesheet ────────────────────────────────────────────────────
# Every selector is scoped under ``html body.gg-editorial`` so the layer wins over
# the renderer's older tier/theme rules without touching them. ``!important`` is
# used only where the PDF's legacy print layers also use it.
_P = "html body.gg-editorial"


def _css_vars() -> str:
    t = TOKENS
    return f"""
:root {{
  --gg-paper: {t['paper']}; --gg-surface: {t['surface']}; --gg-ink: {t['ink']}; --gg-ink-strong: {t['ink_strong']};
  --gg-ink-soft: {t['ink_soft']}; --gg-muted: {t['muted']}; --gg-rule: {t['rule']}; --gg-rule-soft: {t['rule_soft']};
  --gg-tint: {t['tint']}; --gg-forest: {t['forest']}; --gg-navy: {t['navy']}; --gg-accent: var(--gg-forest);
  --gg-positive: {t['positive']}; --gg-negative: {t['negative']}; --gg-warning: {t['warning']}; --gg-warning-tint: {t['warning_tint']};
  --gg-font-display: {FONT_DISPLAY}; --gg-font-body: {FONT_BODY};
  --gg-space-1: 4px; --gg-space-2: 8px; --gg-space-3: 12px; --gg-space-4: 16px; --gg-space-5: 24px; --gg-space-6: 32px; --gg-space-7: 48px;
  --gg-measure: 72ch;
}}"""


def editorial_css() -> str:
    """Screen + print-safe editorial layer (append after the renderer's CSS)."""
    p = _P
    return _css_vars() + f"""
/* ── canvas & type ─────────────────────────────────────────────────────── */
{p} {{ background: var(--gg-paper); color: var(--gg-ink); font-family: var(--gg-font-body); font-size: 16px; line-height: 1.66; -webkit-font-smoothing: antialiased; }}
html:has(body.gg-editorial) {{ background: var(--gg-paper); }}
{p} main {{ width: min(920px, calc(100% - 32px)) !important; max-width: 920px !important; margin-inline: auto !important; padding-block: var(--gg-space-6) var(--gg-space-7); }}
{p} h1, {p} h2, {p} h3, {p} .gg-title, {p} .gg-section-heading h2, {p} .gg-summary-heading {{
  font-family: var(--gg-font-display) !important; color: var(--gg-ink-strong) !important; font-weight: 400 !important; letter-spacing: -0.01em; }}
{p} h4, {p} .gg-subsection-h4, {p} .gg-section-h3 {{ font-family: var(--gg-font-body) !important; color: var(--gg-ink-strong) !important; }}
{p} p, {p} .gg-list, {p} li {{ color: var(--gg-ink) !important; }}
{p}, {p} p, {p} li, {p} td, {p} th, {p} figcaption, {p} .gg-title-meta, {p} .gg-report-index, {p} .gg-report-index a,
{p} .gg-report-index span, {p} .gg-section-body, {p} .gg-summary-card, {p} .gg-callout {{ font-family: var(--gg-font-body) !important; }}
{p} .gg-report-index li, {p} .gg-report-index a {{ color: var(--gg-ink) !important; }}
{p} .gg-section-body > p {{ text-align: left !important; hyphens: manual !important; max-width: var(--gg-measure); margin: 0 0 var(--gg-space-4); font-size: 16px; line-height: 1.7; }}
{p} a {{ color: var(--gg-forest); text-decoration-thickness: 1px; text-underline-offset: 2px; }}
/* Chromium emits kerned link runs glyph-by-glyph; some PDF text extractors
   (pypdf, used by the export audit fallback) then read "[ S 1 ]" / "e xample".
   Unkerned links keep citations and URLs machine-readable in the PDF. */
{p} a, {p} .gg-source-cite, {p} .gg-source-link, {p} .gg-source-url-only {{ font-kerning: none !important; font-feature-settings: "kern" 0 !important; letter-spacing: 0 !important; }}
{p} strong {{ color: var(--gg-ink-strong); font-weight: 600; }}
{p} .gg-overline, {p} .gg-report-brandbar, {p} .gg-section-kicker, {p} .gg-eyebrow, {p} .gg-cover__aside-label {{
  display: flex !important; align-items: center; gap: 10px; margin: 0 0 10px !important; font: 600 10px/1.3 var(--gg-font-body) !important;
  letter-spacing: 0.25em !important; text-transform: uppercase !important; color: var(--gg-muted) !important; background: none !important; }}
{p} .gg-report-brandbar::before, {p} .gg-section-kicker::before {{ content: ""; flex: 0 0 24px; height: 2px; background: var(--gg-forest); }}

/* ── title band ────────────────────────────────────────────────────────── */
{p} .gg-title-band {{ margin: 0 0 var(--gg-space-6) !important; padding: var(--gg-space-5) 0 var(--gg-space-5) !important; border: 0 !important;
  border-bottom: 1px solid var(--gg-rule) !important; background: none !important; }}
{p} .gg-title-band .gg-title {{ max-width: 24ch; margin: 0 0 var(--gg-space-3) !important; font-size: clamp(2rem, 1.4rem + 2.6vw, 3.1rem) !important;
  line-height: 1.12 !important; font-weight: 400 !important; text-wrap: balance; }}
{p} .gg-title-meta, {p} .gg-date {{ color: var(--gg-muted) !important; font-size: 12px !important; letter-spacing: 0.04em; }}
{p} .gg-summary {{ max-width: var(--gg-measure); color: var(--gg-ink-soft) !important; font-family: var(--gg-font-display); font-size: clamp(1.05rem, 1rem + .4vw, 1.25rem); line-height: 1.55; }}
{p} .gg-cover {{ border-bottom: 1px solid var(--gg-rule) !important; }}
{p} .gg-cover--classic, {p} .gg-cover--data_driven {{ background: var(--gg-paper) !important; border: 0 !important; }}
{p} .gg-cover__aside {{ background: var(--gg-tint) !important; border-left: 2px solid var(--gg-forest) !important; }}

/* ── navigation / contents ─────────────────────────────────────────────── */
{p} .gg-report-nav {{ width: min(920px, calc(100% - 32px)) !important; margin: 0 auto !important; padding: 10px 0 !important; background: var(--gg-paper) !important;
  border: 0 !important; border-bottom: 1px solid var(--gg-rule) !important; border-radius: 0 !important; box-shadow: none !important; backdrop-filter: none !important; }}
{p} .gg-report-nav ul {{ display: flex; flex-wrap: wrap; gap: 6px 20px; margin: 8px 0 0; padding: 0; list-style: none; }}
{p} .gg-report-nav summary {{ font: 600 10px var(--gg-font-body); letter-spacing: .25em; text-transform: uppercase; color: var(--gg-muted); }}
{p} .gg-report-nav a {{ color: var(--gg-ink-soft) !important; text-decoration: none; font-size: 13px; }}
{p} .gg-report-nav a:hover {{ color: var(--gg-forest) !important; }}
{p} .gg-report-index {{ background: none !important; border: 0 !important; border-top: 1px solid var(--gg-rule) !important; border-bottom: 1px solid var(--gg-rule) !important;
  border-radius: 0 !important; box-shadow: none !important; padding: var(--gg-space-4) 0 !important; }}
{p} .gg-report-index h2 {{ font-size: 1.15rem !important; }}

/* ── sections ──────────────────────────────────────────────────────────── */
{p} .gg-report-sections {{ display: flex; flex-direction: column; gap: 0 !important; }}
{p} .gg-report-sections, {p} .gg-section, {p} .gg-composition--two_column .gg-report-sections, {p} .gg-composition--sidebar_main .gg-report-sections {{ max-width: none !important; }}
{p} .gg-section {{ min-width: 0; margin: 0 !important; padding: var(--gg-space-6) 0 var(--gg-space-2) !important; border: 0 !important;
  border-top: 1px solid var(--gg-rule) !important; background: none !important; box-shadow: none !important; border-radius: 0 !important; }}
{p} .gg-report-sections > .gg-section:first-child {{ border-top: 0 !important; padding-top: var(--gg-space-2) !important; }}
{p} .gg-section-heading {{ display: block !important; margin: 0 0 var(--gg-space-4) !important; padding: 0 !important; border: 0 !important; background: none !important; }}
{p} .gg-section-index {{ display: none !important; }}
{p} .gg-section-heading-copy::before {{ content: none !important; display: none !important; }}
{p} .gg-section-heading-copy {{ display: block !important; min-width: 0; }}
/* Legacy decorative labels injected on h2::before ("Economics", …) duplicate
   the reader-facing overline; the kicker already names the section type. */
{p} .gg-section-heading h2::before, {p} .gg-section-heading h2::after {{ content: none !important; display: none !important; }}
{p} .gg-section-heading h2 {{ margin: 0 !important; font-size: clamp(1.5rem, 1.2rem + 1.2vw, 2.05rem) !important; line-height: 1.2 !important; text-wrap: balance; }}
{p} .gg-section-heading p {{ color: var(--gg-muted) !important; }}
{p} .gg-section-h3 {{ margin: var(--gg-space-5) 0 var(--gg-space-2) !important; font-size: 1.05rem !important; font-weight: 600 !important; letter-spacing: -0.005em; }}
{p} .gg-subsection-h4 {{ margin: var(--gg-space-5) 0 var(--gg-space-2) !important; font: 600 10px/1.3 var(--gg-font-body) !important; letter-spacing: .2em !important;
  text-transform: uppercase; color: var(--gg-muted) !important; }}
{p} .gg-section-body {{ min-width: 0; }}
{p} .gg-section-body--grid {{ grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)) !important; gap: var(--gg-space-5) !important; }}
{p} .gg-section--sidebar_main {{ grid-template-columns: minmax(0, 1fr) !important; }}
{p} .gg-section[data-emphasis="critical"] {{ background: none !important; padding: var(--gg-space-6) 0 var(--gg-space-2) !important; }}
{p} .gg-section--full_bleed {{ width: auto !important; margin-left: 0 !important; padding-inline: 0 !important; background: none !important; }}
{p} .gg-list {{ padding-left: 1.2em !important; }}
{p} .gg-list li {{ margin: 0 0 var(--gg-space-2) !important; padding-left: 4px; line-height: 1.62; }}
{p} .gg-list li::marker {{ color: var(--gg-forest); }}
{p} .gg-divider {{ border-top: 1px solid var(--gg-rule) !important; }}
{p} .gg-block--emphasis-high, {p} .gg-block--emphasis-critical {{ border-left: 2px solid var(--gg-forest) !important; padding-left: var(--gg-space-4) !important; }}

/* ── citations ─────────────────────────────────────────────────────────── */
{p} .gg-source-cite {{ color: var(--gg-forest) !important; font: 600 0.72em var(--gg-font-body) !important; letter-spacing: 0 !important; font-kerning: none !important;
  text-decoration: none !important; vertical-align: 0.18em; white-space: nowrap; }}
{p} .gg-source-cite:hover {{ text-decoration: underline !important; }}

/* ── executive summary & callouts ──────────────────────────────────────── */
{p} .gg-summary-card {{ padding: var(--gg-space-5) !important; border: 0 !important; border-left: 2px solid var(--gg-forest) !important; border-radius: 0 !important;
  background: var(--gg-tint) !important; box-shadow: none !important; }}
{p} .gg-summary-heading {{ font-size: 1.35rem !important; margin: 0 0 var(--gg-space-3) !important; }}
{p} .gg-callout, {p} .gg-risk {{ padding: var(--gg-space-4) var(--gg-space-5) !important; border: 0 !important; border-left: 2px solid var(--gg-forest) !important;
  border-radius: 0 !important; background: var(--gg-tint) !important; box-shadow: none !important; }}
{p} .gg-callout--warning {{ border-left-color: var(--gg-warning) !important; background: var(--gg-warning-tint) !important; }}
{p} .gg-callout--negative, {p} .gg-risk {{ border-left-color: var(--gg-negative) !important; }}
{p} .gg-callout--positive {{ border-left-color: var(--gg-positive) !important; }}
{p} .gg-callout--info {{ border-left-color: var(--gg-navy) !important; }}
{p} .gg-pullquote {{ margin: var(--gg-space-5) 0 !important; padding: var(--gg-space-1) 0 var(--gg-space-1) var(--gg-space-5) !important; border: 0 !important;
  border-left: 2px solid var(--gg-forest) !important; background: none !important; font: italic 400 clamp(1.1rem, 1rem + .5vw, 1.3rem)/1.5 var(--gg-font-display) !important;
  color: var(--gg-ink-strong) !important; }}

/* ── status banners (draft / warning) ──────────────────────────────────── */
{p} .gg-status-banner {{ display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; margin: 0 0 var(--gg-space-5) !important;
  padding: var(--gg-space-3) var(--gg-space-4) !important; border: 0 !important; border-left: 2px solid var(--gg-warning) !important; border-radius: 0 !important;
  background: var(--gg-warning-tint) !important; color: #4F3609 !important; font: 400 14px/1.55 var(--gg-font-body) !important; font-style: normal !important; }}
{p} .gg-status-banner::before {{ content: attr(data-status-label); font: 600 10px/1.3 var(--gg-font-body); letter-spacing: .25em; text-transform: uppercase; color: var(--gg-warning); }}
{p} .gg-status-banner strong {{ color: #3E2A06 !important; }}

/* ── metric boxes (KPI dashboard + planner metrics) ────────────────────── */
{p} .gg-stats-grid, {p} .gg-metrics {{ display: grid !important; grid-template-columns: repeat(auto-fit, minmax(min(100%, 150px), 1fr)) !important;
  gap: var(--gg-space-3) !important; margin: var(--gg-space-5) 0 !important; }}
{p} .gg-dashboard-strip {{ margin: 0 0 var(--gg-space-6) !important; padding: 0 !important; border: 0 !important; background: none !important; }}
{p} .gg-stat-card, {p} .gg-metric {{ position: relative; display: flex !important; flex-direction: column; justify-content: flex-start; gap: 6px; min-width: 0;
  padding: var(--gg-space-4) var(--gg-space-4) var(--gg-space-3) !important; border: 0 !important; border-left: 2px solid var(--gg-forest) !important;
  border-radius: 0 !important; background: var(--gg-tint) !important; background-color: var(--gg-tint) !important; box-shadow: none !important;
  break-inside: avoid; page-break-inside: avoid; }}
{p} .gg-stat-card::before, {p} .gg-stat-card::after, {p} .gg-metric::before {{ display: none !important; content: none !important; }}
{p} .gg-stat-label, {p} .gg-metric-label {{ order: -1; margin: 0 !important; font: 600 10px/1.35 var(--gg-font-body) !important; letter-spacing: .2em !important;
  text-transform: uppercase !important; color: var(--gg-muted) !important; opacity: 1 !important; overflow-wrap: anywhere; }}
{p} .gg-stat-value, {p} .gg-metric-value {{ display: flex; flex-wrap: wrap; align-items: baseline; column-gap: .22em; row-gap: 0; margin: 0 !important;
  font: 500 clamp(1.45rem, 1.2rem + .9vw, 1.9rem)/1.12 var(--gg-font-body) !important; color: var(--gg-ink-strong) !important;
  font-variant-numeric: tabular-nums lining-nums; letter-spacing: -0.015em; overflow-wrap: anywhere; }}
{p} .gg-stat-number {{ white-space: nowrap; }}
{p} .gg-stat-prefix, {p} .gg-stat-unit {{ font-size: .56em; font-weight: 500; letter-spacing: 0; color: var(--gg-ink-soft); }}
{p} .gg-stat-prefix {{ align-self: flex-start; margin-top: .2em; }}
{p} .gg-stat-change, {p} .gg-metric-change {{ margin: 0 !important; font: 600 12px/1.3 var(--gg-font-body) !important; font-variant-numeric: tabular-nums; color: var(--gg-ink-soft) !important; }}
{p} .gg-stat-card .gg-change-up, {p} .gg-metric .gg-change-up {{ color: var(--gg-positive) !important; }}
{p} .gg-stat-card .gg-change-down, {p} .gg-metric .gg-change-down {{ color: var(--gg-negative) !important; }}
{p} .gg-stat-source {{ margin-top: auto !important; padding-top: 4px; font-size: 11px !important; color: var(--gg-muted) !important; }}

/* ── tables ────────────────────────────────────────────────────────────── */
{p} .gg-table-wrap {{ margin: var(--gg-space-5) 0 !important; padding: 0 !important; border: 0 !important; background: none !important; box-shadow: none !important; border-radius: 0 !important; }}
{p} .gg-table-wrap > .gg-chart-title {{ margin: 0 0 var(--gg-space-2) !important; font: 600 10px/1.3 var(--gg-font-body) !important; letter-spacing: .2em !important;
  text-transform: uppercase !important; color: var(--gg-muted) !important; }}
{p} .gg-table-scroll {{ max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-top: 1px solid var(--gg-forest); }}
{p} .gg-table {{ width: 100%; min-width: min(100%, 560px); border-collapse: collapse !important; table-layout: auto; font: 400 13.5px/1.45 var(--gg-font-body) !important;
  color: var(--gg-ink) !important; font-variant-numeric: tabular-nums lining-nums; }}
{p} .gg-table th {{ padding: 10px 12px !important; background: var(--gg-surface) !important; color: var(--gg-muted) !important; border: 0 !important;
  border-bottom: 1px solid var(--gg-rule) !important; font: 600 10px/1.35 var(--gg-font-body) !important; letter-spacing: .14em !important;
  text-transform: uppercase !important; text-align: left; vertical-align: bottom; white-space: normal !important; overflow-wrap: break-word; hyphens: auto; }}
{p} .gg-table td {{ padding: 10px 12px !important; border: 0 !important; border-bottom: 1px solid var(--gg-rule) !important; color: var(--gg-ink) !important;
  background: var(--gg-surface) !important; vertical-align: top; overflow-wrap: break-word; word-break: normal; }}
{p} .gg-table tbody tr:nth-child(even) td {{ background: var(--gg-paper) !important; }}
{p} .gg-table td:first-child {{ color: var(--gg-ink-strong) !important; font-weight: 500; min-width: 9ch; }}
{p} .gg-table th.gg-cell-numeric, {p} .gg-table td.gg-cell-numeric {{ text-align: right !important; white-space: nowrap; }}
{p} .gg-table tbody tr:last-child td {{ border-bottom: 1px solid var(--gg-ink-soft) !important; }}

/* ── charts ────────────────────────────────────────────────────────────── */
{p} .gg-report-sections {{ counter-reset: gg-figure; }}
{p} .gg-chart-wrap {{ counter-increment: gg-figure; margin: var(--gg-space-5) 0 !important; padding: var(--gg-space-4) var(--gg-space-4) var(--gg-space-3) !important;
  border: 0 !important; border-top: 1px solid var(--gg-rule) !important; border-radius: 0 !important; background: var(--gg-surface) !important; box-shadow: none !important; min-width: 0; }}
{p} .gg-chart-wrap > .gg-chart-title {{ display: block; margin: 0 0 var(--gg-space-3) !important; font: 500 1.06rem/1.35 var(--gg-font-display) !important;
  letter-spacing: 0 !important; text-transform: none !important; color: var(--gg-ink-strong) !important; text-wrap: balance; }}
{p} .gg-chart-wrap > .gg-chart-title::before {{ content: "Figure " counter(gg-figure) !important; display: block !important; position: static !important;
  width: auto !important; height: auto !important; background: none !important; border-radius: 0 !important; margin: 0 0 4px !important;
  font: 600 10px/1.3 var(--gg-font-body) !important; letter-spacing: .25em !important; text-transform: uppercase; color: var(--gg-muted) !important; }}
{p} .gg-chart-wrap > .gg-chart-title::after {{ content: none !important; display: none !important; }}
{p} .gg-chart-canvas-box:not(.gg-chart-canvas-box--static) {{ position: relative; height: auto !important; aspect-ratio: 16 / 9; min-height: 240px; max-height: 440px; width: 100%; }}
{p} .gg-chart-canvas-box--static {{ height: auto !important; min-height: 0 !important; overflow: visible !important; }}
{p} .gg-chart-inline-svg svg, {p} .gg-pdf-chart-fallback svg {{ width: 100% !important; height: auto !important; display: block; }}
{p} .gg-chart-notes, {p} .gg-chart-wrap .gg-source-note {{ margin-top: var(--gg-space-3) !important; }}
{p} .gg-chart-note--takeaway {{ background: none !important; border: 0 !important; border-left: 2px solid var(--gg-forest) !important; padding: 2px 0 2px var(--gg-space-3) !important;
  font-family: var(--gg-font-display) !important; font-style: italic; color: var(--gg-ink-soft) !important; }}
{p} .gg-source-note {{ padding-top: var(--gg-space-2); border-top: 1px solid var(--gg-rule-soft); color: var(--gg-muted) !important; font: 400 11.5px/1.45 var(--gg-font-body) !important; }}
{p} .gg-source-note-label {{ color: var(--gg-ink-soft) !important; font-weight: 600 !important; letter-spacing: .04em; }}

/* ── figures / images ──────────────────────────────────────────────────── */
{p} .gg-figure {{ margin: var(--gg-space-5) 0 !important; padding: 0 !important; border: 0 !important; background: none !important; box-shadow: none !important; }}
{p} .gg-figure img {{ display: block; max-width: 100% !important; width: auto; height: auto; max-height: 420px; margin: 0 auto; object-fit: contain;
  border: 0 !important; border-radius: 0 !important; background: none !important; }}
{p} .gg-figure figcaption {{ margin-top: var(--gg-space-2) !important; font: italic 400 13px/1.45 var(--gg-font-display) !important; color: var(--gg-ink-soft) !important; }}

/* ── sources appendix ──────────────────────────────────────────────────── */
{p} .gg-sources {{ margin-top: var(--gg-space-7) !important; padding: var(--gg-space-5) 0 0 !important; border: 0 !important; border-top: 1px solid var(--gg-forest) !important;
  border-radius: 0 !important; box-shadow: none !important; background: none !important; max-width: 100% !important; }}
{p} .gg-sources h2 {{ font-size: 1.5rem !important; }}
{p} .gg-sources-count {{ font: 500 12px var(--gg-font-body) !important; letter-spacing: 0 !important; text-transform: none !important; color: var(--gg-muted) !important; }}
{p} .gg-sources ol, {p} .gg-sources ul {{ padding-left: 1.4em; }}
{p} .gg-sources li, {p} .gg-source-card {{ padding: 8px 0 !important; border: 0 !important; border-bottom: 1px solid var(--gg-rule) !important; background: none !important;
  box-shadow: none !important; border-radius: 0 !important; font-size: 13px; color: var(--gg-ink) !important; }}
{p} .gg-sources a {{ color: var(--gg-ink-soft) !important; overflow-wrap: anywhere; font-size: 12px; }}
{p} .gg-sources, {p} .gg-sources li, {p} .gg-sources span, {p} .gg-sources a {{ font-family: var(--gg-font-body) !important; }}
{p} .gg-source-list {{ list-style: none !important; padding-left: 0 !important; margin: var(--gg-space-3) 0 0 !important; }}
{p} .gg-source-line {{ display: grid !important; grid-template-columns: 3.2em minmax(0, 1fr); column-gap: var(--gg-space-2); row-gap: 2px; align-items: baseline; }}
{p} .gg-source-id {{ display: inline-block !important; grid-column: 1; grid-row: 1 / span 2; color: var(--gg-forest) !important; font-weight: 600 !important; font-variant-numeric: tabular-nums; font-size: .92em; }}
{p} .gg-source-title-text {{ grid-column: 2; color: var(--gg-ink-strong) !important; font-weight: 500 !important; }}
{p} .gg-source-link {{ grid-column: 2; color: var(--gg-muted) !important; text-decoration: none !important; font-size: .9em !important;
  white-space: normal !important; overflow-wrap: anywhere !important; word-break: break-word; min-width: 0; }}
{p} .gg-sources, {p} .gg-source-list, {p} .gg-source-line {{ min-width: 0; max-width: 100%; }}
{p} .gg-chart-notes {{ border: 0 !important; padding-top: 0 !important; background: none !important; }}
{p} .gg-chart-wrap .gg-chart-canvas-box {{ border: 0 !important; background: none !important; }}

/* ── footer ────────────────────────────────────────────────────────────── */
{p} .gg-report-footer {{ display: flex; flex-wrap: wrap; gap: 6px 18px; justify-content: space-between; width: min(920px, calc(100% - 32px)); margin: 0 auto var(--gg-space-6);
  padding-top: var(--gg-space-4); border-top: 1px solid var(--gg-rule); background: none !important; color: var(--gg-muted) !important; font-size: 11.5px; }}
{p} .gg-report-footer strong {{ font-weight: 600; letter-spacing: .2em; text-transform: uppercase; font-size: 10px; color: var(--gg-ink-soft); }}

/* ── responsive ────────────────────────────────────────────────────────── */
@media (max-width: 640px) {{
  {p} {{ font-size: 15px; }}
  {p} main, {p} .gg-report-footer {{ width: calc(100% - 32px); }}
  {p} .gg-section-body > p {{ font-size: 15px; }}
  {p} .gg-chart-wrap {{ padding: var(--gg-space-3) !important; }}
  {p} .gg-table {{ font-size: 13px !important; }}
}}
@media (prefers-reduced-motion: reduce) {{
  {p} .gg-reveal {{ opacity: 1 !important; transform: none !important; transition: none !important; }}
}}
"""


def editorial_print_css() -> str:
    """Final PDF layer: page furniture, print sizes, pagination. Appended last."""
    p = _P
    t = TOKENS
    return f"""
@page {{
  size: A4; margin: 17mm 15mm 19mm 15mm;
  @top-left {{ content: "GROWTH GRADUAL  ·  RESEARCH"; font-family: {FONT_BODY}; font-size: 6.6pt; font-weight: 600; letter-spacing: .22em; color: {t['muted']}; }}
  @top-right {{ content: none; }}
  @bottom-left {{ content: "{{short_title}}"; font-family: {FONT_BODY}; font-size: 7pt; font-weight: 400; color: {t['muted']}; }}
  @bottom-right {{ content: counter(page); font-family: {FONT_BODY}; font-size: 7.5pt; font-weight: 600; color: {t['ink']}; }}
}}
{p} {{ background: #FFFFFF !important; font-size: 10pt !important; line-height: 1.55 !important; }}
html:has(body.gg-editorial) {{ background: #FFFFFF !important; }}
{p} main {{ width: 100% !important; max-width: none !important; padding: 0 !important; margin: 0 !important; }}
/* Grid sections print as a single flow: grid items cannot honour
   break-inside:avoid in Chromium/WeasyPrint, which split figures from titles. */
{p} .gg-section-body--grid, {p} .gg-section--sidebar_main, {p} .gg-summary-wrap--sidebar {{ display: block !important; }}
{p} .gg-section-body--grid > * {{ width: 100% !important; max-width: 100% !important; }}
{p} .gg-report-sections {{ display: block !important; }}
{p} .gg-report-nav, {p} .gg-report-footer {{ display: none !important; }}
{p} .gg-title-band {{ padding: 6pt 0 12pt !important; margin: 0 0 14pt !important; }}
{p} .gg-title-band .gg-title {{ font-size: 25pt !important; line-height: 1.12 !important; max-width: 92% !important; }}
{p} .gg-title-meta {{ font-size: 8pt !important; }}
{p} .gg-report-brandbar, {p} .gg-section-kicker, {p} .gg-overline {{ font-size: 6.8pt !important; margin-bottom: 5pt !important; }}
{p} .gg-section {{ padding: 14pt 0 2pt !important; margin: 0 !important; }}
{p} .gg-section-heading {{ margin-bottom: 8pt !important; }}
{p} .gg-section-heading h2 {{ font-size: 16.5pt !important; line-height: 1.2 !important; }}
{p} .gg-section-h3 {{ font-size: 11pt !important; margin: 10pt 0 4pt !important; }}
{p} .gg-subsection-h4 {{ font-size: 6.8pt !important; margin: 10pt 0 4pt !important; }}
{p} .gg-section-body > p, {p} p {{ font-size: 10pt !important; line-height: 1.55 !important; max-width: none !important; margin: 0 0 6pt !important; }}
{p} .gg-list li {{ font-size: 10pt !important; line-height: 1.5 !important; margin-bottom: 3pt !important; }}
{p} .gg-stats-grid, {p} .gg-metrics {{ display: flex !important; flex-wrap: wrap !important; gap: 7pt !important; margin: 8pt 0 12pt !important; }}
{p} .gg-stats-grid > *, {p} .gg-metrics > * {{ flex: 1 1 120pt !important; }}
{p} .gg-stat-card, {p} .gg-metric {{ padding: 8pt 10pt 7pt !important; gap: 3pt !important; }}
{p} .gg-stat-value, {p} .gg-metric-value {{ font-size: 16pt !important; color: {t['ink_strong']} !important; }}
{p} .gg-stat-label, {p} .gg-metric-label {{ font-size: 6.4pt !important; color: {t['muted']} !important; }}
{p} .gg-stat-change, {p} .gg-metric-change {{ font-size: 7.6pt !important; }}
{p} .gg-stat-source {{ font-size: 7pt !important; }}
{p} .gg-table {{ font-size: 8.4pt !important; min-width: 0 !important; table-layout: auto !important; }}
{p} .gg-table th {{ font-size: 6.4pt !important; padding: 5pt 6pt !important; background: #FFFFFF !important; color: {t['muted']} !important; }}
{p} .gg-table td {{ padding: 5pt 6pt !important; }}
{p} .gg-table-scroll {{ overflow: visible !important; }}
{p} .gg-table-wrap[data-col-count="7"] .gg-table, {p} .gg-table-wrap[data-col-count="8"] .gg-table,
{p} .gg-table-wrap[data-col-count="9"] .gg-table, {p} .gg-table-wrap[data-col-count="10"] .gg-table {{ font-size: 7.6pt !important; }}
{p} .gg-table-wrap[data-col-count="7"] .gg-table th, {p} .gg-table-wrap[data-col-count="8"] .gg-table th,
{p} .gg-table-wrap[data-col-count="9"] .gg-table th, {p} .gg-table-wrap[data-col-count="10"] .gg-table th {{ font-size: 5.9pt !important; letter-spacing: .08em !important; }}
{p} .gg-table td.gg-cell-numeric {{ white-space: nowrap !important; }}
{p} .gg-chart-wrap {{ padding: 8pt 0 6pt !important; margin: 10pt 0 !important; background: #FFFFFF !important; }}
{p} .gg-chart-wrap > .gg-chart-title {{ font-size: 11.5pt !important; margin-bottom: 6pt !important; }}
{p} .gg-chart-wrap > .gg-chart-title::before {{ font-size: 6.4pt !important; margin-bottom: 2pt !important; }}
{p} .gg-chart-canvas-box--static .gg-pdf-chart-fallback svg {{ max-height: 300pt !important; }}
{p} .gg-source-note {{ font-size: 7.4pt !important; padding-top: 4pt !important; }}
{p} .gg-figure img {{ max-height: 230pt !important; }}
{p} .gg-figure figcaption {{ font-size: 8.4pt !important; }}
{p} .gg-pullquote {{ font-size: 12pt !important; padding-left: 12pt !important; }}
{p} .gg-status-banner {{ font-size: 8.8pt !important; padding: 7pt 10pt !important; margin-bottom: 10pt !important; }}
{p} .gg-status-banner::before {{ font-size: 6.4pt !important; }}
{p} .gg-summary-card {{ padding: 10pt 12pt !important; }}
{p} .gg-sources {{ margin-top: 16pt !important; padding-top: 10pt !important; }}
{p} .gg-sources h2 {{ font-size: 14pt !important; }}
{p} .gg-sources li, {p} .gg-source-card {{ font-size: 8pt !important; padding: 3pt 0 !important; }}
{p} .gg-sources a {{ font-size: 7.4pt !important; }}
{p} .gg-source-line {{ display: block !important; padding-left: 2.6em !important; text-indent: -2.6em; }}
{p} .gg-source-id {{ display: inline-block; width: 2.4em; text-indent: 0; }}
{p} .gg-source-link {{ display: block; text-indent: 0; }}
{p} .gg-source-cite {{ font-size: 0.74em !important; }}
/* pagination: keep compact groups together, let long content flow */
{p} .gg-section-heading, {p} .gg-chart-wrap, {p} .gg-figure, {p} .gg-stat-card, {p} .gg-metric, {p} .gg-status-banner, {p} .gg-summary-card {{
  break-inside: avoid-page !important; page-break-inside: avoid !important; }}
{p} .gg-stats-grid, {p} .gg-metrics {{ break-inside: avoid-page !important; page-break-inside: avoid !important; }}
{p} .gg-keep-with-next {{ break-inside: avoid-page !important; page-break-inside: avoid !important; }}
{p} .gg-keep-with-next .gg-table-wrap, {p} .gg-keep-with-next .gg-table-scroll {{ break-inside: avoid-page !important; page-break-inside: avoid !important; }}
{p} .gg-section-heading, {p} .gg-section-h3, {p} .gg-subsection-h4, {p} .gg-chart-title {{ break-after: avoid-page !important; page-break-after: avoid !important; }}
{p} .gg-table tr {{ break-inside: avoid-page !important; page-break-inside: avoid !important; }}
{p} .gg-table thead {{ display: table-header-group !important; }}
{p} p, {p} li {{ orphans: 3; widows: 3; }}
{p} * {{ print-color-adjust: exact !important; -webkit-print-color-adjust: exact !important; }}
/* Machine-readable link text. Chromium embeds each link's glyphs in its own
   font subset; without a space glyph that subset gets a 0 default width and
   pypdf (the export-audit fallback when PyMuPDF is absent) then extracts
   "[S1]" as "[ S 1 ]", which the citation audit would flag as missing. A
   1px space with matching negative tracking adds the glyph at zero visual cost. */
{p} a.gg-source-cite::after, {p} .gg-sources a::after, {p} .gg-source-note a::after {{
  content: " " !important; white-space: pre !important; font-size: 1px !important; letter-spacing: -0.28px !important; display: inline !important; }}
"""
