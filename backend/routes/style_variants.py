"""Per-report design variants layered over the client house styles.

A variant is a combination of a colour palette, a type pairing, a section-heading
treatment, a cover layout and a table style, so successive reports do not all look
alike. Variants change presentation only: the house style's structure (cover, section
order, callouts, disclaimer), charts, citations and every evidence gate stay the same.

The number of a report's variant travels with the report (``styleVariant``) so the PDF
export, section edits and re-renders of the same report keep its look. ``None`` (old
reports, callers that never chose one) keeps the plain house style.
"""
from __future__ import annotations

import random
import re
from contextvars import ContextVar

# name, primary (dark; white text sits on it), accent, accent-soft (on primary), paper, card, rule, ink, muted
PALETTES: tuple[tuple[str, ...], ...] = (
    ("Navy and Gold", "#0E1F3D", "#AB7E36", "#D9B77A", "#F4F0E8", "#FAF8F3", "#E2DCD0", "#1E2A3A", "#5B6372"),
    ("Forest and Brass", "#12372A", "#A47E3B", "#D8B878", "#F3F1EA", "#FAF9F4", "#DCD8CB", "#1C2B24", "#59645E"),
    ("Burgundy and Sand", "#4A1426", "#B07A4F", "#DDB48E", "#F6F0EC", "#FBF8F6", "#E6DAD3", "#2A1A1F", "#6A5A5F"),
    ("Charcoal and Teal", "#22272E", "#1F8A8A", "#7CC9C4", "#F2F3F2", "#FAFAF9", "#DADDDB", "#1E2328", "#5D646B"),
    ("Slate and Coral", "#263445", "#C75B40", "#F0A58F", "#F5F3F0", "#FBFAF8", "#E1DCD5", "#1F2A36", "#5E6673"),
    ("Midnight and Cyan", "#0B1F33", "#23809B", "#8FD3E6", "#F1F5F7", "#F9FBFC", "#D9E2E7", "#14212D", "#566370"),
    ("Aubergine and Gold", "#2E1A3B", "#A68232", "#E2C98A", "#F5F2EE", "#FBF9F7", "#E2DBD3", "#241A2C", "#62586A"),
    ("Pine and Copper", "#1B3B36", "#B0623A", "#E2A27C", "#F2F2EC", "#FAFAF6", "#DADBD0", "#1A2A27", "#5A6662"),
    ("Ink and Vermilion", "#1B1B1F", "#C0392B", "#EE8C7E", "#F7F5F2", "#FFFFFF", "#E2DED8", "#1B1B1F", "#5E5B57"),
    ("Royal and Amber", "#1D2F6F", "#B97F12", "#F2C66B", "#F4F5F8", "#FBFCFD", "#DADFEA", "#1A2340", "#59617A"),
    ("Oxblood and Stone", "#5A1F1B", "#8C7360", "#D3C1AE", "#F4F1EC", "#FAF8F5", "#E0D9CF", "#2B1D1B", "#67605A"),
    ("Teal and Saffron", "#0F3D3E", "#B47D1C", "#F3CB78", "#F1F4F2", "#F9FBFA", "#D7E0DC", "#132B2B", "#566764"),
    ("Graphite and Olive", "#2B2F33", "#6E8F35", "#B8D68A", "#F3F4F1", "#FAFBF8", "#DCDFD6", "#23272A", "#5E6468"),
    ("Indigo and Rose", "#282A5C", "#B04F6E", "#E9A3B8", "#F5F3F6", "#FBFAFC", "#E0DCE5", "#20223F", "#605E72"),
    ("Espresso and Cream", "#3B2A20", "#A9763F", "#E5C49A", "#F6F1E9", "#FCF9F4", "#E6DCCD", "#2B2019", "#6B5F55"),
    ("Steel and Orange", "#2F3E4E", "#C9661F", "#F4B17C", "#F3F4F5", "#FBFBFC", "#DDE1E5", "#222D38", "#5D6772"),
    ("Emerald and Gold", "#0F4D3A", "#A5832A", "#E6CB7E", "#F2F4EF", "#FAFBF8", "#D8DFD5", "#14302A", "#586862"),
    ("Plum and Silver", "#432541", "#7D7E94", "#C6C7D4", "#F5F3F5", "#FBFAFB", "#E2DCE1", "#2B1D2A", "#655B66"),
    ("Ocean and Sun", "#123E63", "#B97B1D", "#F5CC86", "#F1F5F8", "#F9FBFD", "#D6E0E8", "#142B40", "#57677A"),
    ("Crimson and Charcoal", "#7E1A27", "#C47A35", "#F0C08F", "#F7F4F2", "#FFFFFF", "#E6DDD9", "#26191B", "#6B5D5F"),
)

# name, serif stack (body), sans stack (labels, numbers). Each stack ends in the bundled Lora / Inter,
# so a server without the system font still renders the PDF offline.
TYPE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("Lora and Inter", "'Lora', Georgia, serif", "'Inter', 'Segoe UI', Arial, sans-serif"),
    ("Georgia and Segoe", "Georgia, 'Lora', serif", "'Segoe UI', 'Inter', Arial, sans-serif"),
    ("Cambria and Calibri", "Cambria, 'Lora', Georgia, serif", "Calibri, 'Inter', 'Segoe UI', sans-serif"),
    ("Palatino and Trebuchet", "'Palatino Linotype', 'Book Antiqua', Palatino, 'Lora', serif",
     "'Trebuchet MS', 'Inter', 'Segoe UI', sans-serif"),
    ("Constantia and Verdana", "Constantia, 'Lora', Georgia, serif", "Verdana, 'Inter', 'Segoe UI', sans-serif"),
)
HEADINGS: tuple[str, ...] = ("rule", "bar", "band", "boxed")
COVERS: tuple[str, ...] = ("solid", "sidebar", "topband", "split")
TABLES: tuple[str, ...] = ("solid", "underline")

_DIMENSIONS = (len(PALETTES), len(TYPE_PAIRS), len(HEADINGS), len(COVERS), len(TABLES))
VARIANT_COUNT = 1
for _size in _DIMENSIONS:
    VARIANT_COUNT *= _size

_CURRENT_VARIANT: ContextVar[int | None] = ContextVar("gg_style_variant", default=None)


def normalize_style_variant(value: object) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= VARIANT_COUNT else None


def pick_style_variant(rng: random.Random | None = None) -> int:
    return (rng or random.SystemRandom()).randint(1, VARIANT_COUNT)


def set_current_style_variant(value: object) -> int | None:
    number = normalize_style_variant(value)
    _CURRENT_VARIANT.set(number)
    return number


def current_style_variant() -> int | None:
    return _CURRENT_VARIANT.get()


def variant_from_html(html_doc: str) -> int | None:
    match = re.search(r'<body[^>]*\bdata-style-variant=["\'](\d+)', html_doc or "", re.I)
    return normalize_style_variant(match.group(1)) if match else None


def describe_variant(number: object) -> dict | None:
    value = normalize_style_variant(number)
    if value is None:
        return None
    index = value - 1
    parts = []
    for size in _DIMENSIONS:
        parts.append(index % size)
        index //= size
    palette = PALETTES[parts[0]]
    type_pair = TYPE_PAIRS[parts[1]]
    return {
        "id": value,
        "palette": palette[0],
        "colors": dict(zip(("primary", "accent", "accentSoft", "paper", "card", "rule", "ink", "muted"), palette[1:])),
        "type": type_pair[0], "serif": type_pair[1], "sans": type_pair[2],
        "heading": HEADINGS[parts[2]], "cover": COVERS[parts[3]], "table": TABLES[parts[4]],
        "name": f"{palette[0]} · {type_pair[0]} · {HEADINGS[parts[2]]} headings · {COVERS[parts[3]]} cover",
    }


def _mix(hex_a: str, hex_b: str, share_b: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * share_b):02X}" for x, y in zip(a, b))


def variant_theme(style: str | None, number: object) -> dict:
    """Render-theme tokens (chart colours included) for a variant; empty for no variant."""
    spec = describe_variant(number)
    if not spec or style not in {"market_currents", "feature_article"}:
        return {}
    c = spec["colors"]
    return {
        "primaryColor": c["primary"], "accentColor": c["accent"], "surfaceColor": c["paper"] if style == "market_currents" else "#FFFFFF",
        "surfaceAltColor": c["card"], "textColor": c["ink"], "mutedColor": c["muted"], "borderColor": c["rule"],
        "chartColors": [c["primary"], c["accent"], "#1E7A4C", "#B5382C", _mix(c["primary"], "#FFFFFF", 0.45), c["muted"]],
        "barSignColors": {"pos": "#1E7A4C", "neg": "#B5382C"},
    }


def _market_variant_css(spec: dict) -> str:
    c = spec["colors"]
    alt_row = _mix(c["paper"], c["rule"], 0.35)
    s = 'html body.gg-house--market_currents[data-style-variant]'
    css = [f"""
{s} {{
  --mc-navy: {c['primary']}; --mc-gold: {c['accent']}; --mc-gold-soft: {c['accentSoft']}; --mc-paper: {c['paper']};
  --mc-card: {c['card']}; --mc-rule: {c['rule']}; --mc-ink: {c['ink']}; --mc-muted: {c['muted']}; --mc-bronze: {c['accent']};
  --mc-serif: {spec['serif']}; --mc-sans: {spec['sans']};
  background: var(--mc-paper) !important; color: var(--mc-ink) !important;
}}
{s} main, {s} .gg-section-body p, {s} .gg-section-body li {{ font-family: var(--mc-serif) !important; color: {c['ink']} !important; }}
{s} .gg-house-cover__kicker {{ color: {c['accentSoft']} !important; }}
{s} .gg-section-heading h2, {s} .gg-sources h2 {{ color: {c['ink']} !important; }}
{s} .gg-table tbody td {{ background: {c['card']} !important; color: {c['ink']} !important; border-bottom-color: {c['rule']} !important; }}
{s} .gg-table tbody tr:nth-child(even) td {{ background: {alt_row} !important; }}
{s} .gg-badge--flat {{ background: {c['rule']}; color: {c['muted']}; }}
{s} .gg-house-timeline li::after {{ background: {c['rule']}; }}
{s} .gg-house-timeline__dot {{ background: {c['accent']}; box-shadow: 0 0 0 3px {c['rule']}; }}
{s} .gg-house-timeline__text {{ color: {c['ink']}; }}
{s} .gg-house-disclaimer {{ border-top-color: {c['rule']}; }}
{s} .gg-house-disclaimer p {{ color: {c['muted']} !important; }}
{s} .gg-source-note, {s} .gg-chart-caption {{ color: {c['muted']} !important; }}
{s} .gg-stat-label {{ color: {c['muted']} !important; }}
"""]
    heading = spec["heading"]
    if heading == "bar":
        css.append(f"{s} .gg-section-heading {{ border-bottom: 0 !important; border-left: 6px solid var(--mc-gold) !important; padding: 2px 0 2px 14px !important; }}")
    elif heading == "band":
        css.append(f"{s} .gg-section-heading {{ border-bottom: 0 !important; background: var(--mc-navy) !important; padding: 12px 16px !important; }}\n"
                   f"{s} .gg-section-heading h2 {{ color: #FFFFFF !important; }}\n"
                   f"{s} .gg-section-heading .gg-section-kicker {{ color: var(--mc-gold-soft) !important; }}")
    elif heading == "boxed":
        css.append(f"{s} .gg-section-heading {{ border-bottom: 0 !important; border-top: 3px solid var(--mc-gold) !important; background: var(--mc-card) !important; padding: 12px 16px !important; }}")
    cover = spec["cover"]
    if cover == "sidebar":
        css.append(f"{s} .gg-house-cover {{ background-image: linear-gradient(to right, var(--mc-gold) 0, var(--mc-gold) 22px, var(--mc-navy) 22px, var(--mc-navy) 100%) !important; background-size: auto !important; }}")
    elif cover == "topband":
        css.append(f"{s} .gg-house-cover {{ background-image: linear-gradient(to bottom, var(--mc-gold) 0, var(--mc-gold) 22px, var(--mc-navy) 22px, var(--mc-navy) 100%) !important; background-size: auto !important; }}")
    elif cover == "split":
        css.append(
            f"{s} .gg-house-cover {{ background-color: var(--mc-paper) !important; background-image: linear-gradient(to bottom, var(--mc-navy) 0, var(--mc-navy) 62%, var(--mc-gold) 62%, var(--mc-gold) calc(62% + 6px), var(--mc-paper) calc(62% + 6px), var(--mc-paper) 100%) !important; background-size: auto !important; }}\n"
            f"{s} .gg-house-cover__stat-value.gg-tone--flat {{ color: {c['ink']} !important; }}\n"
            f"{s} .gg-house-cover__stat-value.gg-tone--pos {{ color: #1E7A4C !important; }}\n"
            f"{s} .gg-house-cover__stat-value.gg-tone--neg {{ color: #B5382C !important; }}\n"
            f"{s} .gg-house-cover__stat-label {{ color: {c['muted']} !important; }}")
    if spec["table"] == "underline":
        css.append(f"{s} .gg-table thead th {{ background: transparent !important; color: var(--mc-navy) !important; border-bottom: 2px solid var(--mc-navy) !important; }}")
    return "\n".join(css)


def _feature_variant_css(spec: dict) -> str:
    c = spec["colors"]
    s = 'html body.gg-house--feature_article[data-style-variant]'
    return f"""
{s} {{ --fa-ink: {c['ink']}; --fa-muted: {c['muted']}; --fa-red: {c['accent']}; --fa-card: {c['card']}; --fa-rule: {c['rule']};
  --fa-serif: {spec['serif']}; --fa-sans: {spec['sans']}; }}
{s} .gg-feature-title, {s} blockquote.gg-pullquote {{ color: {c['primary']} !important; }}
{s} .gg-table thead th {{ border-bottom-color: {c['primary']} !important; }}
"""


def variant_css(style: str | None, number: object) -> str:
    spec = describe_variant(number)
    if not spec:
        return ""
    if style == "market_currents":
        return _market_variant_css(spec)
    if style == "feature_article":
        return _feature_variant_css(spec)
    return ""


def variant_print_css(style: str | None, number: object) -> str:
    """Print-only overrides; appended after the house print CSS so the page colour matches the palette."""
    spec = describe_variant(number)
    if not spec or style != "market_currents":
        return ""
    paper, rule = spec["colors"]["paper"], spec["colors"]["rule"]
    return (f"\n@page {{ background: {paper}; }}\n"
            f'html:has(body[data-style-variant]), html body.gg-house--market_currents[data-style-variant] {{ background: {paper} !important; }}\n'
            f"@page {{ @bottom-left {{ border-top-color: {rule}; }} @bottom-center {{ border-top-color: {rule}; }} @bottom-right {{ border-top-color: {rule}; }} }}\n")
