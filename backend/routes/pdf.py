"""
POST /api/chat/report/pdf  — Generate branded PDF (ReportLab, pure Python)
Body: { report, title, charts, question, keyStats, summary }

Report structure rendered:
  Page 1  — Title Page (cover)
  Page 2  — Introduction
  Page 3  — Data Sources & Executive Summary
  Page 4+ — Data Analysis  (charts injected at [CHART_n] placeholders)
  ...     — Key Findings
  ...     — Conclusion
  ...     — References
"""
import base64
import html
import io
import json
import logging
import math
import os
import random
import re
import time
import threading
import unicodedata
from utils.chart_catalog import normalize_chart_type
from utils.markdown_normalizer import normalize_report_markdown
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response

from utils.datawrapper import attach_datawrapper_charts, fetch_png
from routes.source_manifest import normalise_source_manifest, ensure_cited_source_records
from utils.chart_evidence_audit import (
    audit_chart_evidence_consistency, prune_unsupported_chart_points,
    remap_chart_placeholders, sanitize_charts_for_publication,
)
import asyncio
import httpx

router = APIRouter()
log = logging.getLogger("pdf")

# ─── Colour palette ───────────────────────────────────────────────────────────
# Matches the Growth Gradual house style seen in the sample "Market Currents"
# report: deep navy + gold as the primary brand pair, with a restrained set of
# muted, editorial accent colours (teal, slate blue, olive, burgundy) rather
# than bright/neon web-app colours — those clash with the cream-paper, navy-
# cover aesthetic the sample uses throughout.
NAVY    = (26/255,  31/255,  78/255)
GOLD    = (200/255, 134/255, 10/255)
GREEN   = (22/255,  128/255, 88/255)    # muted teal-green — positive deltas, best sector
RED     = (185/255, 60/255,  55/255)    # muted brick-red — negative deltas, worst sector
TEAL    = (33/255,  118/255, 122/255)   # secondary chart colour — cool teal
SLATE   = (74/255,  92/255,  138/255)   # secondary chart colour — muted slate blue
OLIVE   = (128/255, 110/255, 40/255)    # tertiary chart colour — muted olive/bronze
BURGUNDY = (110/255, 47/255, 58/255)    # tertiary chart colour — muted burgundy
AMBER   = (194/255, 140/255, 40/255)    # close to GOLD, used for chart series variety
# Kept as aliases so any pre-existing references elsewhere in this file still resolve.
BLUE    = SLATE
PURPLE  = BURGUNDY
CYAN    = TEAL
PINK    = BURGUNDY
WHITE   = (1.0, 1.0, 1.0)
LIGHT   = (240/255, 243/255, 255/255)
GREY    = (139/255, 147/255, 181/255)
BODY_TXT = (0.18, 0.21, 0.38)

# Order chosen so the first 2-3 series (the common case) land on colours that
# read cleanly against cream paper and don't fight the navy/gold brand pair.
CHART_COLORS = [NAVY, GOLD, TEAL, RED, SLATE, OLIVE, BURGUNDY, GREEN]

# Section accent colours (one per major section heading) — same restrained set.
SECTION_ACCENTS = [GOLD, TEAL, GREEN, OLIVE, RED, SLATE]

# These immutable palette values are used to reset each PDF render.  The old
# implementation mutated module globals for a custom report theme and never
# restored them, so one themed request could silently force that same theme on
# a later unrelated report.  A report now has only its own requested theme.
_DEFAULT_NAVY = NAVY
_DEFAULT_GOLD = GOLD


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float] | None:
    """'#rrggbb' -> (r, g, b) each in 0..1, ReportLab's expected range.
    Returns None for anything malformed so callers can fall back safely."""
    try:
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return None
        return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)
    except (ValueError, AttributeError):
        return None


def apply_theme(theme: dict | None) -> None:
    """Override the module's navy/gold brand pair (and everything derived
    from it — chart series colours, section accents) for this render, when
    the report requested a visual theme (see report.py's THEME schema
    field / _sanitize_theme). Every drawing function below reads NAVY/GOLD/
    CHART_COLORS/SECTION_ACCENTS as module globals at call time, so
    reassigning them here before build_pdf renders anything is enough —
    no per-function threading needed. No-ops (keeps the default palette)
    when theme is missing or its colours don't parse as valid hex."""
    global NAVY, GOLD, CHART_COLORS, SECTION_ACCENTS
    NAVY = _DEFAULT_NAVY
    GOLD = _DEFAULT_GOLD
    CHART_COLORS = [NAVY, GOLD, TEAL, RED, SLATE, OLIVE, BURGUNDY, GREEN]
    SECTION_ACCENTS = [GOLD, TEAL, GREEN, OLIVE, RED, SLATE]
    if not theme:
        return
    new_navy = _hex_to_rgb01(theme.get("primaryColor", "")) if theme.get("primaryColor") else None
    new_gold = _hex_to_rgb01(theme.get("accentColor", "")) if theme.get("accentColor") else None
    if new_navy is None and new_gold is None:
        return
    NAVY = new_navy or NAVY
    GOLD = new_gold or GOLD
    CHART_COLORS = [NAVY, GOLD, TEAL, RED, SLATE, OLIVE, BURGUNDY, GREEN]
    SECTION_ACCENTS = [GOLD, TEAL, GREEN, OLIVE, RED, SLATE]
    log.info("PDF: applied custom theme — navy=%s gold=%s", theme.get("primaryColor"), theme.get("accentColor"))



def fmt_inr(value_str: str) -> str:
    """Format a numeric string into Indian number format with Rs. prefix.
    E.g. '1224826.38' -> '12,24,826.38' (crore label added by caller).
    Leaves non-numeric strings untouched.
    """
    if not value_str:
        return value_str
    s = str(value_str).strip()
    # Strip known currency prefixes/symbols and thousands separators — but do
    # NOT touch the decimal point in the number itself. The previous version
    # used a single character-class regex `[Rs.₹$€£,\s]` which strips ANY "."
    # anywhere in the string, so "1,000.80" lost its decimal point entirely
    # and became "100080" — a 100x magnitude error (e.g. CMP ₹1,000.80
    # rendered as Rs. 1,00,080). Strip whole currency tokens first instead.
    cleaned = s
    for token in ("Rs.", "Rs", "₹", "$", "€", "£", "Cr", "cr"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.replace(",", "").replace(" ", "").strip()
    try:
        num = float(cleaned)
    except (ValueError, TypeError):
        return s  # not a number — return as-is
    is_negative = num < 0
    num = abs(num)
    # Preserve decimal precision whenever the source value actually had a
    # fractional part — dropping it isn't a style choice, it's data loss.
    # This matters most for sub-$10 figures (e.g. stock prices like
    # "1.592667" or "2.364667") where the bare integer part ("1", "2")
    # discards the entire meaningful number, not just its formatting.
    had_decimal = "." in cleaned
    num_rounded = round(num, 2) if had_decimal else num
    int_part = int(num_rounded)
    frac_cents = round((num_rounded - int_part) * 100)
    if frac_cents >= 100:  # rounding carried over (e.g. 4.999 -> 5.00)
        int_part += 1
        frac_cents = 0
    # Format integer part in Indian style
    s_int = str(int_part)
    if len(s_int) <= 3:
        formatted = s_int
    else:
        last3 = s_int[-3:]
        rest = s_int[:-3]
        groups = []
        while len(rest) > 2:
            groups.append(rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.append(rest)
        formatted = ",".join(reversed(groups)) + "," + last3
    if had_decimal:
        formatted += f".{frac_cents:02d}"
    if is_negative:
        formatted = "-" + formatted
    return formatted


def _coerce_value(v) -> float:
    """Safely coerce a chart data value to float.
    Handles ints, floats, and strings like '-3%', '1,25,957', '₹72119', '$1820.5'.
    Returns 0.0 on anything unparseable.
    """
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        # Strip currency symbols, commas, spaces, percent signs
        cleaned = re.sub(r"[₹$€£,\s]", "", v).replace("%", "").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
    return 0.0


# ─── Chart data validator ─────────────────────────────────────────────────────
def _is_valid_chart(spec: dict) -> bool:
    """Return False for charts with no data, all-zero, or all-identical values."""
    chart_type = normalize_chart_type(spec.get("type") or spec.get("chart_type") or spec.get("chartType") or "bar")
    spec = dict(spec); spec["type"] = chart_type
    if chart_type == "table":
        cols = spec.get("columns") or []
        rows = spec.get("rows") or []
        return len(cols) >= 2 and len(rows) >= 1

    chart_type = spec.get("type", "bar")

    # Heatmap carries no "series" at all — a rows×columns matrix instead.
    if chart_type == "heatmap":
        rows, cols, values = spec.get("rows") or [], spec.get("columns") or [], spec.get("values") or []
        return (len(rows) >= 2 and len(cols) >= 2 and len(values) == len(rows)
                and all(isinstance(r, list) and len(r) == len(cols) for r in values))

    series = spec.get("series") or []
    if not series:
        return False

    # Box plot points carry min/q1/median/q3/max instead of "value" — the
    # generic value-based checks below don't apply to that shape.
    if chart_type == "boxplot":
        pts = series[0].get("data") or []
        if not pts:
            return False
        keys = ("min", "q1", "median", "q3", "max")
        return all(d.get(k) is not None for d in pts for k in keys)

    # Bullet points carry "value" + "target" — a single KPI is a perfectly
    # valid bullet chart, so skip the "identical values across points" check
    # below (which is about ranked/comparison charts, not a KPI-vs-target one).
    if chart_type == "bullet":
        pts = series[0].get("data") or []
        return bool(pts) and all(d.get("value") is not None and d.get("target") is not None for d in pts)

    # Candlestick points carry open/high/low/close instead of "value" — the
    # generic value-based checks below (identical values, unique labels)
    # don't apply to that shape, so validate it separately and return early.
    if chart_type in {"candlestick", "ohlc"}:
        pts = series[0].get("data") or []
        if len(pts) < 2:
            return False
        labels = [str(p.get("label", "")) for p in pts]
        if len(set(labels)) < len(labels):
            return False
        for d in pts:
            o, hi, lo, cl = (_coerce_value(d.get(k, 0)) for k in ("open", "high", "low", "close"))
            if hi < lo:
                return False
            if o == 0 and hi == 0 and lo == 0 and cl == 0:
                return False
        return True

    # For multi-series (comparison charts): validate each series individually.
    # Single-point series are allowed (e.g. a one-bar snapshot or a single
    # comparison value per company) — line charts still need at least 2
    # points per series since a single point can't be plotted as a line.
    # A waterfall needs at least 3 stages (e.g. opening total, one move,
    # closing total) to read as a bridge rather than a single before/after
    # bar, and a sparkline needs enough points to actually show a "shape"
    # of trend rather than a near-straight line between 2-3 dots.
    for s in series:
        pts = s.get("data") or []
        if chart_type == "line":
            min_pts = 2
        elif chart_type == "waterfall":
            min_pts = 3
        elif chart_type == "sparkline":
            min_pts = 4
        else:
            min_pts = 1
        if len(pts) < min_pts:
            return False

    # All values across all series
    all_data = [d for s in series for d in (s.get("data") or [])]
    values   = [_coerce_value(d.get("value", 0)) for d in all_data]
    if not values:
        return False
    # A single overall data point trivially has one "unique" value — only
    # reject for flat/identical values when there's more than one point.
    if len(values) > 1 and len(set(values)) <= 1:
        return False
    if max(abs(v) for v in values) == 0:
        return False

    # For single-series: labels must be unique within the series
    # For multi-series: labels within EACH series must be unique
    for s in series:
        labels = [d.get("label", "") for d in (s.get("data") or [])]
        if len(set(labels)) < len(labels):
            return False

    return True



# ─── Logo loader ──────────────────────────────────────────────────────────────
def _load_logo_bytes(logo_b64: str = "") -> bytes | None:
    """Load logo from: (1) request-supplied base64, (2) LOGO_B64 env var, (3) filesystem."""
    # 1. Logo passed directly in the PDF request body (most reliable)
    if logo_b64:
        try:
            return base64.b64decode(logo_b64)
        except Exception as e:
            log.debug("Logo: request-supplied base64 failed to decode (%s)", e)

    # 2. Environment variable (set once at deploy time)
    b64 = os.environ.get("LOGO_B64", "")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as e:
            log.debug("Logo: LOGO_B64 env var failed to decode (%s)", e)

    # 2b. Explicit path override via env var (most reliable for non-monorepo deploys)
    logo_path_env = os.environ.get("LOGO_PATH", "")
    if logo_path_env:
        p = Path(logo_path_env)
        if p.exists():
            try:
                return p.read_bytes()
            except Exception as e:
                log.debug("Logo: LOGO_PATH file failed to read (%s)", e)

    # 3. Filesystem search — covers local dev and various deploy layouts
    _here = Path(__file__).resolve().parent          # routes/
    _backend = _here.parent                          # backend/
    _project = _backend.parent                       # project root
    _frontend_pub = _project / "frontend" / "public"

    search_dirs = [
        _frontend_pub,
        _project / "public",
        _backend / "public",
        _backend / "static",
        _backend / "assets",
        _backend,
        _backend.parent / "frontend" / "public",
        _project,
        _project.parent / "frontend" / "public",
        Path(os.getcwd()),
        Path(os.getcwd()) / "public",
        Path(os.getcwd()) / "static",
        Path(os.getcwd()) / "frontend" / "public",
        Path(os.getcwd()).parent / "frontend" / "public",
        Path("/app"),
        Path("/app/public"),
        Path("/app/static"),
        Path("/app/frontend/public"),
        Path("/opt/render/project/src/frontend/public"),  # Render.com
        Path("/opt/render/project/src/backend/public"),
    ]
    logo_names = [
        "growth-gradual-logo.png",
        "growth-gradual-logo.jpg",
        "growth-gradual-logo.jpeg",
        "growth-gradual-logo-transparent.jpeg",
        "logo.png",
        "logo.jpg",
        "logo.jpeg",
    ]
    for d in search_dirs:
        for name in logo_names:
            p = d / name
            if p.exists():
                return p.read_bytes()
    return None

def _draw_logo(c, logo_bytes, x, y, width, height):
    """Draw logo, preserving alpha transparency for PNGs with transparent backgrounds."""
    from reportlab.lib.utils import ImageReader
    if not logo_bytes:
        return False
    try:
        img_reader = ImageReader(io.BytesIO(logo_bytes))
        pil_img = getattr(img_reader, "_image", None)
        has_alpha = pil_img is not None and pil_img.mode in ("RGBA", "LA", "PA")
        mask = "auto" if has_alpha else None
        c.drawImage(img_reader, x, y, width=width, height=height,
                    mask=mask, preserveAspectRatio=True)
        return True
    except Exception:
        return False


def _flatten_to_rgb(pil_img):
    """Safely convert any PIL image mode to opaque RGB for JPEG encoding.

    Two distinct bugs this fixes:
    1. Images with an alpha channel (RGBA/LA, or palette images carrying a
       "transparency" entry) raise `OSError: cannot write mode RGBA as JPEG`
       if saved as-is — JPEG has no alpha channel. Any uploaded screenshot
       or web-sourced PNG with transparency would silently fail to render
       (the caller's try/except swallows it) — the image placeholder would
       just vanish from the PDF with nothing in its place.
    2. Naively calling `.convert("RGB")` on an RGBA image *drops* the alpha
       channel without compositing — whatever RGB values were stored under
       the transparent pixels (commonly black) show through as solid black
       regions where the image should have been transparent/white.

    Compositing onto a white background first avoids both: the image
    always saves cleanly as JPEG, and transparent areas render as white
    instead of black.
    """
    from PIL import Image as _PILImage
    if pil_img.mode in ("RGBA", "LA") or (pil_img.mode == "P" and "transparency" in pil_img.info):
        rgba = pil_img.convert("RGBA")
        bg = _PILImage.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if pil_img.mode != "RGB":
        return pil_img.convert("RGB")
    return pil_img


@lru_cache(maxsize=6)
def _generate_cover_texture(w_pt: int, h_pt: int, base_rgb: tuple, scale: float = 1.5) -> bytes:
    """
    Procedurally generate a cracked-mosaic / crumpled-fabric canvas texture
    (PNG bytes) for the cover background: a jittered-lattice tessellation of
    small irregular tiles (each a slightly different shade of the brand
    navy, separated by a darker grout line), topped with a soft tonal cloud
    and fine grain — matches the small cellular crackle look of the brand's
    reference cover rather than a flat rect or a smooth linen weave. Built
    purely with PIL (already a hard dependency), cached per (size, colour)
    since every cover in a given process run shares the same values.
    """
    from PIL import Image, ImageDraw, ImageFilter

    W, H = max(1, int(w_pt * scale)), max(1, int(h_pt * scale))
    base = tuple(int(round(v * 255)) for v in base_rgb)

    img = Image.new("RGB", (W, H), base)
    draw = ImageDraw.Draw(img, "RGB")
    rnd = random.Random(11)

    # 1) Jittered-lattice mosaic — adjacent tiles share exact corner points
    # (computed once per lattice vertex, reused by all 4 tiles touching it)
    # so the tessellation has no gaps or overlaps, just irregular cell shapes.
    cell = max(8, round(15 * scale))
    jitter = cell * 0.42
    cols, rows = W // cell + 3, H // cell + 3
    pts = {}
    for gy in range(rows):
        for gx in range(cols):
            pts[(gx, gy)] = (
                gx * cell + rnd.uniform(-jitter, jitter),
                gy * cell + rnd.uniform(-jitter, jitter),
            )
    grout = tuple(max(0, v - 24) for v in base)
    for gy in range(rows - 1):
        for gx in range(cols - 1):
            quad = [pts[(gx, gy)], pts[(gx + 1, gy)], pts[(gx + 1, gy + 1)], pts[(gx, gy + 1)]]
            d = rnd.uniform(-16, 14)
            fill = tuple(max(0, min(255, int(v + d))) for v in base)
            draw.polygon(quad, fill=fill, outline=grout)

    # 2) Soft tonal cloud — large-scale variation so the mosaic doesn't read
    # as perfectly uniform corner to corner.
    sw, sh = max(2, W // 10), max(2, H // 10)
    cloud = Image.new("L", (sw, sh))
    cloud.putdata([rnd.randint(90, 175) for _ in range(sw * sh)])
    cloud = cloud.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(max(1, W / 50)))
    lighter = tuple(min(255, v + 18) for v in base)
    tint = Image.new("RGB", (W, H), lighter)
    img = Image.composite(tint, img, cloud.point(lambda p: int(p * 0.22)))

    # 3) Fine grain speckle — per-pixel noise from os.urandom (fast, no Python loop)
    grain = Image.frombytes("L", (W, H), os.urandom(W * H))
    grain_rgb = Image.merge("RGB", (grain, grain, grain))
    img = Image.blend(img, grain_rgb, 0.03)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    """Linear-blend two 0-1 RGB tuples: t=0 -> c1, t=1 -> c2."""
    return tuple(c1[i] * (1 - t) + c2[i] * t for i in range(3))


def detect_domain(question: str) -> str:
    q = question.lower()
    if re.search(r"\b(stock|share|market|nse|bse|sensex|nifty|sebi|ipo|equity|mutual fund|etf|trading|portfolio|invest)\b", q):
        return "Markets & Equities"
    if re.search(r"\b(crypto|bitcoin|ethereum|blockchain|defi|web3|token)\b", q):
        return "Crypto & Blockchain"
    if re.search(r"\b(macro|gdp|inflation|rbi|fed|interest rate|cpi|wpi|economy|fiscal|monetary)\b", q):
        return "Macroeconomics"
    if re.search(r"\b(ai|artificial intelligence|machine learning|llm|gpt|neural|model|deep learning)\b", q):
        return "Artificial Intelligence"
    if re.search(r"\b(tech|technology|software|startup|saas|cloud|semiconductor|chip)\b", q):
        return "Technology"
    if re.search(r"\b(health|pharma|drug|clinical|fda|medical|biotech|cancer|vaccine|disease)\b", q):
        return "Healthcare & Pharma"
    if re.search(r"\b(sport|cricket|ipl|football|tennis|nba|fifa|athlete|league|match|tournament)\b", q):
        return "Sports"
    if re.search(r"\b(climate|energy|solar|wind|renewable|oil|gas|carbon|esg|environment)\b", q):
        return "Energy & Climate"
    if re.search(r"\b(real estate|property|realty|reit|land|housing|construction)\b", q):
        return "Real Estate"
    # Generic fallback — deliberately NOT "Research Intelligence": the cover
    # page builds its overline as f"{domain.upper()} INTELLIGENCE", so a
    # domain that already ends in "Intelligence" doubles up into the
    # "RESEARCH INTELLIGENCE INTELLIGENCE" label bug.
    return "Research"


# ─── Safe text — strip chars Helvetica can't render ──────────────────────────
# Base14 Helvetica in ReportLab is drawn via WinAnsiEncoding (cp1252). Any
# character outside that codepage silently comes out as a "tofu" ■ block
# instead of raising — which is exactly what LLM-generated report/edit text
# tends to sneak in: things like U+2011 NON-BREAKING HYPHEN or U+2010 HYPHEN
# in compound words ("China‑related", "cyber‑insurance", "break‑even"),
# U+2212 MINUS SIGN, or stray zero-width/formatting characters. En/em dashes
# (\u2013 / \u2014) are NOT rewritten — cp1252 already has them.
_UNICODE_TEXT_REPLACEMENTS = {
    "₹": "Rs.",
    "\u20b9": "Rs.",              # ₹ (alternate codepoint form)
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2026": "...",
    "\u00a0": " ",                 # non-breaking space -> normal space
    "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",  # zero-width chars
    # Hyphen-like characters that LOOK like "-" but aren't in cp1252 — the
    # actual root cause of the "China■related" style artifacts.
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2015": "-",
    "\u2043": "-", "\u2212": "-",
}


def _safe_text(text: str) -> str:
    """Rewrite characters ReportLab's base14 Helvetica can't render so they
    never reach the PDF as a ■ tofu block. First applies known 1:1
    replacements (smart quotes, non-ASCII hyphens, the rupee sign, etc.),
    then — as a safety net for anything not explicitly listed above — folds
    any character still outside cp1252 to its closest ASCII form (e.g. "é"
    -> "e") via Unicode decomposition, dropping it only if that also fails,
    rather than letting it silently render as a black square."""
    if not text:
        return text
    for bad, good in _UNICODE_TEXT_REPLACEMENTS.items():
        if bad in text:
            text = text.replace(bad, good)
    try:
        text.encode("cp1252")
        return text
    except UnicodeEncodeError:
        pass
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
            continue
        except UnicodeEncodeError:
            pass
        # Fold accented/typographic variants to plain ASCII where possible
        # (e.g. "é" -> "e", "ﬁ" -> "fi" via compatibility decomposition).
        ascii_ch = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii")
        out.append(ascii_ch)  # empty string if nothing sensible survives — dropped, not tofu
    return "".join(out)


def _truncate_words(text: str, limit: int) -> str:
    """Truncate to at most `limit` chars WITHOUT cutting a word in half.
    Backs up to the last whitespace boundary within the limit and appends
    an ellipsis, instead of a hard str[:limit] slice that leaves dangling
    fragments like 'subsequen' when the source text runs long (common for
    LLM-written image captions)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit * 0.4:  # don't over-trim short captions
        cut = cut[:last_space]
    return cut.rstrip(" ,;:.-") + "…"


# ─── Inline markdown stripping ────────────────────────────────────────────────
def _strip_inline(text: str) -> str:
    # Remove bold/italic markers
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Remove any stray leading/trailing asterisks
    text = re.sub(r"^\*+\s*", "", text)
    text = re.sub(r"\s*\*+$", "", text)
    return _safe_text(text.strip())

def _parse_inline_bold(text: str) -> list:
    """Split text into [(is_bold, segment), ...] for inline bold rendering."""
    parts = re.split(r"(\*\*[^*]+\*\*)", text)
    result = []
    for p in parts:
        if p.startswith("**") and p.endswith("**") and len(p) > 4:
            result.append((True, _safe_text(p[2:-2])))
        elif p:
            result.append((False, _safe_text(p)))
    return result


def _draw_rich_line(c, x, y, text, regular_font, bold_font, size, color, max_w, justify=False):
    """Draw a line with inline **bold** support. Returns width drawn.

    justify=True distributes the line's slack evenly between word gaps so
    its right edge lands on x+max_w — standard justified-paragraph
    behaviour. Callers should only pass justify=True for lines that aren't
    the last line of their paragraph/bullet/numbered item: the final line
    of a justified paragraph conventionally stays left-aligned/ragged
    rather than being stretched, since a short last line stretched to full
    width reads as a spacing bug, not "justified" text.
    """
    segments = _parse_inline_bold(text)
    # Flatten segments into a per-word list (still tagged bold/not-bold) so
    # justification spacing can be computed across bold/regular boundaries —
    # a bold run is still just words for spacing purposes.
    words: list[tuple[bool, str]] = []
    for is_bold, seg in segments:
        for w in seg.split(" "):
            if w:
                words.append((is_bold, w))

    if not words:
        return 0

    space_w = c.stringWidth(" ", regular_font, size)

    if justify and len(words) > 1:
        natural_w = sum(
            c.stringWidth(w, bold_font if is_bold else regular_font, size)
            for is_bold, w in words
        ) + space_w * (len(words) - 1)
        extra = max_w - natural_w
        gap = space_w + (extra / (len(words) - 1)) if extra > 0 else space_w
    else:
        gap = space_w

    cx = x
    for idx, (is_bold, w) in enumerate(words):
        font = bold_font if is_bold else regular_font
        c.setFont(font, size)
        c.setFillColorRGB(*color)
        c.drawString(cx, y, w)
        cx += c.stringWidth(w, font, size)
        if idx < len(words) - 1:
            cx += gap
    return cx - x


# ─── Markdown tokeniser ───────────────────────────────────────────────────────
def _tokenise(md: str):
    """Yield token dicts from markdown text."""
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        if re.match(r"^\[CHART_\d+\]", stripped):
            m = re.match(r"^\[CHART_(\d+)\]", stripped)
            yield {"type": "chart_placeholder", "index": int(m.group(1)) - 1}

        elif re.match(r"^\[FILE_IMG_\d+\]", stripped) or re.match(r"^\[PAGE_IMG_\d+\]", stripped):
            m = re.match(r"^\[FILE_IMG_(\d+)\]", stripped) or re.match(r"^\[PAGE_IMG_(\d+)\]", stripped)
            yield {"type": "file_img_placeholder", "index": int(m.group(1)) - 1}

        elif re.match(r"^\[WEB_IMG_\d+\]", stripped):
            m = re.match(r"^\[WEB_IMG_(\d+)\]", stripped)
            yield {"type": "web_img_placeholder", "index": int(m.group(1)) - 1}

        elif stripped.startswith("#### "):
            yield {"type": "h4", "text": stripped[5:].strip()}
        elif stripped.startswith("### "):
            yield {"type": "h3", "text": stripped[4:].strip()}
        elif stripped.startswith("## "):
            yield {"type": "h2", "text": stripped[3:].strip()}
        elif stripped.startswith("# "):
            yield {"type": "h1", "text": stripped[2:].strip()}

        elif stripped == "---":
            yield {"type": "hr"}

        elif re.match(r"^```", stripped):
            # Fenced code block. The LLM occasionally wraps an ASCII
            # diagram/roadmap in a ```...``` fence; previously neither the
            # opening/closing ``` fence markers nor this branch existed at
            # all, so each fence line fell through to the generic "para"
            # branch below and got rendered as literal, visible backtick
            # text with the diagram lines as ordinary paragraphs around it.
            # Now: consume the fence, and yield its contents as normal
            # paragraphs (still no attempt to typeset ASCII-art tables /
            # arrows nicely — that's a separate improvement — but at least
            # the ``` markers themselves never show up as visible text).
            i += 1
            while i < len(lines) and not re.match(r"^```", lines[i].rstrip()):
                inner = lines[i].strip()
                if inner:
                    yield {"type": "para", "text": inner}
                i += 1
            # i now points at the closing ``` (or EOF) — the outer i += 1
            # at the bottom of the loop advances past it.

        elif stripped.startswith("| "):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                row_line = lines[i]
                if re.match(r"^\|[\s|:-]+\|$", row_line):
                    i += 1
                    continue
                cells = [c.strip() for c in row_line.strip("|").split("|")]
                rows.append(cells)
                i += 1
            if rows:
                # Filter out data rows where ALL non-first cells are empty/placeholder
                _EMPTY_CELL = {
                    "", "-", "—", "n/a", "na",
                    "not available", "data not available",
                    "not extractable", "data not extractable",
                    "no data", "tbd", "pending", "unknown",
                }
                header = rows[0] if rows else []
                filtered = [header] if header else []
                for row in rows[1:]:
                    data_cells = row[1:] if len(row) > 1 else row
                    if any(cell.strip().lower() not in _EMPTY_CELL for cell in data_cells):
                        filtered.append(row)
                # Only yield if header + at least 1 real data row
                if len(filtered) >= 2:
                    yield {"type": "table", "rows": filtered}
            continue

        elif re.match(r"^>\s?", stripped):
            # Blockquote — one or more consecutive "> " lines collapse into a
            # single pull-quote/insight-callout token, rendered as a distinct
            # card (see the "quote" branch below) rather than a plain paragraph.
            q_lines = []
            while i < len(lines) and re.match(r"^>\s?", lines[i].rstrip()):
                q_lines.append(re.sub(r"^>\s?", "", lines[i].rstrip()))
                i += 1
            yield {"type": "quote", "text": " ".join(l for l in q_lines if l).strip()}
            continue

        elif re.match(r"^\s*[-*+]\s+", stripped):
            yield {"type": "bullet", "text": re.sub(r"^\s*[-*+]\s+", "", stripped)}

        elif re.match(r"^\d+\.\s+", stripped):
            m = re.match(r"^(\d+)\.\s+(.*)", stripped)
            yield {"type": "numbered", "num": m.group(1), "text": m.group(2)}

        elif stripped:
            # Split any inline [CHART_n], [WEB_IMG_n], [FILE_IMG_n] placeholders
            # (also accepts legacy [PAGE_IMG_n] for safety) that the LLM
            # embedded mid-sentence rather than on their own line. We tokenise
            # the paragraph by splitting on placeholder patterns and yielding
            # each part separately so the renderer handles them correctly.
            _INLINE_PH = re.compile(
                r"(\[CHART_\d+\]|\[WEB_IMG_\d+\]|\[FILE_IMG_\d+\]|\[PAGE_IMG_\d+\])"
            )
            parts = _INLINE_PH.split(stripped)
            if len(parts) == 1:
                # No placeholders — plain paragraph
                yield {"type": "para", "text": stripped}
            else:
                for part in parts:
                    if not part:
                        continue
                    cm = re.match(r"^\[CHART_(\d+)\]$", part)
                    wm = re.match(r"^\[WEB_IMG_(\d+)\]$", part)
                    pm = re.match(r"^\[FILE_IMG_(\d+)\]$", part) or re.match(r"^\[PAGE_IMG_(\d+)\]$", part)
                    if cm:
                        yield {"type": "chart_placeholder", "index": int(cm.group(1)) - 1}
                    elif wm:
                        yield {"type": "web_img_placeholder", "index": int(wm.group(1)) - 1}
                    elif pm:
                        yield {"type": "file_img_placeholder", "index": int(pm.group(1)) - 1}
                    else:
                        t = part.strip()
                        if t:
                            yield {"type": "para", "text": t}

        i += 1


# ─── Text word-wrap helper ────────────────────────────────────────────────────
def _wrap(canvas, text: str, font: str, size: float, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if canvas.stringWidth(test, font, size) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_cell(canvas, text: str, font: str, size: float, max_width: float) -> str:
    """Truncate to the actual rendered pixel width (not a fixed char count),
    so long cell values never overflow into the next column. Adds an
    ellipsis when truncated; returns the original text untouched if it
    already fits."""
    if canvas.stringWidth(text, font, size) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if canvas.stringWidth(text[:mid] + ell, font, size) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell if lo > 0 else ell


_SIGNED_CELL_RE = re.compile(r"^[^\d+\-]*([+\-])\s*[₹$]?\s*[\d,.]+")
_PAREN_NEG_RE   = re.compile(r"^[^\d(]*\(\s*[\d,.]")


def _signed_cell_color(cell_str: str):
    """Return GREEN/RED for a table cell that is a signed gain/loss figure
    (e.g. '+6.4%', '-9.56%', '(1,234)' as accounting-style negative), or
    None for anything else (names, dates, unsigned levels) so those keep
    the row's default text color."""
    s = cell_str.strip()
    if not s:
        return None
    m = _SIGNED_CELL_RE.match(s)
    if m:
        return GREEN if m.group(1) == "+" else RED
    if _PAREN_NEG_RE.match(s):
        return RED
    return None


# ─── Chart renderers ──────────────────────────────────────────────────────────
def _axis_titles(c, spec, x0, y0, PL, PB, pw, ph):
    """Draw the axis TITLE text (e.g. 'Return (%)' / 'Sector') distinct from the
    numeric/category tick labels the bar/line renderers already draw. Falls back
    to the unit string for the Y title when the LLM didn't supply an explicit
    yLabel, so a chart is never left with unlabeled axes."""
    x_label = _safe_text((spec.get("xLabel") or "").strip())
    y_label = _safe_text((spec.get("yLabel") or "").strip())
    unit = _safe_text(spec.get("unit", ""))
    if not y_label and unit:
        y_label = unit
    # Drop an xLabel that just repeats the chart's own title/series name
    # (e.g. title "S&P 500 Session Volatility" + xLabel "S&P 500") — it adds
    # no information and, on charts with only a couple of category ticks,
    # its centred position can land right on top of the last (rotated) tick
    # label instead of clear of it.
    title = _safe_text((spec.get("title") or "").strip()).lower()
    if x_label and title and (x_label.lower() in title or title in x_label.lower()):
        x_label = ""
    if x_label:
        c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(x0 + PL + pw / 2, y0 + 2, x_label[:40])
    if y_label:
        c.saveState()
        c.translate(x0 + 13, y0 + PB + ph / 2)
        c.rotate(90)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(0, 0, y_label[:30])
        c.restoreState()


def _bar(c, spec, x0, y0, w, h):
    series_list = spec.get("series") or [{}]
    data = series_list[0].get("data") or []
    if not data:
        return
    unit  = spec.get("unit", "")
    n_ser = len(series_list)
    # Multi-series composition/breakdown charts (e.g. "Portfolio Allocation by
    # Fund" — Equity/Debt/Cash per fund) are meant to render as a STACKED
    # column, not a grouped one — the parts sum to a whole per label. The
    # model sets spec["stacked"]=true for these; anything else (comparisons
    # across entities) stays grouped side-by-side as before.
    stacked = bool(spec.get("stacked")) and n_ser > 1

    if stacked:
        # Y scale is the tallest CUMULATIVE stack across labels, not the
        # tallest individual value.
        n = max(len(data), 1)
        totals = []
        for i in range(n):
            tot = sum(abs(_coerce_value((s.get("data") or [{}])[i].get("value", 0)))
                       for s in series_list if i < len(s.get("data") or []))
            totals.append(tot)
        max_v = max(totals) if totals else 1
    else:
        # All values across all series for unified Y scale
        all_vals = [abs(_coerce_value(d.get("value", 0))) for s in series_list for d in s.get("data", [])]
        max_v    = max(all_vals) if all_vals else 1
        n        = max(len(data), 1)

    has_legend = n_ser > 1
    LEGEND_H   = 16 if has_legend else 0
    PL, PB     = 52, 40   # PB includes room below the category tick labels for the axis title
    pw         = w - PL - 12
    ph         = h - PB - 24 - LEGEND_H
    sp         = pw / n
    # Per-series bar width — grouped layout; stacked uses one column per label
    bw_total   = min(sp * 0.78, 70.0)
    bw         = max(4, bw_total if stacked else bw_total / n_ser)

    safe_unit = _safe_text(unit)
    # Grid lines
    for f in (0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        lbl = f"{f * max_v:.1f}{safe_unit}" if max_v < 10 else f"{f * max_v:.0f}{safe_unit}"
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, lbl)

    c.setStrokeColorRGB(0.78, 0.82, 0.88); c.setLineWidth(0.8)
    c.line(x0 + PL, y0 + PB, x0 + PL + pw, y0 + PB)

    if stacked:
        # Draw each label's column as segments stacked bottom-to-top, one
        # segment per series, in series order — the classic stacked-bar look.
        for i, d in enumerate(data):
            if i >= n:
                continue
            bx = x0 + PL + i * sp + (sp - bw) / 2
            running_y = y0 + PB
            for si, ser in enumerate(series_list):
                ser_data = ser.get("data") or []
                if i >= len(ser_data):
                    continue
                v  = abs(_coerce_value(ser_data[i].get("value", 0)))
                bh = max(1.5, (v / max_v) * ph) if v else 0
                color = CHART_COLORS[si % len(CHART_COLORS)]
                c.setFillColorRGB(*color)
                c.rect(bx, running_y, bw - 1, bh, fill=1, stroke=0)
                # Segment value label — only if the segment is tall enough
                # to hold text without overlapping its neighbors.
                if bh > 9:
                    vs = f"{v:.0f}{safe_unit}" if max_v >= 10 else f"{v:.1f}{safe_unit}"
                    c.setFillColorRGB(*WHITE); c.setFont("Helvetica-Bold", 5.5)
                    c.drawCentredString(bx + (bw - 1) / 2, running_y + bh / 2 - 2, vs)
                running_y += bh
            # Total label above the stack
            total_v = totals[i] if i < len(totals) else 0
            ts = f"{total_v:.0f}{safe_unit}" if max_v >= 10 else f"{total_v:.1f}{safe_unit}"
            c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(bx + (bw - 1) / 2, running_y + 3, ts)
    else:
        # Draw grouped bars
        for si, ser in enumerate(series_list):
            ser_data = ser.get("data") or []
            color    = CHART_COLORS[si % len(CHART_COLORS)]
            for i, d in enumerate(ser_data):
                if i >= n: continue
                v  = _coerce_value(d.get("value", 0))
                bh = max(2, (abs(v) / max_v) * ph)
                group_x = x0 + PL + i * sp + (sp - bw_total) / 2
                bx = group_x + si * bw
                bx = min(bx, x0 + PL + pw - bw)
                bar_color = RED if v < 0 else color
                c.setFillColorRGB(*bar_color)
                c.rect(bx, y0 + PB, bw - 1, bh, fill=1, stroke=0)
                # Value label on top
                if n_ser == 1 or bw > 14:
                    vs = f"{v:+.1f}{safe_unit}" if safe_unit == "%" else f"{v:.1f}{safe_unit}" if max_v < 10 else f"{v:.0f}{safe_unit}"
                    c.setFillColorRGB(*bar_color); c.setFont("Helvetica-Bold", 5.5)
                    c.drawCentredString(bx + (bw - 1) / 2, y0 + PB + bh + 2, vs)

    # X-axis labels — thin these out once bars get too narrow for every
    # label to fit without the (rotated) text overlapping its neighbor.
    # Mirrors the line chart's step-based label skipping below.
    label_step = max(1, math.ceil(n / 10))
    max_chars  = 18 if sp >= 30 else 12 if sp >= 20 else 8
    for i, d in enumerate(data):
        if i % label_step != 0 and i != n - 1:
            continue
        lbl = d.get("label", "")
        bx  = x0 + PL + i * sp + sp / 2
        c.setFillColorRGB(*GREY)
        if len(lbl) > 8:
            c.saveState()
            c.translate(bx, y0 + PB - 4)
            c.rotate(30)
            c.setFont("Helvetica", 6)
            c.drawString(0, 0, lbl[:max_chars])
            c.restoreState()
        else:
            c.setFont("Helvetica", 7)
            c.drawCentredString(bx, y0 + PB - 11, lbl[:max_chars])

    # Legend for multi-series — wraps to a new row instead of running off
    # the right edge of the card when there are several series or long names.
    if has_legend:
        lx = x0 + PL
        ly = y0 + PB + ph + 14
        right_edge = x0 + PL + pw
        for si, ser in enumerate(series_list):
            color = CHART_COLORS[si % len(CHART_COLORS)]
            sname = ser.get("name", f"Series {si+1}")[:22]
            item_w = 15 + c.stringWidth(sname, "Helvetica", 7) + 22
            if lx + item_w > right_edge and lx > x0 + PL:
                lx = x0 + PL
                ly -= 12
            c.setFillColorRGB(*color)
            c.rect(lx, ly - 4, 12, 6, fill=1, stroke=0)
            c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
            c.drawString(lx + 15, ly - 2, sname)
            lx += item_w

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _line(c, spec, x0, y0, w, h):
    series = spec.get("series") or []
    if not series:
        return
    unit  = spec.get("unit", "")
    safe_unit = _safe_text(unit)
    title = spec.get("title", "")

    # Collect all values across ALL series for unified Y scale
    all_v = [_coerce_value(d.get("value", 0)) for s in series for d in s.get("data", [])]
    if not all_v:
        return
    mn, mx = min(all_v), max(all_v)
    rng = mx - mn
    if rng < 1e-9:
        rng = max(abs(mx) * 0.1, 1.0)
        mn -= rng / 2
        mx += rng / 2

    has_legend = len(series) > 1
    LEGEND_H   = 16 if has_legend else 0
    PL, PB     = 52, 40   # PB includes room below the category tick labels for the axis title
    pw         = w - PL - 12
    ph         = h - PB - 24 - LEGEND_H

    # Grid lines
    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        lbl = f"{mn + f * rng:.2f}{safe_unit}" if rng < 5 else f"{mn + f * rng:.1f}{safe_unit}"
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, lbl)

    # X axis baseline
    c.setStrokeColorRGB(0.78, 0.82, 0.88); c.setLineWidth(0.8)
    c.line(x0 + PL, y0 + PB, x0 + PL + pw, y0 + PB)

    # Draw each series
    max_pts = max(len(s.get("data", [])) for s in series)
    step    = max(1, max_pts // 8)

    for si, s in enumerate(series):
        pts = s.get("data", [])
        if not pts:
            continue
        color  = CHART_COLORS[si % len(CHART_COLORS)]
        n      = len(pts)
        coords = [
            (x0 + PL + (j / max(n - 1, 1)) * pw,
             y0 + PB + ((_coerce_value(d.get("value", 0)) - mn) / rng) * ph)
            for j, d in enumerate(pts)
        ]
        # Line
        c.setStrokeColorRGB(*color); c.setLineWidth(2.0)
        p = c.beginPath(); p.moveTo(*coords[0])
        for cx2, cy2 in coords[1:]:
            p.lineTo(cx2, cy2)
        c.drawPath(p, fill=0, stroke=1)
        # Dots + value labels on key points
        c.setFillColorRGB(*color)
        for j, (px2, py2) in enumerate(coords):
            show = (j % step == 0 or j == n - 1)
            c.circle(px2, py2, 2.8 if show else 1.8, fill=1, stroke=0)
            if show and has_legend:
                # Small value tooltip above dot
                val = _coerce_value(pts[j].get("value", 0))
                c.setFont("Helvetica-Bold", 5.5)
                c.setFillColorRGB(*color)
                c.drawCentredString(px2, py2 + 5, f"{val:.1f}{safe_unit}")
                c.setFillColorRGB(*color)

        # For a single-series trend (e.g. an index/stock level across
        # sessions) — callout the peak and trough points in gold, echoing
        # the annotated-trend style of the sample report rather than
        # leaving a bare line with no narrative markers.
        if not has_legend and n >= 3:
            vals = [_coerce_value(d.get("value", 0)) for d in pts]
            hi_j, lo_j = vals.index(max(vals)), vals.index(min(vals))
            for j in (hi_j, lo_j):
                if j == hi_j == lo_j:
                    continue
                px2, py2 = coords[j]
                is_hi = (j == hi_j)
                c.setFillColorRGB(*GOLD)
                c.circle(px2, py2, 3.2, fill=1, stroke=0)
                vs = f"{vals[j]:.1f}{safe_unit}" if rng < 5 else f"{vals[j]:,.0f}{safe_unit}"
                c.setFont("Helvetica-Bold", 6)
                label_y = py2 + 8 if is_hi else py2 - 11
                # Keep the callout from being clipped off the top/bottom of
                # the plotted area.
                label_y = min(max(label_y, y0 + PB + 6), y0 + PB + ph + 4)
                c.drawCentredString(px2, label_y, vs)

    # X-axis labels — use full label, no truncation
    labels = series[0].get("data", [])
    for j, d in enumerate(labels):
        if j % step == 0 or j == len(labels) - 1:
            px2 = x0 + PL + (j / max(len(labels) - 1, 1)) * pw
            lbl = d.get("label", "")
            # Rotate long labels
            c.setFillColorRGB(*GREY)
            if len(lbl) > 7:
                c.saveState()
                c.translate(px2, y0 + PB - 4)
                c.rotate(30)
                c.setFont("Helvetica", 6)
                c.drawString(0, 0, lbl[:14])
                c.restoreState()
            else:
                c.setFont("Helvetica", 7)
                c.drawCentredString(px2, y0 + PB - 11, lbl[:12])

    # Legend for multi-series — wraps to a new row instead of running off
    # the right edge of the card when there are several series.
    if has_legend:
        lx = x0 + PL
        ly = y0 + PB + ph + 14
        right_edge = x0 + PL + pw
        for si, s in enumerate(series):
            color = CHART_COLORS[si % len(CHART_COLORS)]
            sname = s.get("name", f"Series {si+1}")[:20]
            item_w = 15 + c.stringWidth(sname, "Helvetica", 7) + 20
            if lx + item_w > right_edge and lx > x0 + PL:
                lx = x0 + PL
                ly -= 12
            c.setFillColorRGB(*color)
            c.rect(lx, ly - 4, 12, 6, fill=1, stroke=0)
            c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
            c.drawString(lx + 15, ly - 2, sname)
            lx += item_w

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _pie(c, spec, x0, y0, w, h):
    data = (spec.get("series") or [{}])[0].get("data") or []
    if not data:
        return
    total = sum(abs(_coerce_value(d.get("value", 0))) for d in data) or 1
    R = min(w * 0.38, h * 0.42)
    cx2, cy2 = x0 + R + 12, y0 + h / 2
    angle = math.pi / 2

    for i, d in enumerate(data):
        frac = abs(_coerce_value(d.get("value", 0))) / total
        sweep = frac * 2 * math.pi
        color = CHART_COLORS[i % len(CHART_COLORS)]
        c.setFillColorRGB(*color); c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(1)
        steps = max(4, int(sweep * 20))
        p = c.beginPath(); p.moveTo(cx2, cy2)
        for s in range(steps + 1):
            a = angle + s * sweep / steps
            p.lineTo(cx2 + R * math.cos(a), cy2 + R * math.sin(a))
        p.close(); c.drawPath(p, fill=1, stroke=1)
        angle += sweep

    lx = cx2 + R + 18
    ly = cy2 + R * 0.8
    # Cap the per-row height so the legend never runs past the bottom of the
    # chart card, however many slices there are — with the default spacing
    # (15pt/row) more than ~ (R*1.8)/15 slices would otherwise overflow the
    # card's bottom edge and collide with whatever content follows.
    available_h = max(ly - y0, 30)
    row_h = min(15.0, available_h / max(len(data), 1))
    font_sz = 7 if row_h >= 11 else 6
    for i, d in enumerate(data):
        color = CHART_COLORS[i % len(CHART_COLORS)]
        pct = abs(_coerce_value(d.get("value", 0))) / total * 100
        lbl = d.get("label", "")[:22]
        iy = ly - i * row_h
        box = min(8, row_h - 3)
        c.setFillColorRGB(*color); c.rect(lx, iy - box*0.6, box, box, fill=1, stroke=0)
        c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", font_sz)
        c.drawString(lx + 12, iy - 1, lbl)
        c.setFont("Helvetica-Bold", font_sz)
        c.drawRightString(lx + 12 + 110, iy - 1, f"{pct:.1f}%")


def _waterfall(c, spec, x0, y0, w, h):
    """Waterfall chart: a running total that steps up/down across labeled
    stages (e.g. Opening AUM -> Inflows -> Redemptions -> Closing AUM),
    drawn as floating bars connected stage-to-stage. A data point marked
    "isTotal": true is an ANCHOR — it resets the running total to its own
    value (used for opening/closing balances) instead of adding a delta.
    Datawrapper has no native chart type for this shape (see the early
    return in utils/datawrapper.py's publish_chart), so this always
    renders through this fallback path, Datawrapper token or not."""
    series_list = spec.get("series") or [{}]
    data = series_list[0].get("data") or []
    if not data:
        return
    unit = spec.get("unit", "")
    safe_unit = _safe_text(unit)

    # Walk the stages once to compute each bar's floating [bottom, top]
    # span. Non-total points are deltas off the running cumulative; total
    # points reset the baseline to their own value.
    segments = []  # (label, bottom, top, is_total, raw_value)
    cum = 0.0
    for d in data:
        v = _coerce_value(d.get("value", 0))
        is_total = bool(d.get("isTotal"))
        if is_total:
            bottom, top = 0.0, v
            cum = v
        else:
            bottom = cum
            top = cum + v
            cum = top
        segments.append((d.get("label", ""), min(bottom, top), max(bottom, top), is_total, v))
    if not segments:
        return

    all_edges = [e for seg in segments for e in (seg[1], seg[2])] + [0.0]
    mn, mx = min(all_edges), max(all_edges)
    rng = mx - mn
    if rng < 1e-9:
        rng = max(abs(mx) * 0.1, 1.0)

    n = len(segments)
    PL, PB = 54, 44
    pw = w - PL - 12
    ph = h - PB - 24
    sp = pw / max(n, 1)
    bw = min(sp * 0.6, 60.0)

    def _y(val):
        return y0 + PB + ((val - mn) / rng) * ph

    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        lbl_v = mn + f * rng
        lbl = f"{lbl_v:,.0f}{safe_unit}"
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, lbl)

    zero_y = _y(0.0)
    c.setStrokeColorRGB(0.78, 0.82, 0.88); c.setLineWidth(0.8)
    c.line(x0 + PL, zero_y, x0 + PL + pw, zero_y)

    prev_top_y = None
    prev_bx_end = None
    for i, (lbl, bottom, top, is_total, v) in enumerate(segments):
        bx = x0 + PL + i * sp + (sp - bw) / 2
        by, ty = _y(bottom), _y(top)
        bar_h = max(1.5, abs(ty - by))
        bar_y = min(by, ty)
        color = NAVY if is_total else (GREEN if v >= 0 else RED)
        c.setFillColorRGB(*color)
        c.rect(bx, bar_y, bw, bar_h, fill=1, stroke=0)

        # Dotted connector from the previous bar's landing edge to this one
        if prev_top_y is not None and prev_bx_end is not None:
            c.setStrokeColorRGB(0.72, 0.75, 0.82); c.setLineWidth(0.6)
            c.setDash(2, 2)
            c.line(prev_bx_end, prev_top_y, bx, prev_top_y)
            c.setDash()
        prev_top_y = _y(top)
        prev_bx_end = bx + bw

        vs = f"{v:+,.0f}{safe_unit}" if not is_total else f"{v:,.0f}{safe_unit}"
        c.setFillColorRGB(*color); c.setFont("Helvetica-Bold", 6)
        label_y = bar_y + bar_h + 4 if top >= bottom else bar_y - 8
        label_y = min(max(label_y, y0 + PB + 4), y0 + PB + ph + 6)
        c.drawCentredString(bx + bw / 2, label_y, vs)

        c.setFillColorRGB(*GREY)
        if len(lbl) > 9:
            c.saveState()
            c.translate(bx + bw / 2, y0 + PB - 4)
            c.rotate(30)
            c.setFont("Helvetica", 6)
            c.drawString(0, 0, lbl[:16])
            c.restoreState()
        else:
            c.setFont("Helvetica", 7)
            c.drawCentredString(bx + bw / 2, y0 + PB - 11, lbl[:12])

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _candlestick(c, spec, x0, y0, w, h):
    """OHLC candlestick chart for a single instrument's per-session price
    action. Each data point needs open/high/low/close (not "value") —
    see _is_valid_chart for the shape check. Datawrapper has no native
    candlestick type, so this always renders through this fallback path."""
    series_list = spec.get("series") or [{}]
    data = series_list[0].get("data") or []
    if not data:
        return
    unit = spec.get("unit", "")
    safe_unit = _safe_text(unit)

    def _f(d, k):
        return _coerce_value(d.get(k, 0))

    highs = [_f(d, "high") for d in data]
    lows  = [_f(d, "low") for d in data]
    if not highs or not lows:
        return
    mn, mx = min(lows), max(highs)
    rng = mx - mn
    if rng < 1e-9:
        rng = max(abs(mx) * 0.1, 1.0)
        mn -= rng / 2
        mx += rng / 2

    n = len(data)
    PL, PB = 58, 40
    pw = w - PL - 12
    ph = h - PB - 24
    sp = pw / max(n, 1)
    bw = min(sp * 0.5, 18.0)

    def _y(val):
        return y0 + PB + ((val - mn) / rng) * ph

    for f in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        lbl_v = mn + f * rng
        lbl = f"{lbl_v:,.1f}{safe_unit}" if rng < 10 else f"{lbl_v:,.0f}{safe_unit}"
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, lbl)

    c.setStrokeColorRGB(0.78, 0.82, 0.88); c.setLineWidth(0.8)
    c.line(x0 + PL, y0 + PB, x0 + PL + pw, y0 + PB)

    label_step = max(1, math.ceil(n / 10))
    for i, d in enumerate(data):
        o, hi, lo, cl = _f(d, "open"), _f(d, "high"), _f(d, "low"), _f(d, "close")
        cx2 = x0 + PL + i * sp + sp / 2
        up = cl >= o
        color = GREEN if up else RED
        c.setStrokeColorRGB(*color); c.setLineWidth(1.0)
        c.line(cx2, _y(lo), cx2, _y(hi))
        body_top, body_bot = _y(max(o, cl)), _y(min(o, cl))
        body_h = max(1.5, body_top - body_bot)
        c.setFillColorRGB(*color)
        c.rect(cx2 - bw / 2, body_bot, bw, body_h, fill=1, stroke=0)

        lbl = d.get("label", "")
        if i % label_step == 0 or i == n - 1:
            c.setFillColorRGB(*GREY)
            if len(lbl) > 7:
                c.saveState()
                c.translate(cx2, y0 + PB - 4)
                c.rotate(30)
                c.setFont("Helvetica", 6)
                c.drawString(0, 0, lbl[:12])
                c.restoreState()
            else:
                c.setFont("Helvetica", 7)
                c.drawCentredString(cx2, y0 + PB - 11, lbl[:10])

    # ── Moving-average overlay ──────────────────────────────────────────────
    # Computed directly from the candles' own close prices — no extra data
    # needed from the model — so every candlestick chart gets this for free.
    # A bare wall of red/green candles reads as noisy; a short-window MA line
    # gives the eye a trend to follow, the way any real trading chart does.
    # Window scales down for short series so it still draws on a 5-10 point
    # chart instead of silently vanishing.
    ma_window = 5 if n >= 10 else max(2, n // 2)
    if n >= ma_window + 1:
        closes = [_f(d, "close") for d in data]
        ma_pts = []
        for i in range(ma_window - 1, n):
            avg = sum(closes[i - ma_window + 1:i + 1]) / ma_window
            cx2 = x0 + PL + i * sp + sp / 2
            ma_pts.append((cx2, _y(avg)))
        c.setStrokeColorRGB(*GOLD); c.setLineWidth(1.3)
        p = c.beginPath(); p.moveTo(*ma_pts[0])
        for px2, py2 in ma_pts[1:]:
            p.lineTo(px2, py2)
        c.drawPath(p, fill=0, stroke=1)
        c.setFillColorRGB(*GOLD); c.setFont("Helvetica-BoldOblique", 6.5)
        c.drawString(ma_pts[-1][0] + 4, ma_pts[-1][1] - 2, f"{ma_window}-pd MA")

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _sparkline(c, spec, x0, y0, w, h):
    """Minimal axis-less single-series trend line — for a quick 'shape of
    the trend' visual (e.g. a 30-session price trend) where a fully-labeled
    line chart would be visual overkill. No gridlines, no tick labels, just
    the line, its endpoints, and the first/last values. Datawrapper has no
    equivalent chart type at this stripped-down scale, so this always
    renders through this fallback path."""
    series_list = spec.get("series") or [{}]
    data = series_list[0].get("data") or []
    if len(data) < 2:
        return
    unit = spec.get("unit", "")
    safe_unit = _safe_text(unit)
    vals = [_coerce_value(d.get("value", 0)) for d in data]
    mn, mx = min(vals), max(vals)
    rng = mx - mn
    if rng < 1e-9:
        rng = max(abs(mx) * 0.1, 1.0)
        mn -= rng / 2

    up = vals[-1] >= vals[0]
    color = GREEN if up else RED

    title = _safe_text((spec.get("title") or "").strip())
    TITLE_H = 14 if title else 0
    PAD_X, PAD_TOP, PAD_BOT = 34, 14 + TITLE_H, 16
    pw = w - 2 * PAD_X
    ph = h - PAD_TOP - PAD_BOT
    n = len(vals)
    coords = [
        (x0 + PAD_X + (i / max(n - 1, 1)) * pw, y0 + PAD_BOT + ((v - mn) / rng) * ph)
        for i, v in enumerate(vals)
    ]

    if title:
        c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 7)
        c.drawCentredString(x0 + w / 2, y0 + h - 10, title[:44])

    # ── Soft area fill under the line ───────────────────────────────────────
    # A bare stroke on white reads as thin/unfinished at this small scale;
    # a light tint of the trend colour under the curve gives it the same
    # "finished chart" weight as the full bar/line renders elsewhere in the
    # report, while staying subtle enough not to compete with the line itself.
    fill_color = _mix(WHITE, color, 0.14)
    c.setFillColorRGB(*fill_color)
    fp = c.beginPath()
    fp.moveTo(coords[0][0], y0 + PAD_BOT)
    for px2, py2 in coords:
        fp.lineTo(px2, py2)
    fp.lineTo(coords[-1][0], y0 + PAD_BOT)
    fp.close()
    c.drawPath(fp, fill=1, stroke=0)

    c.setStrokeColorRGB(*color); c.setLineWidth(2.2)
    p = c.beginPath(); p.moveTo(*coords[0])
    for px2, py2 in coords[1:]:
        p.lineTo(px2, py2)
    c.drawPath(p, fill=0, stroke=1)

    c.setFillColorRGB(*color)
    for px2, py2 in (coords[0], coords[-1]):
        c.circle(px2, py2, 2.6, fill=1, stroke=0)

    def _fmt(v):
        return f"{v:,.1f}{safe_unit}" if abs(v) < 100 else f"{v:,.0f}{safe_unit}"

    c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica-Bold", 7)
    c.drawString(coords[0][0], coords[0][1] - 12, _fmt(vals[0]))
    c.setFillColorRGB(*color)
    c.drawRightString(coords[-1][0], coords[-1][1] + 6, _fmt(vals[-1]))


def _bullet(c, spec, x0, y0, w, h):
    """Bullet chart: one horizontal bar per KPI, a light 'target range' track
    behind it, and a vertical tick marking the target value — the classic
    Few-style bullet, simplified to value + single target (no qualitative
    good/satisfactory/poor bands, which the chart spec doesn't carry)."""
    data = (spec.get("series") or [{}])[0].get("data") or []
    if not data:
        return
    unit = _safe_text(spec.get("unit", ""))
    n = len(data)
    PL = 90
    row_h = min(34.0, (h - 20) / max(n, 1))
    pw = w - PL - 16
    max_v = max((max(_coerce_value(d.get("value", 0)), _coerce_value(d.get("target", 0))) for d in data), default=1) or 1
    max_v *= 1.08  # headroom so the target tick near the max isn't clipped at the edge

    top = y0 + h - 10
    for i, d in enumerate(data):
        ry = top - i * row_h - row_h / 2
        v = _coerce_value(d.get("value", 0))
        target = _coerce_value(d.get("target", 0))
        # Track (light background showing the full scale)
        c.setFillColorRGB(0.90, 0.92, 0.97)
        c.rect(x0 + PL, ry - 7, pw, 14, fill=1, stroke=0)
        # Value bar
        bw = max(1, (v / max_v) * pw)
        c.setFillColorRGB(*(RED if v < target else NAVY))
        c.rect(x0 + PL, ry - 4, bw, 8, fill=1, stroke=0)
        # Target tick
        tx = x0 + PL + (target / max_v) * pw
        c.setStrokeColorRGB(*BODY_TXT); c.setLineWidth(2)
        c.line(tx, ry - 9, tx, ry + 9)
        # Label + value/target readout
        c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7.5)
        c.drawRightString(x0 + PL - 6, ry - 2, _safe_text(str(d.get("label", "")))[:20])
        c.setFont("Helvetica-Bold", 6.5)
        vs = f"{v:,.1f}{unit}" if not float(v).is_integer() else f"{v:,.0f}{unit}"
        ts = f"{target:,.1f}{unit}" if not float(target).is_integer() else f"{target:,.0f}{unit}"
        c.drawString(x0 + PL + pw + 6, ry - 2, f"{vs} / {ts} tgt")
    _axis_titles(c, spec, x0, y0, PL, 12, pw, h - 22)


def _histogram(c, spec, x0, y0, w, h):
    """Distribution of a single metric across buckets — visually identical to
    a single-series bar chart except the bars touch (no inter-bar gap), which
    is what reads as 'histogram' rather than 'ranked bar chart' to the eye."""
    data = (spec.get("series") or [{}])[0].get("data") or []
    if not data:
        return
    unit = _safe_text(spec.get("unit", ""))
    n = len(data)
    max_v = max((abs(_coerce_value(d.get("value", 0))) for d in data), default=1) or 1
    PL, PB = 40, 40
    pw, ph = w - PL - 12, h - PB - 20
    sp = pw / n

    for f in (0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, f"{f * max_v:.0f}{unit}")

    c.setStrokeColorRGB(0.78, 0.82, 0.88); c.setLineWidth(0.8)
    c.line(x0 + PL, y0 + PB, x0 + PL + pw, y0 + PB)

    for i, d in enumerate(data):
        v = abs(_coerce_value(d.get("value", 0)))
        bh = max(1.5, (v / max_v) * ph)
        bx = x0 + PL + i * sp
        c.setFillColorRGB(*NAVY); c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(0.6)
        c.rect(bx, y0 + PB, sp - 0.5, bh, fill=1, stroke=1)
        if sp > 18:
            c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica-Bold", 6)
            c.drawCentredString(bx + sp / 2, y0 + PB + bh + 2, f"{v:.0f}")
        lbl = str(d.get("label", ""))
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 5.5 if sp < 30 else 6.5)
        c.saveState()
        c.translate(bx + sp / 2, y0 + PB - 4)
        c.rotate(35 if sp < 35 else 0)
        if sp < 35:
            c.drawString(0, 0, lbl[:10])
        else:
            c.drawCentredString(0, -8, lbl[:14])
        c.restoreState()

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _boxplot(c, spec, x0, y0, w, h):
    """Box-and-whisker plot: one vertical box per entity showing min/q1/
    median/q3/max."""
    data = (spec.get("series") or [{}])[0].get("data") or []
    if not data:
        return
    unit = _safe_text(spec.get("unit", ""))
    n = len(data)
    all_v = [_coerce_value(d.get(k, 0)) for d in data for k in ("min", "max")]
    lo, hi = min(all_v, default=0), max(all_v, default=1)
    span = (hi - lo) or 1
    PL, PB = 46, 40
    pw, ph = w - PL - 12, h - PB - 20
    sp = pw / n
    box_w = min(sp * 0.4, 38)

    def yy(v):
        return y0 + PB + ((v - lo) / span) * ph

    for f in (0, 0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 3, gy - 2.5, f"{lo + f * span:.1f}{unit}")

    for i, d in enumerate(data):
        cx2 = x0 + PL + i * sp + sp / 2
        mn, q1, med, q3, mx = (_coerce_value(d.get(k, 0)) for k in ("min", "q1", "median", "q3", "max"))
        color = CHART_COLORS[i % len(CHART_COLORS)]
        c.setStrokeColorRGB(*color); c.setLineWidth(1.2)
        c.line(cx2, yy(mn), cx2, yy(q1))       # lower whisker
        c.line(cx2, yy(q3), cx2, yy(mx))       # upper whisker
        c.line(cx2 - box_w / 4, yy(mn), cx2 + box_w / 4, yy(mn))  # min cap
        c.line(cx2 - box_w / 4, yy(mx), cx2 + box_w / 4, yy(mx))  # max cap
        c.setFillColorRGB(*color)
        c.rect(cx2 - box_w / 2, yy(q1), box_w, max(1, yy(q3) - yy(q1)), fill=1, stroke=0)
        c.setStrokeColorRGB(*WHITE); c.setLineWidth(1.4)
        c.line(cx2 - box_w / 2, yy(med), cx2 + box_w / 2, yy(med))  # median line
        lbl = str(d.get("label", ""))[:14]
        c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
        c.drawCentredString(cx2, y0 + PB - 11, lbl)

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _treemap(c, spec, x0, y0, w, h):
    """Proportional-area composition chart, laid out with a simple
    alternating horizontal/vertical slice-and-dice algorithm (not a full
    squarified treemap, but legible and cheap to compute for the 3-20 item
    counts these reports actually chart)."""
    data = (spec.get("series") or [{}])[0].get("data") or []
    if not data:
        return
    items = sorted(data, key=lambda d: abs(_coerce_value(d.get("value", 0))), reverse=True)
    total = sum(abs(_coerce_value(d.get("value", 0))) for d in items) or 1
    unit = _safe_text(spec.get("unit", ""))

    def layout(items, bx, by, bw, bh, horizontal):
        if not items:
            return
        sub_total = sum(abs(_coerce_value(d.get("value", 0))) for d in items) or 1
        pos = 0.0
        for i, d in enumerate(items):
            frac = abs(_coerce_value(d.get("value", 0))) / sub_total
            if horizontal:
                seg_w = frac * bw
                cell = (bx + pos, by, seg_w, bh)
                pos += seg_w
            else:
                seg_h = frac * bh
                cell = (bx, by + pos, bw, seg_h)
                pos += seg_h
            cx, cy, cw, ch_ = cell
            color = CHART_COLORS[i % len(CHART_COLORS)]
            c.setFillColorRGB(*color); c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(1)
            c.rect(cx, cy, max(0.5, cw - 1), max(0.5, ch_ - 1), fill=1, stroke=1)
            pct = abs(_coerce_value(d.get("value", 0))) / total * 100
            lbl = str(d.get("label", ""))
            if cw > 34 and ch_ > 16:
                c.setFillColorRGB(*WHITE)
                c.setFont("Helvetica-Bold", 7 if cw > 60 else 6)
                c.drawString(cx + 4, cy + ch_ - 11, lbl[:int(cw / 4.2)])
                c.setFont("Helvetica", 6)
                vs = f"{pct:.1f}%" if unit == "%" or not unit else f"{_coerce_value(d.get('value', 0)):,.0f}{unit}"
                c.drawString(cx + 4, cy + 4, vs)

    groups = {}
    order = []
    for d in items:
        g = d.get("group") or "__flat__"
        if g not in groups:
            groups[g] = []
            order.append(g)
        groups[g].append(d)

    if len(groups) > 1:
        # One row per group, sized by the group's total value; items inside
        # each row laid out left-to-right by their own value.
        group_totals = [(g, sum(abs(_coerce_value(d.get("value", 0))) for d in groups[g])) for g in order]
        pos_y = 0.0
        for g, gtot in group_totals:
            row_h = (gtot / total) * h
            layout(groups[g], x0, y0 + h - pos_y - row_h, w, row_h, horizontal=True)
            pos_y += row_h
    else:
        layout(items, x0, y0, w, h, horizontal=True)


def _heatmap(c, spec, x0, y0, w, h):
    """Rows × columns matrix, one colored cell per value — color intensity
    (not position/height) carries the number, so the eye can scan for the
    best/worst cells at a glance."""
    rows = spec.get("rows") or []
    cols = spec.get("columns") or []
    values = spec.get("values") or []
    if not rows or not cols or not values:
        return
    unit = _safe_text(spec.get("unit", ""))
    x_label = _safe_text(spec.get("xLabel") or "")
    y_label = _safe_text(spec.get("yLabel") or "")
    # None means unavailable, never zero. Missing cells are drawn neutral and
    # excluded from the colour scale so a data gap cannot masquerade as a return.
    flat = [_coerce_value(v) for r in values for v in r if v is not None]
    if not flat:
        return
    lo, hi = min(flat, default=0), max(flat, default=1)
    span = (hi - lo) or 1

    PL, PB = 104, 38
    pw, ph = w - PL - 8, h - PB - 28
    cell_w = pw / len(cols)
    cell_h = ph / len(rows)

    def color_for(v):
        t = (v - lo) / span
        # Diverging-ish scale: red (low) -> cream -> teal/green (high)
        if t < 0.5:
            return _mix(RED, LIGHT, t / 0.5)
        return _mix(LIGHT, GREEN, (t - 0.5) / 0.5)

    for ri, row in enumerate(rows):
        ry = y0 + PB + ph - (ri + 1) * cell_h
        c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
        c.drawRightString(x0 + PL - 5, ry + cell_h / 2 - 2, str(row)[:16])
        for ci in range(len(cols)):
            raw = values[ri][ci] if ri < len(values) and ci < len(values[ri]) else None
            cx2 = x0 + PL + ci * cell_w
            c.setStrokeColorRGB(1, 1, 1); c.setLineWidth(1)
            if raw is None:
                c.setFillColorRGB(0.91, 0.92, 0.94)
                c.rect(cx2, ry, cell_w - 1, cell_h - 1, fill=1, stroke=1)
                if cell_w > 24 and cell_h > 10:
                    c.setFillColorRGB(*GREY); c.setFont("Helvetica", 6)
                    c.drawCentredString(cx2 + cell_w / 2, ry + cell_h / 2 - 2, "N/A")
                continue
            v = _coerce_value(raw)
            c.setFillColorRGB(*color_for(v))
            c.rect(cx2, ry, cell_w - 1, cell_h - 1, fill=1, stroke=1)
            if cell_w > 22 and cell_h > 10:
                c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica-Bold", 6.5)
                vs = f"{v:.0f}{unit}" if abs(v) >= 10 else f"{v:.1f}{unit}"
                c.drawCentredString(cx2 + cell_w / 2, ry + cell_h / 2 - 2, vs)

    for ci, col in enumerate(cols):
        cx2 = x0 + PL + ci * cell_w + cell_w / 2
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        c.drawCentredString(cx2, y0 + PB + ph + 4, str(col)[:12])
    if x_label:
        c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(x0 + PL + pw / 2, y0 + 4, x_label[:50])
    if y_label:
        c.saveState()
        c.translate(x0 + 12, y0 + PB + ph / 2)
        c.rotate(90)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(0, 0, y_label[:35])
        c.restoreState()


def _bubble(c, spec, x0, y0, w, h):
    """Three metrics per entity: x, y, and marker size — series order is
    fixed (x, y, size) per the chart-spec contract."""
    series_list = spec.get("series") or []
    if len(series_list) < 3:
        return
    x_by = {str(p.get("label", "")): _coerce_value(p.get("value", 0)) for p in (series_list[0].get("data") or [])}
    y_by = {str(p.get("label", "")): _coerce_value(p.get("value", 0)) for p in (series_list[1].get("data") or [])}
    s_by = {str(p.get("label", "")): abs(_coerce_value(p.get("value", 0))) for p in (series_list[2].get("data") or [])}
    labels = [l for l in x_by if l in y_by and l in s_by]
    if not labels:
        return

    xs, ys, ss = [x_by[l] for l in labels], [y_by[l] for l in labels], [s_by[l] for l in labels]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    x_pad = (x_hi - x_lo) * 0.12 or 1
    y_pad = (y_hi - y_lo) * 0.12 or 1
    x_lo, x_hi = x_lo - x_pad, x_hi + x_pad
    y_lo, y_hi = y_lo - y_pad, y_hi + y_pad
    s_max = max(ss) or 1

    PL, PB = 46, 34
    pw, ph = w - PL - 16, h - PB - 20

    for f in (0, 0.25, 0.5, 0.75, 1.0):
        gy = y0 + PB + f * ph
        gx = x0 + PL + f * pw
        c.setStrokeColorRGB(0.88, 0.9, 0.94); c.setLineWidth(0.4)
        c.line(x0 + PL, gy, x0 + PL + pw, gy)
        c.line(gx, y0 + PB, gx, y0 + PB + ph)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 6)
        c.drawRightString(x0 + PL - 3, gy - 2, f"{y_lo + f * (y_hi - y_lo):.1f}")
        c.drawCentredString(gx, y0 + PB - 9, f"{x_lo + f * (x_hi - x_lo):.1f}")

    for i, lbl in enumerate(labels):
        px = x0 + PL + ((xs[i] - x_lo) / (x_hi - x_lo)) * pw
        py = y0 + PB + ((ys[i] - y_lo) / (y_hi - y_lo)) * ph
        r = 3 + (ss[i] / s_max) * 16
        color = CHART_COLORS[i % len(CHART_COLORS)]
        try:
            c.setFillAlpha(0.65)
        except Exception:
            pass
        c.setFillColorRGB(*color)
        c.circle(px, py, r, fill=1, stroke=0)
        try:
            c.setFillAlpha(1.0)
        except Exception:
            pass
        if r > 10:
            c.setFillColorRGB(*WHITE); c.setFont("Helvetica-Bold", 5.5)
            c.drawCentredString(px, py - 2, lbl[:10])

    _axis_titles(c, spec, x0, y0, PL, PB, pw, ph)


def _radar(c, spec, x0, y0, w, h):
    """Spider/radar chart: one axis per metric, one polygon per entity/series."""
    series_list = spec.get("series") or []
    if not series_list:
        return
    metrics = [str(p.get("label", "")) for p in (series_list[0].get("data") or [])]
    n_axes = len(metrics)
    if n_axes < 3:
        return

    cx2 = x0 + w / 2
    cy2 = y0 + h / 2 - 6
    R = min(w, h) * 0.32
    # Independent per-axis scale (each metric normalised 0-1 across all
    # series) so metrics with very different units/ranges (P/E vs. D/E vs.
    # ROE%) all still fill the chart rather than one axis dwarfing the rest.
    axis_max = []
    for mi in range(n_axes):
        vals = [_coerce_value((s.get("data") or [{}])[mi].get("value", 0)) if mi < len(s.get("data") or []) else 0
                for s in series_list]
        axis_max.append(max(vals) or 1)

    def point(mi, frac):
        angle = math.pi / 2 + mi * (2 * math.pi / n_axes)
        r = frac * R
        return cx2 + r * math.cos(angle), cy2 + r * math.sin(angle)

    # Axis lines + metric labels + a light ring at 50%/100%
    for ring in (0.5, 1.0):
        c.setStrokeColorRGB(0.85, 0.87, 0.93); c.setLineWidth(0.5)
        p0 = point(0, ring)
        path = c.beginPath(); path.moveTo(*p0)
        for mi in range(1, n_axes):
            path.lineTo(*point(mi, ring))
        path.close(); c.drawPath(path, fill=0, stroke=1)
    for mi in range(n_axes):
        ax, ay = point(mi, 1.0)
        c.setStrokeColorRGB(0.85, 0.87, 0.93); c.setLineWidth(0.5)
        c.line(cx2, cy2, ax, ay)
        lx, ly = point(mi, 1.14)
        c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
        c.drawCentredString(lx, ly - 2, metrics[mi][:16])

    for si, s in enumerate(series_list):
        pts = s.get("data") or []
        color = CHART_COLORS[si % len(CHART_COLORS)]
        c.setStrokeColorRGB(*color); c.setFillColorRGB(*color); c.setLineWidth(1.4)
        path = c.beginPath()
        for mi in range(n_axes):
            v = _coerce_value(pts[mi].get("value", 0)) if mi < len(pts) else 0
            frac = v / axis_max[mi] if axis_max[mi] else 0
            xx, yy = point(mi, max(0.02, frac))
            (path.moveTo if mi == 0 else path.lineTo)(xx, yy)
        path.close()
        c.drawPath(path, fill=0, stroke=1)

    # Legend
    if len(series_list) > 1:
        lx = x0 + 4
        ly = y0 + h - 6
        for si, s in enumerate(series_list):
            color = CHART_COLORS[si % len(CHART_COLORS)]
            c.setFillColorRGB(*color); c.rect(lx, ly - 4, 10, 6, fill=1, stroke=0)
            name = str(s.get("name", f"Series {si+1}"))[:20]
            c.setFillColorRGB(*BODY_TXT); c.setFont("Helvetica", 7)
            c.drawString(lx + 13, ly - 2, name)
            lx += 14 + c.stringWidth(name, "Helvetica", 6.5) + 10


def _datawrapper_image(c, spec, x0, y0, w, h):
    """Draw a fetched Datawrapper PNG export, scaled to fit the card, centred.

    Falls back to the native renderer if pngBytes is missing, corrupt, or not
    a valid PNG (e.g. a JSON status object returned while Datawrapper was still
    rendering the export).

    Datawrapper has no literal axis-title field for bar/column/line charts
    (confirmed platform limitation — only scatter plots get one). Previously
    xLabel/yLabel were folded into the chart's "intro" subtitle sentence
    baked into the PNG (e.g. "Amount (INR) by Financial Metric"), which reads
    as a description line under the title, not a real axis label sitting at
    the axis. Fixed here by carving out a thin margin around the PNG —
    bottom for the x-axis title, left (rotated) for the y-axis title — and
    drawing real ReportLab text there with the exact same styling/position
    convention _axis_titles() already uses for natively-rendered charts, so
    a Datawrapper chart and a native chart both end up with genuine,
    consistently-placed axis labels instead of just one of the two.
    """
    from reportlab.lib.utils import ImageReader
    import base64 as _b64
    png_bytes = (spec.get("datawrapper") or {}).get("pngBytes")
    if not png_bytes:
        return False
    # Decode base64 string — pngBytes may be a b64 string after JSON serialization
    if isinstance(png_bytes, str):
        try:
            png_bytes = _b64.b64decode(png_bytes)
        except Exception:
            log.warning("Datawrapper pngBytes for chart %r is not valid base64 — using native renderer", spec.get("title", "?"))
            return False
    # Reject non-PNG payloads (JSON status objects, HTML error pages, etc.)
    if not isinstance(png_bytes, (bytes, bytearray)) or png_bytes[:4] != b"\x89PNG":
        log.warning(
            "Datawrapper pngBytes for chart %r is not a valid PNG (%d bytes, starts %r) — using native renderer",
            spec.get("title", "?"), len(png_bytes), png_bytes[:16],
        )
        return False
    try:
        # Axis titles only make sense for XY chart types — pies/donuts/tables
        # have no x/y axes to label, so skip reserving margin for them.
        dw_type = str(spec.get("type", "bar")).lower()
        # Scatter plots are the one chart type Datawrapper gives *native*
        # per-axis titles to (baked into the exported PNG from the CSV's own
        # column headers — see publish_chart/_spec_to_csv). Drawing our own
        # xLabel/yLabel overlay on top of that, as we do for bar/line/arrow
        # charts (which have no native axis titles at all), just stacks a
        # second, differently-worded label over the real one — e.g. the
        # native "Rating" title plus an overlaid "Cost (Rs.)" both fighting
        # for the same y-axis. Skip the overlay for scatter; the PNG already
        # has the real thing.
        wants_axis_titles = dw_type not in ("pie", "donut", "table", "scatter")
        x_label = _safe_text((spec.get("xLabel") or "").strip()) if wants_axis_titles else ""
        y_label = _safe_text((spec.get("yLabel") or "").strip()) if wants_axis_titles else ""
        if not y_label and wants_axis_titles:
            y_label = _safe_text(spec.get("unit", ""))
        title_lower = _safe_text((spec.get("title") or "").strip()).lower()
        if x_label and title_lower and (x_label.lower() in title_lower or title_lower in x_label.lower()):
            x_label = ""

        PB = 13 if x_label else 0   # bottom strip for the x-axis title
        PL = 13 if y_label else 0   # left strip for the (rotated) y-axis title

        img = ImageReader(io.BytesIO(png_bytes))
        iw, ih = img.getSize()
        avail_w, avail_h = max(w - PL, 1), max(h - PB, 1)
        scale = min(avail_w / iw, avail_h / ih)
        dw, dh = iw * scale, ih * scale
        dx = x0 + PL + (avail_w - dw) / 2
        dy = y0 + PB + (avail_h - dh) / 2
        c.drawImage(img, dx, dy, width=dw, height=dh,
                    preserveAspectRatio=True, mask="auto")

        if x_label:
            c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
            c.drawCentredString(x0 + PL + avail_w / 2, y0 + 2, x_label[:40])
        if y_label:
            c.saveState()
            c.translate(x0 + 10, y0 + PB + avail_h / 2)
            c.rotate(90)
            c.setFillColorRGB(*GREY); c.setFont("Helvetica-Oblique", 8)
            c.drawCentredString(0, 0, y_label[:30])
            c.restoreState()
        return True
    except Exception as exc:
        log.warning("Failed to draw Datawrapper PNG for chart %r: %s", spec.get("title", "?"), exc)
        return False


def _wrap_to_width(c, text: str, font: str, size: float, max_w: float, max_lines: int = 3) -> list[str]:
    """Word/char-wrap `text` to fit `max_w` points, measuring actual glyph
    width (c.stringWidth) instead of a crude character-count guess. Falls
    back to hard character breaks for single "words" wider than max_w on
    their own (e.g. a long URL with no spaces) so it still fits rather than
    silently overflowing. Truncates with an ellipsis if it still doesn't
    fit in max_lines."""
    if not text:
        return [""]
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    for word in words:
        candidate = f"{cur} {word}".strip()
        if c.stringWidth(candidate, font, size) <= max_w or not cur:
            # Word itself may still be wider than max_w (e.g. a bare URL) —
            # hard-break it character by character in that case.
            if c.stringWidth(candidate, font, size) <= max_w:
                cur = candidate
                continue
            # cur is empty and the single word overflows — break it up.
            piece = ""
            for ch in word:
                if c.stringWidth(piece + ch, font, size) <= max_w:
                    piece += ch
                else:
                    lines.append(piece)
                    piece = ch
            cur = piece
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and c.stringWidth(last + "…", font, size) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines or [""]


def _table(c, spec, x0, y0, w, h):
    """Fallback grid renderer used only if the Datawrapper PNG isn't available.

    Column widths used to be split perfectly evenly (w / n_cols) and every
    cell was hard-truncated to `col_w / 4` characters regardless of what
    that column actually held. For a 3-4 column table that gives each
    column ~25-30 characters — fine for a short "Data Type" cell, but it
    silently chopped every URL down to a meaningless "https://uk.finance.
    yahoo.com/q…" with no way to tell what page it actually pointed to.
    Fixed by (1) sizing columns by their real content instead of splitting
    evenly, and (2) wrapping long cells (URLs in particular) across up to
    3 lines using actual measured text width instead of truncating them.
    """
    columns = spec.get("columns") or []
    rows = spec.get("rows") or []
    if not columns or not rows:
        return
    n_cols = len(columns)
    header_h = 18
    PAD = 4

    HEADER_FONT, HEADER_SIZE = "Helvetica-Bold", 7
    BODY_FONT, BODY_SIZE = "Helvetica", 6.5

    # ── Column widths: proportional to the longest content each column
    # actually holds (header or any cell), not a blind even split. A
    # generous cap keeps any single very-long column (URLs) from starving
    # the rest down to nothing.
    def _content_weight(ci: int) -> float:
        longest = len(str(columns[ci]))
        for row in rows:
            if ci < len(row):
                longest = max(longest, len(str(row[ci])))
        return min(longest, 60)  # cap so one huge cell doesn't dominate

    weights = [max(_content_weight(ci), 6) for ci in range(n_cols)]
    total_w_units = sum(weights) or n_cols
    min_col_w = 46.0
    col_ws = [max(min_col_w, w * (wt / total_w_units)) for wt in weights]
    # If mins pushed the total over the card width, rescale everything down
    # proportionally so columns still sum to exactly `w`.
    scale = w / sum(col_ws)
    col_ws = [cw * scale for cw in col_ws]
    col_x = [x0]
    for cw in col_ws[:-1]:
        col_x.append(col_x[-1] + cw)

    # ── Pre-wrap every cell (and header) now, using each column's actual
    # width, so we know how tall each row needs to be before drawing.
    def _wrap_cell(text: str, ci: int, font: str, size: float) -> list[str]:
        return _wrap_to_width(c, str(text), font, size, col_ws[ci] - 2 * PAD, max_lines=3)

    wrapped_rows: list[list[list[str]]] = []
    row_heights: list[float] = []
    LINE_H = 8
    for row in rows:
        cells_wrapped = [_wrap_cell(row[ci] if ci < len(row) else "", ci, BODY_FONT, BODY_SIZE)
                          for ci in range(n_cols)]
        n_lines = max(len(cw) for cw in cells_wrapped)
        wrapped_rows.append(cells_wrapped)
        row_heights.append(max(14, n_lines * LINE_H + 6))

    # If showing every row wouldn't fit, reserve space for a "+N more rows"
    # note up front — computed against real (variable) row heights this
    # time, rather than assuming every row is the same fixed height.
    avail_h = h - header_h
    NOTE_H = 12
    # Reserve room for the "+N more rows" note up front whenever not every
    # row is going to fit — otherwise that note gets computed AFTER filling
    # every available row and ends up drawn below the card's bottom edge.
    fits_all = sum(row_heights) <= avail_h
    budget = avail_h if fits_all else max(0.0, avail_h - NOTE_H)
    shown_rows: list[list[list[str]]] = []
    shown_heights: list[float] = []
    running = 0.0
    for cw, rh in zip(wrapped_rows, row_heights):
        if running + rh > budget and shown_rows:
            break
        running += rh
        shown_rows.append(cw)
        shown_heights.append(rh)
    needs_note = len(shown_rows) < len(wrapped_rows)

    # Header
    c.setFillColorRGB(*NAVY)
    c.rect(x0, y0 + h - header_h, w, header_h, fill=1, stroke=0)
    c.setFillColorRGB(*WHITE)
    c.setFont(HEADER_FONT, HEADER_SIZE)
    for ci, col in enumerate(columns):
        cx = col_x[ci] + PAD
        c.drawString(cx, y0 + h - header_h + 6, str(col)[:40])

    # Rows
    ry_top = y0 + h - header_h
    for ri, (cells, rh) in enumerate(zip(shown_rows, shown_heights)):
        ry = ry_top - rh
        if ri % 2 == 1:
            c.setFillColorRGB(*LIGHT)
            c.rect(x0, ry, w, rh, fill=1, stroke=0)
        c.setFillColorRGB(*BODY_TXT)
        c.setFont(BODY_FONT, BODY_SIZE)
        for ci, lines in enumerate(cells):
            cx = col_x[ci] + PAD
            # Cells that look like a URL are also drawn as real clickable
            # links (in addition to being fully visible, wrapped text) so
            # the source is one click away instead of a dead-end ellipsis.
            cell_text_full = " ".join(lines)
            is_url = cell_text_full.strip().lower().startswith(("http://", "https://"))
            ty = ry + rh - LINE_H
            for line in lines:
                c.drawString(cx, ty, line)
                ty -= LINE_H
            if is_url:
                c.linkURL(cell_text_full.strip(), (cx, ry, col_x[ci] + col_ws[ci] - PAD, ry + rh),
                          relative=0, thickness=0)
        ry_top = ry

    if needs_note:
        n_more = len(wrapped_rows) - len(shown_rows)
        c.setFillColorRGB(*GREY)
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(x0, ry_top - 10, f"+ {n_more} more row{'s' if n_more != 1 else ''} — see full table on Datawrapper")


_NO_DATAWRAPPER_TYPES = ("waterfall", "candlestick", "sparkline", "boxplot", "treemap", "heatmap", "bubble", "radar")


def _draw_chart(c, spec, x0, y0, w, h):
    spec = dict(spec or {})
    t = normalize_chart_type(spec.get("type") or spec.get("chart_type") or spec.get("chartType") or "bar")
    spec["type"] = t
    # Types in _NO_DATAWRAPPER_TYPES have no Datawrapper equivalent (see the
    # early return in utils/datawrapper.py's publish_chart) — skip the
    # Datawrapper-image lookup entirely for them rather than wasting a
    # lookup that will never find anything under chart["datawrapper"].
    if t not in _NO_DATAWRAPPER_TYPES and _datawrapper_image(c, spec, x0, y0, w, h):
        return
    if t == "table":
        _table(c, spec, x0, y0, w, h)
    elif t in {"pie", "doughnut", "polar_area"}:
        _pie(c, spec, x0, y0, w, h)
    elif t == "line":
        _line(c, spec, x0, y0, w, h)
    elif t == "waterfall":
        _waterfall(c, spec, x0, y0, w, h)
    elif t in {"candlestick", "ohlc"}:
        _candlestick(c, spec, x0, y0, w, h)
    elif t == "sparkline":
        _sparkline(c, spec, x0, y0, w, h)
    elif t == "bullet":
        _bullet(c, spec, x0, y0, w, h)
    elif t == "histogram":
        _histogram(c, spec, x0, y0, w, h)
    elif t == "boxplot":
        _boxplot(c, spec, x0, y0, w, h)
    elif t == "treemap":
        _treemap(c, spec, x0, y0, w, h)
    elif t == "heatmap":
        _heatmap(c, spec, x0, y0, w, h)
    elif t == "bubble":
        _bubble(c, spec, x0, y0, w, h)
    elif t == "radar":
        _radar(c, spec, x0, y0, w, h)
    elif t in {"bar", "stacked_bar", "grouped_bar", "horizontal_bar", "area", "stacked_area", "step_line", "spline", "combo", "histogram", "dot_plot", "lollipop", "pareto", "progress", "kpi", "gauge", "funnel", "funnel_pyramid", "marimekko", "population_pyramid"}:
        _bar(c, spec, x0, y0, w, h)
    else:
        # Older ReportLab-only fallback does not implement every advanced SVG
        # shape. Do not silently disguise a Sankey/Gantt/heatmap/etc. as bars.
        c.saveState()
        c.setFillColorRGB(0.95, 0.97, 0.99); c.setStrokeColorRGB(0.82, 0.86, 0.91)
        c.roundRect(x0, y0 + h * 0.25, w, h * 0.5, 8, fill=1, stroke=1)
        c.setFillColorRGB(0.12, 0.18, 0.31); c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x0 + w / 2, y0 + h * 0.52, str(spec.get("title") or t.replace("_", " ").title())[:90])
        c.setFillColorRGB(0.35, 0.40, 0.50); c.setFont("Helvetica", 8)
        c.drawCentredString(x0 + w / 2, y0 + h * 0.41, f"{t.replace('_', ' ').title()} visual requires the full report renderer")
        c.restoreState()


# ─── PDF builder ──────────────────────────────────────────────────────────────
def _legacy_build_pdf(report: str, title: str, question: str, summary: str,
              key_stats: list, charts: list, logo_b64: str = "",
              file_images: list | None = None, web_images: list | None = None,
              theme: dict | None = None, sources: object = None) -> bytes:
    apply_theme(theme)
    import json as _json

    # Last-resort: if report arrived as raw JSON, extract the markdown field
    _s = (report or "").strip()
    if _s.startswith("{") and '"report"' in _s:
        try:
            _inner = _json.loads(_s, strict=False)
            if isinstance(_inner, dict) and "report" in _inner:
                report = (_inner["report"] or "").replace("\\n", "\n").strip()
                title   = title   or _inner.get("title", "")
                summary = summary or _inner.get("summary", "")
                key_stats = key_stats or _inner.get("keyStats", [])
                charts    = charts    or _inner.get("charts", [])
        except Exception as e:
            log.debug("PDF: report field is not double-encoded JSON, using as-is (%s)", e)

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.utils import ImageReader
    from PIL import Image as PILImage

    PAGE_W, PAGE_H = A4          # 595 x 842 pt
    MARGIN = 52
    CW = PAGE_W - 2 * MARGIN     # content width
    HEADER_H = 34
    FOOTER_H = 22
    BODY_TOP = PAGE_H - HEADER_H - 24   # 24pt gap below header band (was 16 — still touching)
    BODY_BOT = FOOTER_H + 24            # guard above footer

    now_utc = datetime.now(timezone.utc)
    now_str  = now_utc.strftime("%d %b %Y")
    date_str = now_utc.strftime("%d %B %Y")
    domain   = detect_domain(question)
    logo_bytes = _load_logo_bytes(logo_b64)

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)
    page_num = [0]

    # ── Per-page header / footer ───────────────────────────────────────────────
    def hf(section_title: str = ""):
        page_num[0] += 1
        # Body background — paint white over the full body area first so no
        # stray graphics state from a previous page can bleed through.
        c.setFillColorRGB(1, 1, 1)
        c.rect(0, FOOTER_H, PAGE_W, PAGE_H - HEADER_H - FOOTER_H, fill=1, stroke=0)
        # Header
        c.setFillColorRGB(*NAVY)
        c.rect(0, PAGE_H - HEADER_H, PAGE_W, HEADER_H, fill=1, stroke=0)
        if logo_bytes:
            if not _draw_logo(c, logo_bytes, MARGIN, PAGE_H - HEADER_H + 5, 72, 22):
                _logo_text()
        else:
            _logo_text()
        if section_title:
            c.setFillColorRGB(*WHITE); c.setFont("Helvetica-Bold", 8)
            # Pixel-width-aware ellipsis instead of a blind [:70] char slice,
            # which could cut a long section title off mid-word right against
            # the page-number/date area with no visual indication of the cut.
            title_max_w = PAGE_W - MARGIN - 150
            c.drawRightString(PAGE_W - MARGIN, PAGE_H - HEADER_H + 18,
                               _fit_cell(c, section_title, "Helvetica-Bold", 8, title_max_w))
        c.setFillColorRGB(0.65, 0.68, 0.82); c.setFont("Helvetica", 7)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - HEADER_H + 7, now_str)
        # Footer — branded: report title + firm name on the left, page number on the right.
        c.setFillColorRGB(0.94, 0.95, 1.0)
        c.rect(0, 0, PAGE_W, FOOTER_H, fill=1, stroke=0)
        c.setStrokeColorRGB(*NAVY); c.setLineWidth(1.2)
        c.line(0, FOOTER_H, PAGE_W, FOOTER_H)
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 7)
        # Same fix here: was f"{title[:60]} · Growth Gradual", a fixed
        # character count that cuts a long title mid-word ("...Ten Million
        # Dollar Busine · Growth Gradual") regardless of the actual pixel
        # width available on the page.
        suffix = " · Growth Gradual"
        footer_max_w = PAGE_W - 2 * MARGIN - 70 - c.stringWidth(suffix, "Helvetica", 7)
        footer_title = _fit_cell(c, _safe_text(title) or "Market Intelligence Report", "Helvetica", 7, footer_max_w)
        footer_label = f"{footer_title}{suffix}"
        c.drawString(MARGIN, 7, footer_label)
        c.drawRightString(PAGE_W - MARGIN, 7, f"Page {page_num[0]}")

    def _logo_text():
        c.setFillColorRGB(*WHITE); c.setFont("Helvetica-Bold", 10)
        c.drawString(MARGIN, PAGE_H - HEADER_H + 10, "Growth Gradual")

    # ── cursor helpers ─────────────────────────────────────────────────────────
    y = [BODY_TOP]

    def need(space: float, section: str = ""):
        if y[0] - space < BODY_BOT:
            c.showPage(); hf(section); y[0] = BODY_TOP

    def nl(n: float = 6):
        y[0] -= n

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — TITLE PAGE (navy textured cover, "Market Currents" style)
    # ═══════════════════════════════════════════════════════════════════════════
    # Solid navy fallback fill first (belt-and-braces if texture gen fails)
    c.setFillColorRGB(*NAVY)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

    # Real woven canvas/linen texture, full bleed — see _generate_cover_texture().
    try:
        _tex_png = _generate_cover_texture(int(PAGE_W), int(PAGE_H), NAVY)
        c.drawImage(ImageReader(io.BytesIO(_tex_png)), 0, 0, width=PAGE_W, height=PAGE_H, mask=None)
    except Exception as _tex_exc:
        log.warning("PDF: cover texture generation failed, falling back to flat navy (%s)", _tex_exc)

    # Gold accent bar at very top (thin, matches brand rule elsewhere)
    c.setFillColorRGB(*GOLD)
    c.rect(0, PAGE_H - 6, PAGE_W, 6, fill=1, stroke=0)

    # Logo in a white card, top-left (source cover contains the logo inside
    # a plain white box since the logo art itself expects a light background)
    logo_card_w, logo_card_h = 150, 76
    logo_card_x, logo_card_y = MARGIN, PAGE_H - 40 - logo_card_h
    c.setFillColorRGB(*WHITE)
    c.rect(logo_card_x, logo_card_y, logo_card_w, logo_card_h, fill=1, stroke=0)
    if logo_bytes:
        if not _draw_logo(c, logo_bytes, logo_card_x + 14, logo_card_y + 15, logo_card_w - 28, logo_card_h - 30):
            c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 14)
            c.drawString(logo_card_x + 14, logo_card_y + logo_card_h / 2 - 5, "Growth Gradual")
    else:
        c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 14)
        c.drawString(logo_card_x + 14, logo_card_y + logo_card_h / 2 - 5, "Growth Gradual")

    # Overline — small-caps gold label, letter-spaced
    overline_y = logo_card_y - 46
    _domain_up = domain.upper()
    overline = _domain_up if _domain_up.endswith("INTELLIGENCE") else f"{_domain_up} INTELLIGENCE"
    c.setFillColorRGB(*GOLD); c.setFont("Helvetica-Bold", 9.5)
    _ox = MARGIN
    for _ch in overline:
        c.drawString(_ox, overline_y, _ch)
        _ox += c.stringWidth(_ch, "Helvetica-Bold", 9.5) + 1.6  # manual letter-spacing

    # ── Title — serif, gold, wraps to fit; date line in white beneath it ──────
    raw_title = title or question
    raw_title = re.sub(r"^\*+\s*", "", raw_title.strip())
    m = re.match(r"^(.{0,80}?)(?:\*\*|:|\.\s|,\s*here are|,\s*based on)", raw_title, re.IGNORECASE)
    if m and len(m.group(1).strip()) >= 8:
        raw_title = m.group(1).strip()
    _PREAMBLE_PATTERNS = (
        r"^here(?:'s| is| are)\b",
        r"^based on\b",
        r"^(?:latest|some of the latest)\b.{0,40}\b(news|trends|updates|data)\b",
        r"^the following\b",
        r"^below (?:is|are)\b",
        r"^so,?\s",
        r"^you(?:'re| are)\b",
        r"^i(?:'ve| have| will| can)\b",
        r"^let(?:'s| us)\b",
        r"^looking for\b",
        r"^exploring\b",
        r"^understanding\b",
        r"^a (?:look|guide|deep dive|breakdown|comprehensive|detailed)\b",
        r"^an (?:analysis|overview|exploration|examination|in-depth)\b",
    )
    _SENTENCE_PATTERNS = (
        r":\s*$",
        r"\bas\s+follows\s*$",
        r"\b(?:are|is|were|have\s+been|has\s+been)\s+(?:as\s+follows|below|here|outlined|shown|listed|summarized|summarised)\b",
    )
    if (any(re.match(p, raw_title.strip(), re.IGNORECASE) for p in _PREAMBLE_PATTERNS)
            or any(re.search(p, raw_title.strip(), re.IGNORECASE) for p in _SENTENCE_PATTERNS)):
        q_clean = re.sub(r"(?i)^(tell me about|what are|what is|show me|give me|find me|list|compare|analyse|analyze|explain)\s+", "", question.strip())
        q_clean = q_clean.rstrip("?!").strip()
        raw_title = q_clean[:80] if len(q_clean) >= 8 else f"{domain} Briefing"
    report_title = _strip_inline(raw_title)

    title_size = 27
    while title_size > 18 and c.stringWidth(report_title, "Times-Bold", title_size) > CW:
        title_size -= 1

    def _wrap_title(text: str, font: str, size: float, max_w: float):
        if c.stringWidth(text, font, size) <= max_w:
            return [text]
        words = text.split()
        line1, line2 = [], []
        for w in words:
            test = " ".join(line1 + [w])
            if c.stringWidth(test, font, size) <= max_w:
                line1.append(w)
            else:
                line2.append(w)
        l2_text = " ".join(line2)
        while size > 14 and c.stringWidth(l2_text, font, size) > max_w:
            size -= 1
        return [" ".join(line1), l2_text] if line2 else [" ".join(line1)]

    title_lines = _wrap_title(report_title, "Times-Bold", title_size, CW)
    ty = overline_y - 34
    c.setFillColorRGB(*GOLD); c.setFont("Times-Bold", title_size)
    for tl in title_lines:
        c.drawString(MARGIN, ty, tl)
        ty -= title_size + 6

    # Date subtitle line, in white, directly under the title (mirrors the
    # reference cover's "June 2026" line under "India : Market Currents")
    ty -= 4
    c.setFillColorRGB(*WHITE); c.setFont("Helvetica", 15)
    c.drawString(MARGIN, ty, date_str)
    ty -= 30

    # ── Description paragraph — light grey/white body text ────────────────────
    if summary:
        summary = _safe_text(summary)
        c.setFillColorRGB(0.82, 0.85, 0.95); c.setFont("Helvetica", 10.5)
        s_lines = _wrap(c, summary, "Helvetica", 10.5, CW)
        if len(s_lines) > 5:
            # Don't hard-cut mid-sentence — back up to the last sentence
            # boundary that fits within the 5-line budget, so the cover
            # paragraph always ends cleanly (with punctuation) rather than
            # stopping on a dangling clause.
            kept_text = " ".join(s_lines[:5])
            last_punct = max(kept_text.rfind(". "), kept_text.rfind("! "), kept_text.rfind("? "))
            if last_punct > len(kept_text) * 0.4:  # only trim back if it doesn't lose too much
                kept_text = kept_text[: last_punct + 1]
            else:
                kept_text = kept_text.rstrip(",;: ") + "…"
            s_lines = _wrap(c, kept_text, "Helvetica", 10.5, CW)
        for ln in s_lines[:5]:
            c.drawString(MARGIN, ty, ln)
            ty -= 15
        ty -= 8

    # Compact "about this report" line — small, unobtrusive, single row
    c.setFillColorRGB(0.55, 0.60, 0.80); c.setFont("Helvetica", 7.5)
    about_line = f"RESEARCH INTELLIGENCE   ·   GENERATED {date_str.upper()}   ·   GROWTH GRADUAL"
    c.drawString(MARGIN, ty, about_line)
    ty -= 22

    # ── Table of contents — compact, restyled for the navy cover ──────────────
    section_headings = []
    for _tok in _tokenise(report):
        if _tok["type"] == "h2":
            heading_text = _strip_inline(_tok["text"])
            # Report bodies often self-number headings ("1. Historical
            # Context...") while the TOC below assigns its own sequential
            # badge number starting at 1 for "Executive Summary". Keeping
            # both produces mismatched double-numbering (badge "2" next to
            # heading text starting with "1."). Strip any leading "N." /
            # "N)" prefix so the badge is the single source of numbering.
            heading_text = re.sub(r"^\d+[\.\)]\s*", "", heading_text)
            section_headings.append(heading_text)
        if len(section_headings) >= 9:
            break

    STAT_ROW_H = 74   # reserved height for the bottom stat-card row
    available = ty - STAT_ROW_H - 24
    if available >= 60 and section_headings:
        c.setFillColorRGB(*GOLD); c.setFont("Helvetica-Bold", 7.5)
        c.drawString(MARGIN, ty, "CONTENTS")
        lw = c.stringWidth("CONTENTS", "Helvetica-Bold", 7.5)
        c.setStrokeColorRGB(*GOLD); c.setLineWidth(0.8)
        c.line(MARGIN, ty - 4, MARGIN + lw, ty - 4)

        toc_y = ty - 18
        col_w2 = (CW - 12) / 2
        col_idx = 0
        for i, heading in enumerate(section_headings):
            if toc_y < ty - available:
                break
            tx = MARGIN + col_idx * (col_w2 + 12)
            c.setFillColorRGB(*GOLD)
            c.roundRect(tx, toc_y - 2, 14, 12, 2, fill=1, stroke=0)
            c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(tx + 7, toc_y + 1, str(i + 1))
            c.setFillColorRGB(0.85, 0.87, 0.97); c.setFont("Helvetica", 8)
            max_tw = col_w2 - 18 - 4
            c.drawString(tx + 18, toc_y, _fit_cell(c, heading, "Helvetica", 8, max_tw))
            col_idx += 1
            if col_idx >= 2:
                col_idx = 0
                toc_y -= 16
        ty = toc_y - 10

    # ── Bottom stat-card row — colour-coded KPI cards (mirrors the reference
    #    cover's NIFTY/SENSEX/sector stat blocks at the foot of the page) ──────
    if key_stats:
        n_cards = min(len(key_stats), 4)
        card_gap = 18
        card_w = (CW - card_gap * (n_cards - 1)) / n_cards
        card_y = 34
        for i in range(n_cards):
            st = key_stats[i]
            val_full = _safe_text(fmt_inr(str(st.get("value", ""))))
            lbl = _safe_text(st.get("label", ""))
            chg = _safe_text(st.get("change", ""))
            col_c = GREEN if chg.startswith("+") or val_full.startswith("+") else (RED if chg.startswith("-") or val_full.startswith("-") else WHITE)
            cx = MARGIN + i * (card_w + card_gap)
            c.setFillColorRGB(*col_c); c.setFont("Helvetica-Bold", 17)
            # Fit to the card's actual pixel width (like the label below and
            # the TOC/table cells elsewhere in this file) instead of a fixed
            # 14-char cutoff — a hard char count truncated e.g. "Rs.49.07
            # Lac Cr" (15 chars) into "Rs.49.07 Lac C", silently dropping the
            # unit letter and reading as a rendering glitch/typo.
            val = _fit_cell(c, val_full, "Helvetica-Bold", 17, card_w)
            c.drawString(cx, card_y + 26, val)
            c.setFillColorRGB(0.7, 0.74, 0.9); c.setFont("Helvetica-Bold", 7.5)
            lbl_lines = _wrap(c, lbl.upper(), "Helvetica-Bold", 7.5, card_w)[:2]
            if len(lbl_lines) == 2 and c.stringWidth(lbl_lines[1], "Helvetica-Bold", 7.5) >= card_w:
                lbl_lines[1] = _fit_cell(c, lbl_lines[1], "Helvetica-Bold", 7.5, card_w)
            lyy = card_y + 12
            for ll in lbl_lines:
                c.drawString(cx, lyy, ll)
                lyy -= 10
            if chg and chg != val:
                c.setFillColorRGB(*col_c); c.setFont("Helvetica-Bold", 7.5)
                c.drawString(cx, lyy, chg)
    # "growth-gradual.com" — placed top-right, level with the overline, so it
    # never collides with the bottom stat-card row (which can run tall when
    # a card's label wraps to two lines).
    c.setFillColorRGB(0.55, 0.60, 0.80); c.setFont("Helvetica", 8)
    c.drawRightString(PAGE_W - MARGIN, overline_y, "growth-gradual.com")
    # Cover-page footer stamp (light text on the navy background) so page
    # numbering is visible from page 1, consistent with the body pages' footer.
    page_num[0] += 1
    c.setFillColorRGB(0.55, 0.60, 0.80); c.setFont("Helvetica", 7)
    c.drawRightString(PAGE_W - MARGIN, 16, f"Page {page_num[0]}")
    c.drawString(MARGIN, 16, f"{_fit_cell(c, _safe_text(title) or 'Market Intelligence Report', 'Helvetica', 7, PAGE_W - 2 * MARGIN - 70 - c.stringWidth(' · Growth Gradual', 'Helvetica', 7))} · Growth Gradual")
    c.showPage()

    # ═══════════════════════════════════════════════════════════════════════════
    # PAGES 2+ — REPORT BODY
    # ═══════════════════════════════════════════════════════════════════════════
    # Section counter used for accent colour cycling
    section_idx = [0]
    current_section = [""]

    def start_page(sec: str = ""):
        hf(sec)
        y[0] = BODY_TOP

    start_page()

    # Section number colour accent cycling
    def accent():
        return SECTION_ACCENTS[section_idx[0] % len(SECTION_ACCENTS)]

    # ── Inline infographic stat-card strip ───────────────────────────────────
    # The cover page only has room for 4 keyStats; this renders additional
    # batches inline in the body so the report's data points show up as
    # scannable cards (not just buried in prose) — mirrors the reference
    # report's "RBI Policy Snapshot" style stat blocks.
    def stat_strip(stats_subset: list, heading_label: str = "", strip_color: tuple = None):
        stats_subset = [s for s in (stats_subset or []) if s.get("value") or s.get("label")]
        if not stats_subset:
            return
        n = len(stats_subset)
        gap = 12
        card_w = (CW - gap * (n - 1)) / n
        STRIP_H = 54
        LABEL_H = 12 if heading_label else 0
        need(STRIP_H + LABEL_H + 24, current_section[0])
        nl(14)
        if heading_label:
            c.setFillColorRGB(*GREY); c.setFont("Helvetica-BoldOblique", 7)
            c.drawString(MARGIN, y[0], heading_label.upper())
            nl(LABEL_H)
        bottom = y[0] - STRIP_H
        _col = strip_color if strip_color is not None else accent()
        card_tint = _mix(WHITE, _col, 0.12)
        card_border = _mix(WHITE, _col, 0.35)
        for i, st in enumerate(stats_subset):
            cx = MARGIN + i * (card_w + gap)
            c.setFillColorRGB(*card_tint)
            c.roundRect(cx, bottom, card_w, STRIP_H, 4, fill=1, stroke=0)
            c.setStrokeColorRGB(*card_border); c.setLineWidth(0.6)
            c.roundRect(cx, bottom, card_w, STRIP_H, 4, fill=0, stroke=1)
            val_full = _safe_text(fmt_inr(str(st.get("value", ""))))
            lbl_raw = _safe_text(st.get("label", "")).upper()
            chg = _safe_text(st.get("change", ""))
            col_c = GREEN if chg.startswith("+") or val_full.startswith("+") else (RED if chg.startswith("-") or val_full.startswith("-") else NAVY)
            c.setFillColorRGB(*col_c); c.setFont("Helvetica-Bold", 13)
            # Pixel-width-aware fit (was a blind [:14] char slice — see the
            # matching fix on the cover-page stat cards above for why that
            # silently mangled longer values like "Rs.49.07 Lac Cr").
            val = _fit_cell(c, val_full, "Helvetica-Bold", 13, card_w - 16)
            c.drawString(cx + 8, bottom + STRIP_H - 22, val)
            c.setFillColorRGB(*GREY); c.setFont("Helvetica-Bold", 6.5)
            # Pixel-width-aware wrap (was a blind [:28] char slice, which cut
            # mid-word with no ellipsis — e.g. "...MILLIONAIRES WITHO" — since
            # 28 characters is not a fixed width across different letters).
            lbl_max_w = card_w - 16
            lbl_lines = _wrap(c, lbl_raw, "Helvetica-Bold", 6.5, lbl_max_w)[:2]
            if len(lbl_lines) == 2 and c.stringWidth(lbl_lines[1], "Helvetica-Bold", 6.5) >= lbl_max_w:
                lbl_lines[1] = _fit_cell(c, lbl_lines[1], "Helvetica-Bold", 6.5, lbl_max_w)
            lyy = bottom + STRIP_H - 34
            for ll in lbl_lines:
                c.drawString(cx + 8, lyy, ll)
                lyy -= 8
            if chg and chg != val:
                c.setFillColorRGB(*col_c); c.setFont("Helvetica-Bold", 7)
                c.drawString(cx + 8, bottom + 6, chg)
        y[0] = bottom - 14

    # ── Dynamic stat-strip cadence ───────────────────────────────────────────
    # Rather than hardcoding "one strip after Executive Summary, one before
    # Conclusion" (which forced every report into the same rhythm regardless
    # of how many sections it actually had, or where its data-dense parts
    # fell), a strip is dropped in after whichever sections turn out to be
    # data-rich (a table, or 3+ bullets/numbered points) — up to a cap — so
    # the cadence tracks the actual shape of the content instead of a fixed
    # template. The first 4 keyStats are reserved for the cover; the rest
    # are drawn down here as data-rich sections are encountered.
    _STAT_STRIP_MAX = 3
    _stat_strip_count = [0]
    _stat_pool = list(key_stats[4:]) if key_stats else []
    _sec_signal = {"bullets": 0, "tables": 0}
    _sec_text_buf = [""]   # plain text seen so far in the CURRENT section, for stat relevance matching

    _STOPWORDS = {"the", "and", "for", "with", "from", "this", "that", "yield",
                  "price", "change", "return", "value", "level", "close", "rate"}

    def _pick_relevant_stats(pool: list, section_text: str, k: int = 4) -> list:
        """Pull up to k stats out of `pool` (in place) that are actually about
        this section's topic, instead of just taking whichever 4 happen to be
        next in the list. A stat like "Marqeta 5-Yr Return" showing up under
        an "Energy Markets" section (because it was simply next in line) reads
        as a mistake — this scores each candidate by whether its label's
        distinctive words appear anywhere in the section's own text, and only
        falls back to FIFO order if nothing in the pool actually matches."""
        if not pool:
            return []
        text_lower = section_text.lower()
        scored = []
        for st in pool:
            label = str(st.get("label", "")).lower()
            words = [w.strip(".,()%") for w in re.split(r"\s+", label) if len(w.strip(".,()%")) >= 4]
            words = [w for w in words if w not in _STOPWORDS]
            score = sum(1 for w in words if w in text_lower)
            scored.append((score, st))
        matched = [st for score, st in scored if score > 0]
        if not matched:
            take = pool[:k]
            del pool[:k]
            return take
        take = matched[:k]
        for st in take:
            pool.remove(st)
        return take

    # ── Token renderer ─────────────────────────────────────────────────────────
    tokens = list(_tokenise(report))
    chart_idx = [0]   # next chart to render
    rendered_charts: set[int] = set()  # indices of charts already rendered inline
    _seen_table_sigs: set[str] = set()  # dedup identical tables (LLM sometimes repeats them)
    _seen_line_sigs: set[str] = set()   # dedup identical bullets/numbered items/paragraphs

    # Data Sources is the last REQUIRED ANCHOR in the system prompt's section
    # order — "the ONLY sources listing... nothing more". In practice the
    # model occasionally tacks on a stray token after that table anyway (a
    # [CHART_n] placeholder with no heading/context above it, an extra
    # paragraph, etc.), which renders as an orphaned, unlabeled chart sitting
    # alone on a trailing page with no surrounding text — seen in production.
    # Once we've rendered the Data Sources heading + its one table, drop
    # every token that follows instead of rendering it.
    _past_data_sources = [False]
    _reached_data_sources_heading = False

    def _line_sig(text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().strip(" .,-—:"))

    for tok in tokens:
        tp = tok["type"]

        if _past_data_sources[0]:
            continue

        # ── Chart placeholder ────────────────────────────────────────────────
        if tp == "chart_placeholder":
            ci = tok.get("index", chart_idx[0])
            chart_idx[0] = ci + 1
            if ci < len(charts) and _is_valid_chart(charts[ci]):
                ch = charts[ci]
                _sec_text_buf[0] += " " + str(ch.get("title", "")).lower()
                GAP_ABOVE = 8      # breathing room between previous content and this card

                # When a Datawrapper PNG is available the title and axes are baked
                # into the image — no separate ReportLab title bar needed, and we
                # give the card more height so the PNG fills it properly.
                # Bumped from 220/185 — the extra vertical room lets Datawrapper's
                # own renderer space out gridlines, tick labels and value labels
                # instead of cramming them into a shorter frame, which is what
                # made charts read as small/busy at the old sizing.
                has_dw_png = bool((ch.get("datawrapper") or {}).get("pngBytes"))
                CHART_H    = 260 if has_dw_png else 215   # DW PNG is taller (includes title)
                CARD_HEADER = 0 if has_dw_png else 26     # 0 = no title bar, PNG fills card

                # Row-based chart types (arrow-plots, tables) now export at
                # "auto" height (see utils/datawrapper.py _export_dims) so
                # a 2-3 row chart isn't forced into the same fixed export
                # box as a full-size bar/line chart. But the card height
                # here was still a flat 220pt regardless — a short PNG just
                # ends up centred in a lot of extra white padding instead
                # of the old "content squeezed into 15% of an oversized
                # image" bug. Size the card to the PNG's real proportions
                # (at the card's fixed width) for these chart types,
                # clamped so it never gets absurdly short or taller than
                # the original fixed allocation.
                if has_dw_png:
                    dw_type = str(ch.get("type", "")).lower()
                    if dw_type in ("arrow", "table", "bullet"):
                        try:
                            png_bytes = (ch.get("datawrapper") or {}).get("pngBytes")
                            if isinstance(png_bytes, str):
                                import base64 as _b64chk
                                png_bytes = _b64chk.b64decode(png_bytes)
                            from reportlab.lib.utils import ImageReader as _IR
                            _pw, _ph = _IR(io.BytesIO(png_bytes)).getSize()
                            fitted_h = (CW - 16) * (_ph / _pw)
                            CHART_H = max(80, min(260, fitted_h))
                        except Exception:
                            pass   # fall back to the flat 220pt default above

                CARD_TOTAL = CHART_H + CARD_HEADER + 10 + GAP_ABOVE
                need(CARD_TOTAL, current_section[0])
                nl(GAP_ABOVE)

                card_top    = y[0]                       # top of card (just below the gap)
                card_bottom = card_top - (CARD_HEADER + CHART_H + 4)

                # White card background
                c.setFillColorRGB(1, 1, 1)
                c.roundRect(MARGIN, card_bottom, CW, card_top - card_bottom, 5, fill=1, stroke=0)
                c.setStrokeColorRGB(0.87, 0.9, 0.95)
                c.roundRect(MARGIN, card_bottom, CW, card_top - card_bottom, 5, fill=0, stroke=1)

                if has_dw_png:
                    # Datawrapper PNG already contains the chart title — fill the
                    # entire card with the image, no separate title bar needed.
                    chart_top = card_top - 4
                else:
                    # Title bar — sits inside the card, near its top
                    title_y = card_top - 18
                    c.setFillColorRGB(*accent())
                    c.rect(MARGIN, title_y - 2, 4, 14, fill=1, stroke=0)
                    c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 9)
                    c.drawString(MARGIN + 10, title_y, _safe_text(ch.get("title", ""))[:80])
                    c.setStrokeColorRGB(0.9, 0.92, 0.96); c.setLineWidth(0.6)
                    c.line(MARGIN + 8, title_y - 6, MARGIN + CW - 8, title_y - 6)
                    chart_top = title_y - 10

                # Chart body — fills remaining card area below the title bar
                _draw_chart(c, ch, MARGIN + 8, chart_top - CHART_H, CW - 16, CHART_H)
                y[0] = card_bottom - 14   # gap below the card before next content
                rendered_charts.add(ci)
            continue

        # ── Extracted file image placeholder (chart/figure/photo pulled out of
        #    the uploaded file — never a full-page screenshot) ────────────────
        if tp == "file_img_placeholder":
            imgs = file_images or []
            pi = tok.get("index", 0)
            if pi < len(imgs):
                img_info = imgs[pi]
                try:
                    import base64 as _b64
                    import io as _io
                    img_data = _b64.b64decode(img_info["data"])
                    pil_img = PILImage.open(_io.BytesIO(img_data))
                    pil_img = _flatten_to_rgb(pil_img)
                    # Scale to fit content width. Max height kept modest (220pt)
                    # since these are individual extracted charts/figures, not
                    # full pages — a full page would need ~340pt+ to stay legible.
                    MAX_IMG_W = CW
                    MAX_IMG_H = 220
                    ow, oh = pil_img.size
                    scale = min(MAX_IMG_W / ow, MAX_IMG_H / oh, 1.0)
                    iw, ih = ow * scale, oh * scale
                    need(ih + 32, current_section[0])
                    nl(10)
                    # Caption bar above image
                    cap = img_info.get("name", f"Figure {pi+1}")[:80]
                    cx = MARGIN + (CW - iw) / 2
                    c.setFillColorRGB(0.95, 0.96, 0.99)
                    c.roundRect(MARGIN, y[0] - ih - 26, CW, ih + 26, 4, fill=1, stroke=0)
                    c.setStrokeColorRGB(0.87, 0.9, 0.95)
                    c.roundRect(MARGIN, y[0] - ih - 26, CW, ih + 26, 4, fill=0, stroke=1)
                    c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 8)
                    c.drawString(MARGIN + 8, y[0] - 14, f"🖼 {cap}")
                    # Draw image
                    img_buf = _io.BytesIO()
                    pil_img.save(img_buf, format="JPEG", quality=85)
                    img_buf.seek(0)
                    img_reader = ImageReader(img_buf)
                    c.drawImage(img_reader, cx, y[0] - ih - 22, width=iw, height=ih, mask="auto")
                    y[0] = y[0] - ih - 36
                except Exception as exc:
                    log.warning("FILE_IMG_%d render failed: %s", pi + 1, exc)
            continue

        # ── Web-search image placeholder (fetched from Tavily image results) ──
        if tp == "web_img_placeholder":
            imgs = web_images or []
            wi = tok.get("index", 0)
            if wi < len(imgs) and imgs[wi]:
                img_info = imgs[wi]
                try:
                    import base64 as _b64
                    import io as _io
                    img_data = _b64.b64decode(img_info["data"])
                    pil_img = PILImage.open(_io.BytesIO(img_data))
                    pil_img = _flatten_to_rgb(pil_img)
                    MAX_IMG_W = CW
                    MAX_IMG_H = 260
                    ow, oh = pil_img.size
                    if ow <= 0 or oh <= 0:
                        log.warning("WEB_IMG_%d has zero dimension (%dx%d) — skipping", wi + 1, ow, oh)
                        continue
                    scale = min(MAX_IMG_W / ow, MAX_IMG_H / oh, 1.0)
                    iw, ih = ow * scale, oh * scale
                    if iw < 10 or ih < 10:
                        log.warning("WEB_IMG_%d scaled too small (%.1fx%.1f) — skipping", wi + 1, iw, ih)
                        continue
                    cap = _truncate_words(img_info.get("caption") or "", 120)
                    cap_h = 18 if cap else 0
                    need(ih + 26 + cap_h, current_section[0])
                    nl(10)
                    cx = MARGIN + (CW - iw) / 2
                    c.setFillColorRGB(0.97, 0.97, 0.98)
                    c.roundRect(MARGIN, y[0] - ih - 22 - cap_h, CW, ih + 22 + cap_h, 4, fill=1, stroke=0)
                    c.setStrokeColorRGB(0.88, 0.89, 0.91)
                    c.roundRect(MARGIN, y[0] - ih - 22 - cap_h, CW, ih + 22 + cap_h, 4, fill=0, stroke=1)
                    img_buf = _io.BytesIO()
                    pil_img.save(img_buf, format="JPEG", quality=85)
                    img_buf.seek(0)
                    img_reader = ImageReader(img_buf)
                    c.drawImage(img_reader, cx, y[0] - ih - 10, width=iw, height=ih, mask="auto")
                    if cap:
                        c.setFillColorRGB(0.4, 0.42, 0.46); c.setFont("Helvetica-Oblique", 7.5)
                        cap_fit = _fit_cell(c, cap, "Helvetica-Oblique", 7.5, CW - 20)
                        c.drawCentredString(MARGIN + CW / 2, y[0] - ih - 10 - cap_h + 5, cap_fit)
                    y[0] = y[0] - ih - 22 - cap_h - 10
                except Exception as exc:
                    import traceback as _tb
                    log.warning("WEB_IMG_%d render failed: %s\n%s", wi + 1, exc, _tb.format_exc())
            continue
        # ── Pull-quote / insight callout (from a markdown blockquote) ───────────
        if tp == "quote":
            text = _strip_inline(tok.get("text", ""))
            if not text:
                continue
            QLINE_H = 16
            wlines = _wrap(c, text, "Helvetica-Oblique", 11, CW - 52)
            box_h = len(wlines) * QLINE_H + 26
            need(box_h + 18, current_section[0])
            nl(12)
            top = y[0]
            bottom = top - box_h
            col = accent()
            # Warm cream card — deliberately distinct from the white chart/
            # stat cards so a callout reads as "stop and notice this", not
            # just another data card.
            c.setFillColorRGB(0.975, 0.965, 0.94)
            c.roundRect(MARGIN, bottom, CW, box_h, 5, fill=1, stroke=0)
            c.setFillColorRGB(*col)
            c.rect(MARGIN, bottom, 4, box_h, fill=1, stroke=0)
            c.setFont("Times-Bold", 30)
            c.drawString(MARGIN + 14, top - 24, "\u201C")
            ty2 = top - 20
            c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Oblique", 11)
            for ln in wlines:
                ty2 -= QLINE_H
                c.drawString(MARGIN + 42, ty2 + 4, ln)
            y[0] = bottom - 14
            continue

        if tp == "hr":
            need(12, current_section[0])
            c.setStrokeColorRGB(0.87, 0.9, 0.94); c.setLineWidth(0.6)
            c.line(MARGIN, y[0], PAGE_W - MARGIN, y[0])
            nl(10); continue

        # ── H1 (report title — skip, already on cover) ───────────────────────
        if tp == "h1":
            # Skip rendering the H1 title since it's on the cover page
            continue

        # ── H2 — major section heading ───────────────────────────────────────
        if tp == "h2":
            section_idx[0] += 1
            text = _strip_inline(tok["text"])
            # Strip any leading "N. " the LLM already put in the heading text
            # itself, since we render our own "SECTION 0N" overline instead —
            # avoids a duplicated/mismatched number ("SECTION 02" over "2. Title").
            text_display = re.sub(r"^\d+\.\s*", "", text)
            if text_display.strip().lower() in ("data sources", "sources", "data source"):
                _reached_data_sources_heading = True
            else:
                _reached_data_sources_heading = False

            # Drop in an infographic stat-card strip right as we LEAVE a
            # section that turned out data-rich (a table, or 3+ bullets/
            # numbered points) — using the keyStats batches the cover page
            # had no room for — so cards show up where the data actually is,
            # not at two fixed headings every single report repeats.
            # section_idx was already bumped above for the section we're about
            # to render, so the section that just ENDED (the one the strip
            # represents) is one index back — look that colour up explicitly
            # rather than calling accent(), which would give the new section's
            # colour instead of the one the stat card is actually reporting on.
            # NOTE: this used to call stat_strip(...) immediately here, i.e.
            # on the OUTGOING section's page, right before the unconditional
            # page break below. When that outgoing page was already nearly
            # full, need() inside stat_strip pushed the strip onto a brand
            # new page by itself — then the forced h2 page-break a few lines
            # down immediately started ANOTHER new page, leaving the strip's
            # page with just 2-4 small cards and a wall of empty space below
            # (e.g. an "— Key Metrics" page with two stat cards and nothing
            # else). The strip is now deferred and drawn just under the new
            # section's heading instead, so it always lands on a page that
            # already has real content following it.
            _prev_accent = SECTION_ACCENTS[(section_idx[0] - 1) % len(SECTION_ACCENTS)]
            _section_was_data_rich = _sec_signal["tables"] >= 1 or _sec_signal["bullets"] >= 3
            _pending_strip = None
            if (current_section[0] and _section_was_data_rich
                    and _stat_pool and _stat_strip_count[0] < _STAT_STRIP_MAX):
                _take = _pick_relevant_stats(_stat_pool, _sec_text_buf[0])
                if _take:
                    _pending_strip = (_take, f"{current_section[0][:44]} — Key Metrics", _prev_accent)
                    _stat_strip_count[0] += 1
            elif ("conclusion" in text_display.lower() and _stat_pool
                    and _stat_strip_count[0] < _STAT_STRIP_MAX):
                # Safety net: make sure any leftover stats still surface
                # somewhere rather than silently disappearing.
                _take = _stat_pool[:4]
                del _stat_pool[:4]
                _pending_strip = (_take, "At a Glance", _prev_accent)
                _stat_strip_count[0] += 1
            _sec_signal["bullets"] = 0
            _sec_signal["tables"] = 0
            _sec_text_buf[0] = ""

            current_section[0] = text
            # Every major (H2) section heading now ALWAYS starts at the very
            # top of a fresh page — never mid-page, never near the bottom.
            # A soft "is there enough room?" check (the old need(190, ...))
            # still let a heading land mid-page whenever ~190pt happened to
            # be free, which is exactly what produced headings sitting in
            # the middle of a page with unrelated content above them, or
            # (when the guess was too small) squeezed near the bottom with
            # its body pushed to the next page. Forcing an unconditional
            # page break here — unless we're already at the top of a blank
            # page, i.e. this is the very first section right after the
            # intro page — guarantees "SECTION 0N" + its title is always the
            # first thing a reader sees on its page.
            if abs(y[0] - BODY_TOP) > 0.5:
                c.showPage(); hf(text); y[0] = BODY_TOP
            nl(20)   # visible gap before section heading block
            col = accent()
            # Gold "SECTION 0N" overline, letter-spaced small caps
            ov = f"SECTION {section_idx[0]:02d}"
            c.setFillColorRGB(*GOLD); c.setFont("Helvetica-Bold", 8.5)
            _ox = MARGIN
            for _ch in ov:
                c.drawString(_ox, y[0], _ch)
                _ox += c.stringWidth(_ch, "Helvetica-Bold", 8.5) + 1.4
            nl(20)
            # Serif navy title, wraps to 2 lines if needed
            title_font_sz = 16
            while title_font_sz > 11 and c.stringWidth(text_display, "Times-Bold", title_font_sz) > CW:
                title_font_sz -= 1
            title_words = text_display.split()
            t_line1, t_line2 = [], []
            for w in title_words:
                test = " ".join(t_line1 + [w])
                if c.stringWidth(test, "Times-Bold", title_font_sz) <= CW:
                    t_line1.append(w)
                else:
                    t_line2.append(w)
            c.setFillColorRGB(*NAVY); c.setFont("Times-Bold", title_font_sz)
            c.drawString(MARGIN, y[0], " ".join(t_line1)[:90])
            if t_line2:
                nl(title_font_sz + 4)
                c.drawString(MARGIN, y[0], " ".join(t_line2)[:90])
            nl(12)
            # Thin gold rule under the title, in the section's accent colour
            c.setStrokeColorRGB(*col); c.setLineWidth(1.4)
            c.line(MARGIN, y[0], MARGIN + CW, y[0])
            nl(20)
            # Any stat strip carried over from the section that just ended
            # (see note above) renders here — top of the new page, directly
            # under its heading — instead of dangling alone on the previous,
            # now-empty page.
            if _pending_strip:
                _take, _label, _color = _pending_strip
                stat_strip(_take, _label, strip_color=_color)
            continue

        # ── H3 — sub-section ─────────────────────────────────────────────────
        if tp == "h3":
            text = _strip_inline(tok["text"])
            # Was need(80, ...) — same "orphaned heading" risk as h2 above,
            # just smaller scale: bump the guaranteed post-banner slack.
            need(120, current_section[0])
            nl(10)   # visible gap before sub-section
            # Section-accent-tinted background (was a fixed mid-navy block for
            # every section) — each section now reads as visually its own.
            c.setFillColorRGB(*_mix(accent(), NAVY, 0.35))
            c.rect(MARGIN, y[0] - 4, CW, 20, fill=1, stroke=0)
            c.setFillColorRGB(*accent())
            c.rect(MARGIN, y[0] - 4, 3, 20, fill=1, stroke=0)
            c.setFillColorRGB(*WHITE); c.setFont("Helvetica-Bold", 10.5)
            c.drawString(MARGIN + 9, y[0] + 4, text[:90])
            nl(28); continue   # gap after sub-section banner

        # ── H4 ───────────────────────────────────────────────────────────────
        if tp == "h4":
            text = _strip_inline(tok["text"])
            need(20, current_section[0])
            nl(6)
            c.setFillColorRGB(0.22, 0.28, 0.52); c.setFont("Helvetica-Bold", 10)
            c.drawString(MARGIN, y[0], text[:90])
            nl(14); continue

        # ── Bullet ───────────────────────────────────────────────────────────
        if tp == "bullet":
            plain_text = _strip_inline(tok["text"])
            _sig = _line_sig(plain_text)
            if len(_sig) > 12 and _sig in _seen_line_sigs:
                continue  # exact repeat of an earlier bullet — skip, don't say it twice
            _seen_line_sigs.add(_sig)
            _sec_signal["bullets"] += 1
            _sec_text_buf[0] += " " + plain_text.lower()
            wlines = _wrap(c, plain_text, "Helvetica", 10, CW - 16)
            for li, ln in enumerate(wlines):
                need(15, current_section[0])
                if li == 0:
                    c.setFillColorRGB(*accent())
                    c.circle(MARGIN + 5, y[0] + 3.5, 2.5, fill=1, stroke=0)
                _draw_rich_line(c, MARGIN + 15, y[0], ln, "Helvetica", "Helvetica-Bold", 10, BODY_TXT, CW - 15,
                                 justify=(li < len(wlines) - 1))
                nl(15)
            continue

        # ── Numbered item ────────────────────────────────────────────────────
        if tp == "numbered":
            plain_text = _strip_inline(tok["text"])
            _sig = _line_sig(plain_text)
            if len(_sig) > 12 and _sig in _seen_line_sigs:
                continue  # exact repeat of an earlier finding/item — skip
            _seen_line_sigs.add(_sig)
            _sec_signal["bullets"] += 1  # numbered items count toward "data-rich" same as bullets
            _sec_text_buf[0] += " " + plain_text.lower()
            num = tok.get("num", "•")
            indent = 18
            wlines = _wrap(c, plain_text, "Helvetica", 10, CW - indent)
            for li, ln in enumerate(wlines):
                need(15, current_section[0])
                if li == 0:
                    c.setFillColorRGB(*accent()); c.setFont("Helvetica-Bold", 10)
                    c.drawString(MARGIN, y[0], f"{num}.")
                _draw_rich_line(c, MARGIN + indent, y[0], ln, "Helvetica", "Helvetica-Bold", 10, BODY_TXT, CW - indent,
                                 justify=(li < len(wlines) - 1))
                nl(15)
            continue

        # ── Paragraph ────────────────────────────────────────────────────────
        if tp == "para":
            raw_text = tok["text"]
            plain_text = _strip_inline(raw_text)
            # Suppress LLM "Note:" disclaimers about missing/unavailable data —
            # they become orphaned and misleading once the empty table is suppressed.
            _tlow = plain_text.lower().strip()
            _DATA_NOTE_HINTS = (
                "note: specific numerical data", "note: the table above indicates",
                "were not directly extractable", "data not available",
                "not directly available in the provided snippets",
            )
            if any(h in _tlow for h in _DATA_NOTE_HINTS):
                continue
            _sig = _line_sig(plain_text)
            if len(_sig) > 40 and _sig in _seen_line_sigs:
                continue  # exact repeat of an earlier paragraph — skip
            _seen_line_sigs.add(_sig)
            _sec_text_buf[0] += " " + plain_text.lower()
            wlines = _wrap(c, plain_text, "Helvetica", 10, CW)
            for li, ln in enumerate(wlines):
                need(15, current_section[0])
                _draw_rich_line(c, MARGIN, y[0], ln, "Helvetica", "Helvetica-Bold", 10, BODY_TXT, CW,
                                 justify=(li < len(wlines) - 1))
                nl(15)
            nl(6); continue

        # ── Table ─────────────────────────────────────────────────────────────
        if tp == "table":
            rows = tok["rows"]
            if not rows:
                continue
            # Deduplicate: skip table if an identical one was already rendered
            _tbl_sig = "|".join(",".join(r) for r in rows[:3])
            if _tbl_sig in _seen_table_sigs:
                continue
            _seen_table_sigs.add(_tbl_sig)
            _sec_signal["tables"] += 1
            _sec_text_buf[0] += " " + " ".join(str(cell) for r in rows for cell in r).lower()
            col_count = max(len(r) for r in rows)

            # Column widths sized to each column's actual content (header or
            # any cell), not a blind even split — an even split starves a
            # long free-text column (e.g. "Data Type" in the Data Sources
            # table) down to ~30 chars, forcing a hard single-line ellipsis
            # truncation even though the table has plenty of vertical room
            # to just wrap onto a second line instead.
            def _md_col_weight(ci: int) -> float:
                longest = 6
                for r in rows:
                    if ci < len(r):
                        longest = max(longest, len(_strip_inline(str(r[ci]))))
                return min(longest, 60)
            weights = [_md_col_weight(ci) for ci in range(col_count)]
            total_wt = sum(weights) or col_count
            min_col_w = 60.0
            col_ws = [max(min_col_w, CW * (wt / total_wt)) for wt in weights]
            scale = CW / sum(col_ws)
            col_ws = [cw * scale for cw in col_ws]
            col_x = [MARGIN]
            for cw in col_ws[:-1]:
                col_x.append(col_x[-1] + cw)

            LINE_H = 11
            for ri, row in enumerate(rows):
                if ri == 0:
                    tfont, tsize, tcol = "Helvetica-Bold", 7.5, WHITE
                else:
                    tfont, tsize, tcol = "Helvetica", 8.5, BODY_TXT

                # Pre-wrap every cell in this row using its column's real
                # width so the row is drawn at the height it actually needs
                # (up to 3 lines) instead of a fixed single-line ROW_H.
                cell_strs = []
                for ci in range(col_count):
                    cell = row[ci] if ci < len(row) else ""
                    cell_str = str(cell)
                    if ri > 0 and ci > 0:
                        cell_str = fmt_inr(cell_str)
                    cell_strs.append(_strip_inline(cell_str))
                wrapped_cells = [
                    _wrap_to_width(c, cell_strs[ci], tfont, tsize, col_ws[ci] - 8, max_lines=3)
                    for ci in range(col_count)
                ]
                n_lines = max(len(wc) for wc in wrapped_cells)
                ROW_H = max(15, n_lines * LINE_H + 6)
                need(ROW_H + 2, current_section[0])

                if ri == 0:
                    c.setFillColorRGB(*_mix(accent(), NAVY, 0.4))
                    c.rect(MARGIN, y[0] - ROW_H + 4, CW, ROW_H, fill=1, stroke=0)
                elif ri % 2 == 0:
                    c.setFillColorRGB(0.96, 0.97, 1.0)
                    c.rect(MARGIN, y[0] - ROW_H + 4, CW, ROW_H, fill=1, stroke=0)

                c.setStrokeColorRGB(0.87, 0.9, 0.94); c.setLineWidth(0.4)
                c.rect(MARGIN, y[0] - ROW_H + 4, CW, ROW_H, fill=0, stroke=1)

                for ci, lines in enumerate(wrapped_cells):
                    cx2 = col_x[ci] + 4
                    # Gain/loss cells (e.g. "+6.4%", "-9.56%") get green/red ink so
                    # the table reads at a glance, same as the chart bars do.
                    cell_color = _signed_cell_color(" ".join(lines)) if ri > 0 else None
                    c.setFillColorRGB(*(cell_color or tcol)); c.setFont(tfont, tsize)
                    ty2 = y[0] - ROW_H + 4 + ROW_H - LINE_H + 1
                    for line in lines:
                        c.drawString(cx2, ty2, line)
                        ty2 -= LINE_H
                nl(ROW_H)
            nl(8)
            if _reached_data_sources_heading:
                # This table is the Data Sources table — it's the last thing
                # allowed to render. See the module-level note where
                # _past_data_sources is declared.
                _past_data_sources[0] = True

    # ── Complete source appendix ─────────────────────────────────────────────
    # The writer sees a curated source subset to keep its prompt manageable,
    # but exports must show the entire provenance inventory.  This renderer is
    # intentionally independent of whatever abbreviated source table the LLM
    # may have put in the prose, and it has no row cap: 100 inputs render as
    # 100 numbered source cards over as many appendix pages as required.
    source_manifest = ensure_cited_source_records(report, sources)
    if source_manifest:
        c.showPage(); hf("Complete Data Sources"); y[0] = BODY_TOP

        def _source_heading(continued: bool = False) -> None:
            c.setFillColorRGB(*GOLD); c.setFont("Helvetica-Bold", 8.5)
            c.drawString(MARGIN, y[0], "SOURCES CONTINUED" if continued else "COMPLETE DATA SOURCES")
            nl(20)
            c.setFillColorRGB(*NAVY); c.setFont("Times-Bold", 18 if not continued else 14)
            c.drawString(MARGIN, y[0], "Data Sources" if not continued else "Data Sources — continued")
            nl(12)
            c.setStrokeColorRGB(*GOLD); c.setLineWidth(1.2)
            c.line(MARGIN, y[0], MARGIN + CW, y[0])
            nl(18)
            if not continued:
                pass

        _source_heading()
        # The source manifest already canonicalizes raw retrieval results. Do NOT
        # dedupe or filter entries by URL here: canonical registries can contain
        # sparse IDs (e.g. S1,S2,S4) and URL-less verified-data/document records.
        # Dropping a row at the renderer boundary makes valid in-text [S#] labels
        # point to nothing and is a common cause of "source not found" in exports.
        for index, source in enumerate(source_manifest, start=1):
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            source_id = str(source.get("id") or f"S{index}").strip().upper()
            source_title = str(source.get("title") or source.get("publisher") or url or "Untitled source").strip()
            source_publisher = str(source.get("publisher") or source.get("domain") or "").strip()
            source_kind = str(source.get("source_type") or source.get("kind") or "").strip()
            heading_text = f"[{source_id}] {source_title}" + (f" — {source_publisher}" if source_publisher and source_publisher.casefold() not in source_title.casefold() else "")
            heading_lines = _wrap(c, heading_text, "Helvetica-Bold", 8.4, CW - 8)
            if url:
                url_lines = _wrap(c, url, "Helvetica", 8.2, CW - 8)
            else:
                # Unlinked sources are identified plainly without leaking the
                # internal page-fetch/extraction state into the client appendix.
                description = "No public URL available" + (f" · {source_kind}" if source_kind else "")
                url_lines = _wrap(c, description, "Helvetica-Oblique", 8.0, CW - 8)
            row_h = max(36, 8 + len(heading_lines) * 9 + len(url_lines) * 9)
            if y[0] - row_h < BODY_BOT:
                c.showPage(); hf("Complete Data Sources"); y[0] = BODY_TOP
                _source_heading(continued=True)

            top = y[0]
            c.setFillColorRGB(0.975, 0.98, 0.995)
            c.roundRect(MARGIN, top - row_h + 4, CW, row_h, 5, fill=1, stroke=0)
            c.setStrokeColorRGB(0.86, 0.89, 0.95); c.setLineWidth(0.45)
            c.roundRect(MARGIN, top - row_h + 4, CW, row_h, 5, fill=0, stroke=1)
            text_x = MARGIN + 4
            text_y = top - 10
            c.setFillColorRGB(*NAVY); c.setFont("Helvetica-Bold", 8.4)
            for line in heading_lines:
                c.drawString(text_x, text_y, line)
                text_y -= 9
            c.setFillColorRGB(*TEAL if url else (0.36, 0.39, 0.43))
            c.setFont("Helvetica" if url else "Helvetica-Oblique", 8.2 if url else 8.0)
            for line in url_lines:
                c.drawString(text_x, text_y, line)
                if url:
                    line_w = c.stringWidth(line, "Helvetica", 8.2)
                    try:
                        c.linkURL(url, (text_x, text_y - 2, text_x + line_w, text_y + 8), relative=0, thickness=0)
                    except Exception:
                        pass
                text_y -= 9
            y[0] -= row_h + 6

    # Fallback chart rendering is intentionally suppressed.
    # The LLM system prompt places all charts inline via [CHART_n] placeholders.
    # Dumping leftovers after References corrupts the document structure.

    # Disclaimer removed per product requirement

    c.save()
    return buf.getvalue()



# ─── Dynamic PDF builder ──────────────────────────────────────────────────────
def _pdf_data_uri_from_base64(data: str, mime: str = "image/jpeg") -> str:
    payload = str(data or "").strip()
    if payload.startswith("data:image/"):
        return payload
    return f"data:{mime};base64,{payload}" if payload else ""


def _image_mime_from_base64(payload: str) -> str:
    try:
        raw = base64.b64decode(payload, validate=False)[:16]
    except Exception:
        return "image/jpeg"
    if raw.startswith(b"\x89PNG"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


# A tiny valid transparent PNG used when a selected web image could not be
# downloaded (for example a 403 from the publisher CDN).  We must preserve the
# original image slot because report text already contains [WEB_IMG_n] tokens,
# but passing None into the HTML/PDF presentation pipeline is unsafe.
_PDF_EMPTY_IMAGE_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

def _image_bytes_look_editorial(content: bytes) -> bool:
    """Reject icons, sprites and banner strips; keep photo/illustration-shaped images.

    Unknown formats PIL cannot read (e.g. some AVIF builds) are allowed through.
    """
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            width, height = img.size
    except Exception:
        return True
    if width < 320 or height < 180:
        return False
    ratio = width / max(1, height)
    return 0.6 <= ratio <= 2.6


def _prepare_pdf_images(images: list, web_images: list | None, file_images: list | None) -> list:
    """Prepare a slot-stable, None-free image list for HTML/PDF rendering.

    Failed web-image downloads intentionally keep their original 1-based slot so
    [WEB_IMG_2] can never accidentally render image 1. A transparent placeholder
    is used for a failed slot instead of None, allowing every downstream renderer
    to receive the same dictionary shape.
    """
    out = list(images or [])
    if not out:
        out = list(web_images or [])
    for i, info in enumerate(web_images or []):
        if i >= len(out):
            out.append(None)
        if not isinstance(info, dict):
            out[i] = {
                "url": _pdf_data_uri_from_base64(_PDF_EMPTY_IMAGE_B64, "image/png"),
                "sourceUrl": "",
                "caption": "",
                "_pdf_placeholder": True,
            }
            continue
        payload = str(info.get("data") or "").strip()
        if payload:
            prepared = dict(info)
            prepared.update({
                "url": _pdf_data_uri_from_base64(payload, _image_mime_from_base64(payload)),
                "sourceUrl": info.get("sourceUrl") or info.get("sourceURL") or "",
                "caption": info.get("caption", ""),
            })
            out[i] = prepared
        else:
            # Unfetched image record (pagination preflight): keep its caption
            # and publisher page so the figure and its Source line are laid out
            # like the final export instead of being suppressed.
            out[i] = {
                "url": _pdf_data_uri_from_base64(_PDF_EMPTY_IMAGE_B64, "image/png"),
                "sourceUrl": info.get("sourceUrl") or info.get("sourceURL") or "",
                "sourceTitle": info.get("sourceTitle") or "",
                "publisher": info.get("publisher") or "",
                "method": info.get("method") or "",
                "caption": info.get("caption", ""),
                "_pdf_placeholder": True,
            }
    if not out and file_images:
        for item in file_images:
            if not isinstance(item, dict):
                continue
            payload = str(item.get("data") or "").strip()
            if not payload:
                continue
            out.append({
                "url": _pdf_data_uri_from_base64(payload, item.get("mimeType") or _image_mime_from_base64(payload)),
                "caption": item.get("name", ""),
            })
    return out

def _svg_escape(value: object) -> str:
    import html as _html
    return _html.escape(str(value or ""), quote=True)


from routes.report_design import (
    SVG_AXIS_INK as _SVG_AXIS_INK, SVG_GRID as _SVG_GRID, SVG_LABEL_INK as _SVG_LABEL_INK,
    SVG_TITLE_INK as _SVG_TITLE_INK, SVG_VALUE_INK as _SVG_VALUE_INK, SVG_FONT as _SVG_FONT,
)

_SVG_HBAR_LABEL_MAX = 34
_SVG_GENERIC_AXIS_TITLES = {"category", "categories", "label", "labels", "entity", "entities", "item", "items",
                            "name", "names", "period", "periods", "value", "values", "x", "y", "series", "group"}


def _svg_axis_title_is_generic(title: object) -> bool:
    return str(title or "").strip().strip(":").casefold() in _SVG_GENERIC_AXIS_TITLES


def _svg_wrap_label(label: object, max_chars: int, max_lines: int = 2) -> list[str]:
    """Wrap a tick label on word boundaries; ellipsize only what cannot fit."""
    words = str(label or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = (kept[-1] + " " + " ".join(lines[max_lines:])).strip()
        lines = kept
    out = []
    for line in lines:
        if len(line) > max_chars:
            line = line[: max(1, max_chars - 1)].rstrip() + "…"
        out.append(line)
    return out


def _svg_chart_from_config(config: dict, width: int = 720, height: int = 320, theme: dict | None = None) -> str:
    """Render the chart config emitted by html_report as static SVG.

    This covers the chart forms most commonly emitted by the application and
    gives WeasyPrint a JS-free representation. Unsupported chart types degrade
    to a labelled block instead of silently producing an empty canvas.
    """
    if not isinstance(config, dict):
        return ""
    ctype = str(config.get("type") or "bar").lower()
    data = config.get("data") or {}
    labels = [str(x) for x in (data.get("labels") or [])]
    datasets = data.get("datasets") or []
    options = config.get("options") or {}
    if ctype in ("doughnut", "pie"):
        # Defense-in-depth for saved reports and Action-edited charts that may
        # bypass the report endpoint's chart validator. Never draw an empty
        # ring: the prior SVG fallback used total=1.0 for an empty series and
        # emitted a convincing but data-free doughnut.
        first_dataset = datasets[0] if datasets and isinstance(datasets[0], dict) else {}
        raw_composition = first_dataset.get("data") or []
        derived_labels = labels or [
            str(point.get("label") or point.get("name") or "") if isinstance(point, dict) else ""
            for point in raw_composition
        ]
        composition_values = []
        composition_valid = isinstance(raw_composition, list) and len(raw_composition) >= 2
        for point in raw_composition if isinstance(raw_composition, list) else []:
            raw_value = point.get("value", point.get("y")) if isinstance(point, dict) else point
            try:
                number = float(raw_value)
            except (TypeError, ValueError):
                composition_valid = False
                break
            if not math.isfinite(number) or number < 0:
                composition_valid = False
                break
            composition_values.append(number)
        if (
            not composition_valid or len(derived_labels) < len(composition_values)
            or any(not str(label).strip() for label in derived_labels[:len(composition_values)])
            or sum(composition_values) <= 0
        ):
            return (
                '<div class="gg-pdf-chart-fallback" style="break-inside:avoid;max-width:100%;'
                'border:1px solid #d7dee4;border-radius:8px;padding:14px;color:#52616B;'
                'font-size:10pt;">Chart omitted: validated category labels and numeric values were unavailable.</div>'
            )
    theme = theme or {}
    chart_palette = theme.get("chartColors") or [theme.get("accentColor") or "#c8860a", theme.get("primaryColor") or "#1a1f4e", "#6a8caf", "#b46a55", "#7c6f9f", "#2f7f75", "#a65d7a", "#6c7a89"]
    scales = ((config.get("options") or {}).get("scales") or {})
    x_axis_cfg = scales.get("x") if isinstance(scales, dict) else {}
    y_axis_cfg = scales.get("y") if isinstance(scales, dict) else {}
    x_axis_title = str(((x_axis_cfg or {}).get("title") or {}).get("text") or "").strip()
    y_axis_title = str(((y_axis_cfg or {}).get("title") or {}).get("text") or "").strip()
    x_log = str((x_axis_cfg or {}).get("type") or "").casefold() in {"log", "logarithmic"}
    y_log = str((y_axis_cfg or {}).get("type") or "").casefold() in {"log", "logarithmic"}
    # Combo charts: line datasets drawn over the bars, on their own right axis
    # when the dataset asks for one (yAxisID "y1").
    overlay_lines = [ds for ds in datasets if isinstance(ds, dict) and ds.get("type") == "line"] if ctype == "bar" else []
    if overlay_lines and len(overlay_lines) < len(datasets):
        datasets = [ds for ds in datasets if ds not in overlay_lines]
    else:
        overlay_lines = []
    horizontal_bar = ctype == "bar" and options.get("indexAxis") == "y"
    dataset_names = [str(ds.get("label") or "").strip().lower() for ds in datasets]
    bullet_chart = (
        horizontal_bar
        and any(name == "scale" or "track" in name for name in dataset_names)
        and any("actual" in name for name in dataset_names)
        and any("target" in name for name in dataset_names)
    )
    plugins = options.get("plugins") if isinstance(options, dict) else {}
    legend_cfg = plugins.get("legend") if isinstance(plugins, dict) else {}
    show_legend = legend_cfg.get("display") if isinstance(legend_cfg, dict) else None
    if show_legend is None:
        show_legend = len(datasets) > 1
    margin_l, margin_r, margin_t, margin_b = (174, 116, 44, 74) if bullet_chart else (68, 28, 44, 62)
    # v93: generic axis names ("Category", "Period", "Value") restate what the
    # tick labels already say; drop them. Real titles (e.g. "CRAR (%)") stay.
    if _svg_axis_title_is_generic(x_axis_title):
        x_axis_title = ""
    if _svg_axis_title_is_generic(y_axis_title):
        y_axis_title = ""
    _hbar_category_title = ""
    if horizontal_bar and not bullet_chart:
        # Category labels sit left of the bars: size the margin to the longest
        # label (bounded) so names are never clipped at the SVG edge, and give
        # each row enough height instead of squeezing many rows into 320px.
        # A rotated category-axis title would collide with those labels; a
        # meaningful one ("Company") is drawn as a header above the label column.
        if y_axis_title:
            _hbar_category_title = y_axis_title
        y_axis_title = ""
        _longest = max((len(str(lbl)) for lbl in labels[:40]), default=8)
        margin_l = int(min(width * 0.40, max(68, 18 + min(_longest, _SVG_HBAR_LABEL_MAX) * 6.1)))
        height = max(height, int(margin_t + margin_b + min(len(labels), 40) * 30))
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b
    grid = []
    text = []
    marks = []

    def _fmt_axis_tick(tick: float) -> str:
        # One format per axis (decided by the axis maximum), no trailing ".0".
        try:
            top = max(abs(float(vmax)), abs(float(vmin)))
        except Exception:
            top = abs(tick)
        if abs(tick) < 1e-12:
            return "0"
        if top >= 1_000_000:
            return f"{round(tick / 1_000_000, 3):g}M"
        if top >= 10_000:
            return f"{round(tick / 1_000, 3):g}k"
        return f"{round(tick, 4):,g}"

    def add_grid():
        if y_log:
            ticks = [10 ** exp for exp in range(math.floor(math.log10(vmin)), math.ceil(math.log10(vmax)) + 1)]
            ticks = [v for v in ticks if vmin <= v <= vmax]
            if len(ticks) < 2:
                ticks = [vmin, vmax]
            log_min_local = math.log10(vmin)
            log_max_local = math.log10(vmax)
            log_span_local = log_max_local - log_min_local or 1.0
            for tick in ticks:
                y = margin_t + (log_max_local - math.log10(tick)) / log_span_local * plot_h
                grid.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{width-margin_r}" y2="{y:.1f}" stroke="{_SVG_GRID}" stroke-width="1"/>')
                text.append(f'<text x="{margin_l-8}" y="{y+4:.1f}" text-anchor="end" font-size="10.5" fill="{_SVG_AXIS_INK}">{_svg_escape(_fmt_axis_tick(tick))}</text>')
            return
        _n = _nice_tick_count
        for i in range(_n + 1):
            y = margin_t + plot_h * i / _n
            grid.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{width-margin_r}" y2="{y:.1f}" stroke="{_SVG_GRID}" stroke-width="1"/>')
            tick = vmax - (span * i / _n)
            val = _fmt_axis_tick(tick)
            text.append(f'<text x="{margin_l-8}" y="{y+4:.1f}" text-anchor="end" font-size="10.5" fill="{_SVG_AXIS_INK}">{_svg_escape(val)}</text>')

    def add_horizontal_grid():
        if x_log:
            ticks = [10 ** exp for exp in range(math.floor(math.log10(vmin)), math.ceil(math.log10(vmax)) + 1)]
            ticks = [v for v in ticks if vmin <= v <= vmax]
            if len(ticks) < 2:
                ticks = [vmin, vmax]
            log_min_local = math.log10(vmin)
            log_max_local = math.log10(vmax)
            log_span_local = log_max_local - log_min_local or 1.0
            for tick in ticks:
                x = margin_l + (math.log10(tick) - log_min_local) / log_span_local * plot_w
                grid.append(f'<line x1="{x:.1f}" y1="{margin_t}" x2="{x:.1f}" y2="{height-margin_b}" stroke="{_SVG_GRID}" stroke-width="1"/>')
                text.append(f'<text x="{x:.1f}" y="{height-margin_b+15}" text-anchor="middle" font-size="10.5" fill="{_SVG_AXIS_INK}">{_svg_escape(_fmt_axis_tick(tick))}</text>')
            return
        _n = _nice_tick_count
        for i in range(_n + 1):
            x = margin_l + plot_w * i / _n
            grid.append(f'<line x1="{x:.1f}" y1="{margin_t}" x2="{x:.1f}" y2="{height-margin_b}" stroke="{_SVG_GRID}" stroke-width="1"/>')
            tick = vmin + span * i / _n
            val = _fmt_axis_tick(tick)
            text.append(f'<text x="{x:.1f}" y="{height-margin_b+15}" text-anchor="middle" font-size="10.5" fill="{_SVG_AXIS_INK}">{_svg_escape(val)}</text>')

    all_values = []
    for ds in datasets:
        vals = ds.get("data") or []
        for v in vals:
            if isinstance(v, dict):
                if "y" in v: v = v.get("y")
                elif "value" in v: v = v.get("value")
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                for endpoint in v[:2]:
                    try: all_values.append(float(endpoint))
                    except Exception: pass
            elif isinstance(v, (int, float)):
                all_values.append(float(v))
            else:
                try: all_values.append(float(v))
                except Exception: pass
    vmax = max(all_values) if all_values else 1.0
    vmin = min(all_values) if all_values else 0.0
    _nice_tick_count = 5
    active_log = x_log if horizontal_bar else y_log
    if active_log:
        positive_values = [value for value in all_values if math.isfinite(value) and value > 0]
        if positive_values:
            actual_min, actual_max = min(positive_values), max(positive_values)
            # Use visible powers-of-ten at the axis edges so every positive bar
            # remains visible without pretending a logarithmic axis starts at 0.
            vmin = 10 ** math.floor(math.log10(actual_min))
            vmax = 10 ** math.ceil(math.log10(actual_max))
            if vmax <= vmin:
                vmax = vmin * 10
        else:
            # Invalid log-axis data should not create misleading geometry.
            return '<div class="gg-pdf-chart-fallback" style="border:1px solid #d7dee4;padding:12px;color:#52616B;font-size:10pt;">Chart omitted: logarithmic axis requires positive numeric values.</div>'
    else:
        if vmax == vmin:
            pad = abs(vmax) * 0.1 or 1.0
            vmax += pad; vmin -= pad
        if vmin > 0: vmin = 0
        # "Nice" axis: steps of 1, 2, 2.5 or 5 x 10^n so ticks read 0, 5k,
        # 10k … instead of 4.158, 8.316, 12.4745k.
        _raw_span = (vmax - vmin) or 1.0
        _magnitude = 10 ** math.floor(math.log10(_raw_span / 5))
        _fraction = _raw_span / 5 / _magnitude
        _step = next(m for m in (1, 2, 2.5, 5, 10) if _fraction <= m) * _magnitude
        vmax = math.ceil(vmax / _step - 1e-9) * _step
        vmin = math.floor(vmin / _step + 1e-9) * _step
        _nice_tick_count = max(1, min(8, int(round((vmax - vmin) / _step))))
    span = vmax - vmin or 1.0
    if active_log:
        log_min = math.log10(vmin)
        log_max = math.log10(vmax)
        log_span = log_max - log_min or 1.0
    if ctype not in ("doughnut", "pie"):
        add_horizontal_grid() if horizontal_bar else add_grid()

    def y_of(v):
        value = float(v)
        if y_log:
            value = max(value, vmin)
            return margin_t + (log_max - math.log10(value)) / log_span * plot_h
        return margin_t + (vmax - value) / span * plot_h

    def x_of(v):
        value = float(v)
        if x_log:
            value = max(value, vmin)
            return margin_l + (math.log10(value) - log_min) / log_span * plot_w
        return margin_l + (value - vmin) / span * plot_w

    colors = chart_palette
    legend_x = margin_l
    if show_legend and ctype not in ("doughnut", "pie"):
        for ds_i, ds in enumerate(datasets[:8]):
            name = _svg_escape(ds.get("label") or ds.get("name") or f"Series {ds_i + 1}")
            lx = legend_x + ds_i * 150
            if lx + 130 <= width - margin_r:
                marks.append(f'<rect x="{lx}" y="12" width="10" height="10" rx="2" fill="{colors[ds_i%len(colors)]}"/>')
                text.append(f'<text x="{lx+15}" y="21" font-size="10" fill="{_SVG_LABEL_INK}">{name}</text>')
    if ctype not in ("doughnut", "pie") and not horizontal_bar:
        _slot_chars = max(4, int((plot_w / max(1, len(labels))) / 5.6))
        for i, lbl in enumerate(labels[:40]):
            x = margin_l + (i + 0.5) * plot_w / max(1, len(labels))
            _lines = _svg_wrap_label(lbl, _slot_chars, max_lines=2)
            _y0 = height - 18 - (len(_lines) - 1) * 11
            for _li, _line in enumerate(_lines):
                text.append(f'<text x="{x:.1f}" y="{_y0 + _li * 11:.1f}" text-anchor="middle" font-size="10" fill="{_SVG_LABEL_INK}">{_svg_escape(_line)}</text>')
    elif horizontal_bar:
        _label_chars = max(6, int((margin_l - 16) / 6.1))
        for i, lbl in enumerate(labels[:40]):
            y = margin_t + (i + 0.5) * plot_h / max(1, len(labels))
            _shown = _svg_wrap_label(lbl, _label_chars, max_lines=1)[0]
            text.append(f'<text x="{margin_l-9}" y="{y+3.5:.1f}" text-anchor="end" font-size="10" fill="{_SVG_LABEL_INK}">{_svg_escape(_shown)}</text>')
        if _hbar_category_title:
            text.append(f'<text x="{margin_l-9}" y="{margin_t-12}" text-anchor="end" font-size="10.5" font-weight="600" fill="{_SVG_TITLE_INK}">{_svg_escape(_hbar_category_title)}</text>')
    if bullet_chart:
        # With indexAxis=y, the numeric scale is horizontal. The LLM's axis
        # metadata commonly names the category axis as xLabel and the numeric
        # scale as yLabel, so map the latter to the visible value axis.
        x_axis_title = y_axis_title or x_axis_title
        y_axis_title = ""
    if x_axis_title and ctype not in ("doughnut", "pie"):
        axis_title_y = height - 8 if horizontal_bar else height - 2
        text.append(f'<text x="{margin_l + plot_w/2:.1f}" y="{axis_title_y}" text-anchor="middle" font-size="12" font-weight="700" fill="{_SVG_TITLE_INK}">{_svg_escape(x_axis_title)}</text>')
    if y_axis_title and ctype not in ("doughnut", "pie"):
        cy = margin_t + plot_h/2
        text.append(f'<text x="14" y="{cy:.1f}" transform="rotate(-90 14 {cy:.1f})" text-anchor="middle" font-size="12" font-weight="700" fill="{_SVG_TITLE_INK}">{_svg_escape(y_axis_title)}</text>')

    if ctype in ("doughnut", "pie"):
        # Pie/donut charts must remain self-explanatory in static PDF output.
        # Some providers emit dataset points as {label,value} objects while
        # others emit a separate labels[] array. Normalize both shapes so the
        # legend can never disappear simply because labels were encoded with
        # the data points.
        ds = datasets[0] if datasets else {}
        raw_values = ds.get("data", [])
        if not labels:
            labels = [str(v.get("label") or v.get("name") or f"Item {i+1}")
                      if isinstance(v, dict) else f"Item {i+1}"
                      for i, v in enumerate(raw_values)]
        # Ring on the left, legend on the right: a bottom legend was clipped by
        # fixed-height chart boxes, leaving an unlabelled ring in the PDF.
        cx, cy, r = width * 0.30, height / 2, min(height * 0.40, width * 0.2)
        vals = []
        for v in raw_values:
            if isinstance(v, dict):
                v = v.get("value", v.get("y", 0))
            try: vals.append(max(0.0, float(v)))
            except Exception: vals.append(0.0)
        total = sum(vals) or 1.0
        import math as _m
        angle = -_m.pi/2
        for i, val in enumerate(vals):
            a2 = angle + 2*_m.pi*(val/total)
            x1,y1 = cx+r*_m.cos(angle), cy+r*_m.sin(angle)
            x2,y2 = cx+r*_m.cos(a2), cy+r*_m.sin(a2)
            large = 1 if a2-angle > _m.pi else 0
            marks.append(f'<path d="M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} A {r:.1f} {r:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="{colors[i%len(colors)]}"/>')
            angle = a2
        marks.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r*0.48:.1f}" fill="#ffffff"/>')
        _rows = min(len(vals), 12)
        legend_y = cy - (_rows - 1) * 11 + 4
        for i, val in enumerate(vals[:12]):
            lbl = labels[i] if i < len(labels) else f"Item {i+1}"
            pct = (val/total*100) if total else 0
            lx = int(width * 0.56)
            ly = legend_y + i * 22
            marks.append(f'<rect x="{lx}" y="{ly-10}" width="11" height="11" rx="3" fill="{colors[i%len(colors)]}"/>')
            legend_text = lbl if re.search(r"\d%\s*$", str(lbl)) else f"{lbl} {pct:.1f}%"
            text.append(f'<text x="{lx+16}" y="{ly}" font-size="13" fill="{_SVG_TITLE_INK}">{_svg_escape(legend_text)}</text>')
    elif ctype == "scatter":
        # Scatter configs contain datasets of {x,y}.
        pts = []
        xs=[]; ys=[]
        for ds in datasets:
            for p in ds.get("data") or []:
                if isinstance(p, dict):
                    try: xs.append(float(p.get("x",0))); ys.append(float(p.get("y",0))); pts.append((float(p.get("x",0)),float(p.get("y",0))))
                    except Exception: pass
        if xs and ys:
            xmin,xmax=min(xs),max(xs); ymin,ymax=min(ys),max(ys)
            if xmin==xmax: xmin-=1; xmax+=1
            if ymin==ymax: ymin-=1; ymax+=1
            for x,y in pts[:500]:
                px=margin_l+(x-xmin)/(xmax-xmin)*plot_w; py=margin_t+(ymax-y)/(ymax-ymin)*plot_h
                marks.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4" fill="{colors[0]}" opacity="0.88"/>')
    elif ctype == "radar":
        import math as _m
        count = max(3, len(labels))
        cx, cy = margin_l + plot_w/2, margin_t + plot_h/2
        rr = min(plot_w, plot_h) * 0.36
        for ring in range(1, 5):
            rp = rr * ring / 4
            pts = []
            for i in range(count):
                a = -_m.pi/2 + i*2*_m.pi/count
                pts.append((cx+rp*_m.cos(a), cy+rp*_m.sin(a)))
            d = ' '.join(("M" if j==0 else "L")+f" {x:.1f} {y:.1f}" for j,(x,y) in enumerate(pts)) + ' Z'
            marks.append(f'<path d="{d}" fill="none" stroke="{_SVG_GRID}" stroke-width="1"/>')
        for i, lbl in enumerate(labels[:count]):
            a = -_m.pi/2 + i*2*_m.pi/count
            tx, ty = cx+(rr+18)*_m.cos(a), cy+(rr+18)*_m.sin(a)
            text.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" font-size="8.5" fill="{_SVG_AXIS_INK}">{_svg_escape(lbl)}</text>')
        for ds_i, ds in enumerate(datasets[:6]):
            vals=[]
            raw=ds.get("data") or []
            max_ref=max([float(v.get("y",v.get("value",0))) if isinstance(v,dict) else float(v or 0) for v in raw] or [1]) or 1
            for i in range(count):
                v=raw[i] if i < len(raw) else 0
                try: val=float(v.get("y",v.get("value",0))) if isinstance(v,dict) else float(v)
                except Exception: val=0
                rp=rr*val/max_ref
                a=-_m.pi/2+i*2*_m.pi/count
                vals.append((cx+rp*_m.cos(a), cy+rp*_m.sin(a)))
            d=' '.join(("M" if j==0 else "L")+f" {x:.1f} {y:.1f}" for j,(x,y) in enumerate(vals))+' Z'
            col=colors[ds_i%len(colors)]
            marks.append(f'<path d="{d}" fill="{col}" fill-opacity="0.12" stroke="{col}" stroke-width="2"/>')
    elif ctype == "line":
        for ds_i, ds in enumerate(datasets[:8]):
            segments = []
            current = []
            for i, raw_value in enumerate(ds.get("data") or []):
                try:
                    value = raw_value.get("y", raw_value.get("value")) if isinstance(raw_value, dict) else raw_value
                    if value is None:
                        raise ValueError("missing period value")
                    val = float(value)
                    if not math.isfinite(val):
                        raise ValueError("non-finite period value")
                    x = margin_l + (i + 0.5) * plot_w / max(1, len(labels))
                    y = y_of(val)
                    current.append((x, y, val))
                except Exception:
                    if current:
                        segments.append(current)
                        current = []
            if current:
                segments.append(current)
            for pts in segments:
                if len(pts) >= 2:
                    d = " ".join(("M" if j == 0 else "L") + f" {x:.1f} {y:.1f}" for j, (x, y, _) in enumerate(pts))
                    marks.append(f'<path d="{d}" fill="none" stroke="{colors[ds_i%len(colors)]}" stroke-width="2.4"/>')
                for pi, (x, y, val) in enumerate(pts):
                    marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{colors[ds_i%len(colors)]}"/>')
                    if len(pts) <= 12 or pi in (0, len(pts)-1):
                        text.append(f'<text x="{x:.1f}" y="{y-7:.1f}" text-anchor="middle" font-size="8.5" fill="{_SVG_VALUE_INK}">{val:,.1f}</text>')
    elif ctype == "bubble":
        all_pts=[]
        for ds in datasets:
            all_pts.extend([p for p in (ds.get("data") or []) if isinstance(p,dict)])
        xs=[float(p.get("x",0)) for p in all_pts if isinstance(p.get("x",0),(int,float))]
        ys=[float(p.get("y",0)) for p in all_pts if isinstance(p.get("y",0),(int,float))]
        if xs and ys:
            xmin,xmax=min(xs),max(xs); ymin,ymax=min(ys),max(ys)
            if xmin==xmax: xmin-=1; xmax+=1
            if ymin==ymax: ymin-=1; ymax+=1
            for ds_i, ds in enumerate(datasets[:4]):
                col=colors[ds_i%len(colors)]
                for pnt in ds.get("data") or []:
                    try:
                        xv,yv=float(pnt.get("x",0)),float(pnt.get("y",0)); rv=float(pnt.get("r",6))
                    except Exception: continue
                    px=margin_l+(xv-xmin)/(xmax-xmin)*plot_w; py=margin_t+(ymax-yv)/(ymax-ymin)*plot_h
                    marks.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{max(3,min(20,rv)):.1f}" fill="{col}" fill-opacity="0.35" stroke="{col}" stroke-width="1.5"/>')
                    if pnt.get("label"):
                        text.append(f'<text x="{px+7:.1f}" y="{py-7:.1f}" font-size="8.2" fill="{_SVG_VALUE_INK}">{_svg_escape(pnt.get("label"))}</text>')
    elif bullet_chart:
        scale_ds = next((ds for ds in datasets if str(ds.get("label") or "").strip().lower() in {"scale", "track"}), datasets[0])
        actual_ds = next((ds for ds in datasets if "actual" in str(ds.get("label") or "").lower()), datasets[1])
        target_ds = next((ds for ds in datasets if "target" in str(ds.get("label") or "").lower()), datasets[-1])

        def _range(ds, index):
            values = ds.get("data") or []
            raw = values[index] if index < len(values) else 0
            if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                try: return float(raw[0]), float(raw[1])
                except Exception: return 0.0, 0.0
            try: return 0.0, float(raw.get("y", raw.get("value", 0)) if isinstance(raw, dict) else raw)
            except Exception: return 0.0, 0.0

        row_h = plot_h / max(1, len(labels))
        target_colors = actual_ds.get("backgroundColor")
        for i, label in enumerate(labels):
            _, track_end = _range(scale_ds, i)
            _, value = _range(actual_ds, i)
            target_start, target_end = _range(target_ds, i)
            target = (target_start + target_end) / 2
            cy = margin_t + (i + 0.5) * row_h
            bar_h = min(22.0, row_h * 0.48)
            track_right = x_of(track_end if track_end > 0 else vmax)
            marks.append(f'<rect x="{x_of(0):.1f}" y="{cy-bar_h/2:.1f}" width="{max(1,track_right-x_of(0)):.1f}" height="{bar_h:.1f}" rx="3" fill="#e4ebe7"/>')
            color = target_colors[i] if isinstance(target_colors, list) and i < len(target_colors) else (target_colors if isinstance(target_colors, str) else colors[0])
            value_x = x_of(value)
            marks.append(f'<rect x="{x_of(0):.1f}" y="{cy-bar_h*0.34:.1f}" width="{max(1,value_x-x_of(0)):.1f}" height="{bar_h*0.68:.1f}" rx="2" fill="{_svg_escape(color)}"/>')
            target_x = x_of(target)
            marks.append(f'<line x1="{target_x:.1f}" y1="{cy-bar_h*0.68:.1f}" x2="{target_x:.1f}" y2="{cy+bar_h*0.68:.1f}" stroke="#243746" stroke-width="2.2"/>')
            val_label = f"{value:,.1f}".rstrip("0").rstrip(".")
            target_label = f"{target:,.1f}".rstrip("0").rstrip(".")
            unit = str(config.get("unit") or (config.get("data") or {}).get("unit") or "")
            text.append(f'<text x="{width-margin_r+7}" y="{cy+3:.1f}" font-size="8.5" fill="{_SVG_TITLE_INK}">{val_label} / {target_label} {_svg_escape(unit)}</text>')
    elif horizontal_bar:
        n = max(1, len(labels)); series_count = max(1, len(datasets)); row_h = plot_h / n
        for ds_i, ds in enumerate(datasets[:8]):
            vals = ds.get("data") or []
            bar_h = row_h * 0.68 / series_count
            for i, raw in enumerate(vals[:len(labels)]):
                if isinstance(raw, (list, tuple)) and len(raw) >= 2:
                    try: a, b = float(raw[0]), float(raw[1])
                    except Exception: continue
                else:
                    try: a, b = 0.0, float(raw.get("y", raw.get("value", 0)) if isinstance(raw, dict) else raw)
                    except Exception: continue
                cy = margin_t + (i + 0.5) * row_h
                y = cy - (series_count * bar_h) / 2 + ds_i * bar_h
                if x_log:
                    a = vmin if a <= 0 else a
                    if b <= 0:
                        continue
                xa, xb = x_of(a), x_of(b)
                marks.append(f'<rect x="{min(xa,xb):.1f}" y="{y:.1f}" width="{max(1,abs(xb-xa)):.1f}" height="{max(2,bar_h-2):.1f}" rx="2" fill="{colors[ds_i%len(colors)]}"/>')
    else:  # vertical bar, including floating/bar-like fallback
        n = max(1, len(labels)); series_count=max(1, len(datasets)); group_w=plot_w/n; bar_w=max(4, group_w*0.72/series_count)
        for ds_i, ds in enumerate(datasets[:8]):
            vals=ds.get("data") or []
            for i,v in enumerate(vals[:len(labels)]):
                if isinstance(v, list) and len(v)>=2:
                    # Floating-range bars can use two explicit endpoints on a
                    # linear axis. On log axes a zero/negative implicit origin
                    # is invalid, so omit ranges rather than draw misleading bars.
                    if x_log or y_log:
                        continue
                    try:
                        a, b = float(v[0]), float(v[1])
                    except Exception:
                        continue
                    y1, y2 = y_of(a), y_of(b)
                    top, bh = min(y1, y2), abs(y2 - y1)
                    raw_val = b
                else:
                    try:
                        raw_value = v.get("y", v.get("value")) if isinstance(v, dict) else v
                        if raw_value is None:
                            continue
                        val = float(raw_value)
                    except Exception:
                        continue
                    if not math.isfinite(val) or (y_log and val <= 0):
                        continue
                    baseline = y_of(vmin) if y_log else y_of(0)
                    yv = y_of(val)
                    top, bh = min(baseline, yv), abs(baseline - yv)
                    raw_val = val
                x=margin_l+i*group_w+(group_w-series_count*bar_w)/2+ds_i*bar_w
                bw=max(2,bar_w-2); bh2=max(1,bh)
                marks.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw:.1f}" height="{bh2:.1f}" rx="2" fill="{colors[ds_i%len(colors)]}"/>')
                if len(labels) <= 14 and isinstance(raw_val, (int, float)):
                    txt = f"{raw_val:,.1f}".rstrip('0').rstrip('.')
                    text.append(f'<text x="{x+bw/2:.1f}" y="{max(margin_t+10, top-4):.1f}" text-anchor="middle" font-size="8.2" fill="{_SVG_VALUE_INK}">{txt}</text>')

    if overlay_lines:
        line_colors = ["#B4533C", "#2F6F95", "#7A5C99"]
        for li, ds in enumerate(overlay_lines):
            vals = []
            for v in ds.get("data") or []:
                if isinstance(v, dict):
                    v = v.get("y", v.get("value"))
                try:
                    vals.append(float(v))
                except Exception:
                    vals.append(None)
            finite = [v for v in vals if v is not None]
            if not finite:
                continue
            own_axis = ds.get("yAxisID") == "y1"
            lo, hi = (min(finite + [0.0]), max(finite)) if own_axis else (vmin, vmax)
            if hi == lo:
                hi = lo + (abs(lo) * 0.1 or 1.0)
            pts = []
            for i, v in enumerate(vals):
                if v is None:
                    continue
                x = margin_l + (i + 0.5) * plot_w / max(1, len(labels) or len(vals))
                y = margin_t + (hi - v) / (hi - lo) * plot_h
                pts.append((x, y, v))
            color = line_colors[li % len(line_colors)]
            if len(pts) > 1:
                marks.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
            for x, y, v in pts:
                marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
                text.append(f'<text x="{x:.1f}" y="{y-8:.1f}" text-anchor="middle" font-size="8.6" fill="{color}">{_svg_escape(f"{v:,.2f}".rstrip("0").rstrip("."))}</text>')
            if own_axis:
                for i in range(6):
                    tick = hi - (hi - lo) * i / 5
                    y = margin_t + plot_h * i / 5
                    text.append(f'<text x="{width - margin_r + 6}" y="{y+4:.1f}" font-size="9" fill="{color}">{_svg_escape(f"{tick:,.1f}")}</text>')
            name = _svg_escape(ds.get("label") or "Line")
            lx = margin_l + (len(datasets) + li) * 150
            if lx + 130 <= width:
                marks.append(f'<line x1="{lx}" y1="17" x2="{lx+12}" y2="17" stroke="{color}" stroke-width="2.5"/>')
                text.append(f'<text x="{lx+16}" y="21" font-size="10" fill="{_SVG_LABEL_INK}">{name}</text>')

    title = (config.get("options") or {}).get("plugins", {}).get("title", {}).get("text") or ""
    return (
        f'<div class="gg-pdf-chart-fallback" style="break-inside:avoid;max-width:100%;">'
        f'{f"<div style=\"font-weight:600;font-size:11pt;margin:0 0 5px;color:{_SVG_VALUE_INK};\">{_svg_escape(title)}</div>" if title else ""}'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="chart" font-family="{_svg_escape(_SVG_FONT)}">'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
        + "".join(grid + marks + text) +
        f'</svg></div>'
    )


def _replace_chart_runtime_with_svg(html_doc: str, theme: dict | None = None) -> str:
    """Replace Chart.js canvas/script pairs with static SVGs for PDF output."""
    import json as _json
    import re as _re

    script_re = _re.compile(r'<script>(.*?)</script>', _re.DOTALL)
    push_re = _re.compile(r'window\.__ggCharts\.push\(\{\s*id:\s*"([^"]+)"\s*,\s*config:\s*', _re.DOTALL)
    decoder = _json.JSONDecoder()
    replacements = {}

    for sm in script_re.finditer(html_doc):
        body = sm.group(1)
        pm = push_re.search(body)
        if not pm:
            continue
        try:
            config, _ = decoder.raw_decode(body[pm.end():])
            replacements[pm.group(1)] = _svg_chart_from_config(config, theme=theme)
        except Exception as exc:
            log.warning("PDF: failed to parse chart runtime block %s: %s", pm.group(1), exc)
            replacements[pm.group(1)] = '<div class="gg-pdf-chart-fallback">Chart data could not be rendered.</div>'

    for chart_id, replacement in replacements.items():
        # Mark the enclosing box as static so print CSS lets it grow to the SVG.
        box_pattern = rf'(<div class="gg-chart-canvas-box)([^"]*)("[^>]*>\s*)<canvas\s+id="{_re.escape(chart_id)}"\s*>\s*</canvas>'
        new_doc, n = _re.subn(box_pattern, lambda _m, value=replacement: f'{_m.group(1)}{_m.group(2)} gg-chart-canvas-box--static{_m.group(3)}{value}', html_doc, count=1)
        if n:
            html_doc = new_doc
            continue
        pattern = rf'<canvas\s+id="{_re.escape(chart_id)}"\s*>\s*</canvas>'
        # lambda replacement is intentional: SVG markup can contain backslashes
        # or replacement-like sequences that re.sub would otherwise interpret.
        html_doc = _re.sub(pattern, lambda _m, value=replacement: value, html_doc, count=1)

    # Remove only inline chart registration scripts. The main application JS
    # is left intact; the chart registrations are no longer needed once SVGs
    # have been inserted.
    html_doc = _re.sub(
        r'<script>\s*(?:window\.__ggCharts\s*=\s*window\.__ggCharts\s*\|\|\s*\[\];\s*)?window\.__ggCharts\.push\(.*?\);\s*</script>',
        '',
        html_doc,
        flags=_re.DOTALL,
    )
    html_doc = _re.sub(
        r'<script\s+src="https://cdnjs\.cloudflare\.com/ajax/libs/Chart\.js/4\.4\.1/chart\.umd\.min\.js"></script>\s*',
        '',
        html_doc,
        count=1,
    )
    return html_doc

def _strip_non_printing_runtime(html_doc: str) -> str:
    """Prepare browser-oriented report HTML for print/PDF engines.

    The interactive HTML renderer intentionally uses a few modern screen CSS
    features that WeasyPrint does not implement (clamp(), 100vw, sticky,
    overflow-x, grid place-items and screen-only media queries). Normalize
    those constructs only in the PDF copy so the web report remains unchanged.
    """
    # Remove browser media blocks entirely from the PDF copy. The dedicated
    # print stylesheet below becomes the only print-specific CSS.
    def _strip_media_blocks(doc: str) -> str:
        pos = 0
        while True:
            low = doc.lower()
            candidates = [low.find(token, pos) for token in ('@media screen', '@media (max-width', '@media print') if low.find(token, pos) >= 0]
            if not candidates:
                return doc
            idx = min(candidates)
            brace = doc.find('{', idx)
            if brace < 0:
                return doc
            depth = 0
            end_idx = None
            for j in range(brace, len(doc)):
                if doc[j] == '{':
                    depth += 1
                elif doc[j] == '}':
                    depth -= 1
                    if depth == 0:
                        end_idx = j + 1
                        break
            if end_idx is None:
                return doc
            doc = doc[:idx] + doc[end_idx:]
            pos = idx
    html_doc = _strip_media_blocks(html_doc)

    # Make the generated HTML CSS print-safe before WeasyPrint parses it.
    html_doc = re.sub(r'font-size:\s*clamp\([^;{}]+\)', 'font-size: 42px', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'width:\s*100vw', 'width: 100%', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'margin-left:\s*calc\(50%\s*-\s*50vw\)', 'margin-left: 0', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'position:\s*sticky', 'position: static', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'overflow-x:\s*auto', 'overflow: visible', html_doc, flags=re.IGNORECASE)
    # Remove the browser multicolumn shorthand from the generic stylesheet.
    # The PDF stylesheet below uses explicit CSS Grid for compact two-column
    # sections because WeasyPrint's multicolumn engine can fail on mixed
    # descendants during pagination.
    html_doc = re.sub(r'columns:\s*2\s+320px\s*;', '', html_doc, flags=re.IGNORECASE)
    # WeasyPrint 68 intentionally supports a narrower CSS subset than the
    # browser renderer. Drop decorative/modern properties from the PDF copy
    # so they cannot produce warning noise or interact with layout. The PDF
    # has explicit print-safe equivalents below.
    html_doc = re.sub(r'(?im)^\s*(?:text-wrap|box-shadow|border-inline|padding-inline)\s*:[^;{}]+;?', '', html_doc)
    html_doc = re.sub(r'(?im)^\s*(?:background)\s*:\s*(?:linear-gradient|color-mix)\([^;{}]+\)\s*;?', '', html_doc)
    html_doc = re.sub(r'place-items:\s*center', 'align-items: center; justify-content: center', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'print-color-adjust:\s*exact\s*!?important?;?', '', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'-webkit-print-color-adjust:\s*exact\s*!?important?;?', '', html_doc, flags=re.IGNORECASE)
    html_doc = re.sub(r'\sloading=["\']lazy["\']', "", html_doc, flags=re.IGNORECASE)
    # HTML stat cards normally animate from 0 to their target in the browser.
    # PDF is static, so write the target value into the markup before printing.
    def _materialize_count(m):
        attrs = m.group(1)
        target = m.group(2)
        try:
            value = float(target)
            rendered = str(int(value)) if value.is_integer() else str(value)
        except Exception:
            rendered = target
        return f'<span class="gg-count"{attrs}>{rendered}</span>'

    html_doc = re.sub(
        r'<span class="gg-count"([^>]*)data-count-target="([^"]+)"[^>]*>0</span>',
        _materialize_count,
        html_doc,
    )
    # No browser runtime is needed in a static PDF after charts have become SVG.
    html_doc = re.sub(r'<script>.*?</script>', '', html_doc, flags=re.DOTALL)
    title_match = re.search(
        r'<h1[^>]*class=["\'][^"\']*\bgg-title\b[^"\']*["\'][^>]*>(.*?)</h1>',
        html_doc,
        flags=re.IGNORECASE | re.DOTALL,
    )
    title_html = title_match.group(1) if title_match else ""
    short_title = html.unescape(re.sub(r"<[^>]+>", " ", title_html))
    short_title = re.sub(r"\s+", " ", short_title).strip()
    for separator in (" — ", " – ", " | ", ":"):
        if separator in short_title:
            candidate = short_title.split(separator, 1)[0].strip()
            if len(candidate) >= 18:
                short_title = candidate
                break
    if len(short_title) > 44:
        short_title = " ".join(short_title.split()[:6])
    short_title = short_title.replace("\\", "\\\\").replace('"', '\\"')
    tier_match = re.search(r'<body[^>]*data-report-tier=["\']([^"\']+)', html_doc, flags=re.IGNORECASE)
    is_in_depth = bool(tier_match and tier_match.group(1).strip().lower() == "in_depth")
    print_css = """
<style id="gg-pdf-print-overrides">
@page {
  size: A4;
  margin: 15mm 12mm 18mm 12mm;
  @top-left { content: "GROWTH GRADUAL · RESEARCH INTELLIGENCE"; font-family: system-ui, sans-serif; font-size: 7.5pt; font-weight: 700; letter-spacing: .08em; color: #7a8496; }
  @top-right { content: none; }
  @bottom-left { content: "Growth Gradual | Research Intelligence | {short_title}"; font-family: Arial, sans-serif; font-size: 7.2pt; font-weight: 600; color: #354a54; }
  @bottom-right { content: counter(page); font-family: Arial, sans-serif; font-size: 8pt; font-weight: 700; color: #17384f; }
}
@page :first { @top-left { content: none; } }
/* Hard A4 content-box guard: browser layouts may be fluid/wide, but the PDF
   renderer must never allow a child block, SVG, table or section to extend
   past the printable page width. */
*, *::before, *::after { box-sizing: border-box !important; min-width: 0; }
html, body { width: 100%; max-width: 100%; overflow: visible; }
main, .gg-cover, .gg-report-sections, .gg-section, .gg-section-body,
.gg-block, .gg-chart-wrap, .gg-table-wrap, .gg-chart-canvas-box,
.gg-chart-inline-svg, .gg-figure, .gg-sources {
  width: 100% !important; max-width: 100% !important; min-width: 0 !important;
  box-sizing: border-box !important;
}
main, .gg-cover, .gg-section, .gg-section--full_bleed { margin-left: 0 !important; margin-right: 0 !important; }
body.gg-tier--in_depth main { padding-top: 0 !important; padding-bottom: 0 !important; }
.gg-section--full_bleed { width: 100% !important; padding-left: 0 !important; padding-right: 0 !important; }
img, svg, canvas, table { max-width: 100% !important; box-sizing: border-box !important; }
p, li, td, th, .gg-chart-title, .gg-chart-note, .gg-source-title, .gg-source-link {
  max-width: 100%; overflow-wrap: anywhere; word-break: normal;
}
* { animation: none !important; transition: none !important; caret-color: transparent !important; }
[data-reveal] { opacity: 1 !important; transform: none !important; visibility: visible !important; }
body { font-size: 10.5pt; line-height: 1.48; }
p, .gg-list { font-size: 10.5pt; line-height: 1.48; }
p { width: 100%; max-width: 100%; text-align: justify; text-justify: inter-word; hyphens: auto; orphans: 3; widows: 3; }
h1, h2, h3, h4 { break-after: avoid-page; page-break-after: avoid; }
.gg-section { break-before: auto; page-break-before: auto; break-inside: auto; page-break-inside: auto; margin-bottom: 18px; }
body.gg-tier--in_depth .gg-report-sections { display: block !important; gap: 0 !important; }
body.gg-tier--in_depth .gg-report-sections > .gg-section { display: block !important; margin-bottom: 14px; }
.gg-section-heading { break-inside: avoid; break-after: avoid-page; page-break-after: avoid; margin-bottom: 8px; }
.gg-section-heading h2 { font-size: 18pt; line-height: 1.15; }
.gg-section-h3 { font-size: 12pt; margin: 12px 0 6px; }
.gg-subsection-h4 { font-size: 10.5pt; margin: 10px 0 5px; }
.gg-section-body > p + p { margin-top: 6px; }
.gg-block, .gg-callout, .gg-risk, .gg-metric { break-inside: auto; page-break-inside: auto; }
.gg-chart-wrap, .gg-table-wrap, .gg-figure, .gg-pdf-chart-fallback { break-inside: avoid; page-break-inside: avoid; }
.gg-source-note { break-inside: avoid; page-break-inside: avoid; font-size: 8.5pt; line-height: 1.3; color: #3d5059; }
body.gg-tier--in_depth .gg-source-cite, body.gg-tier--in_depth .gg-source-link { color: #245f49 !important; text-decoration: underline; }
.gg-title-band { break-inside: avoid; break-after: avoid-page; margin-top: 2mm; margin-bottom: 7mm; padding-top: 2mm; }
.gg-title-band .gg-title { font-family: Georgia, 'Times New Roman', serif; font-size: 25pt; line-height: 1.1; }
.gg-chart-canvas-box { width: 100% !important; max-width: 100% !important; min-height: 260px; overflow: hidden; }
.gg-chart-inline-svg { width: 100% !important; max-width: 100% !important; overflow: hidden; }
.gg-chart-inline-svg svg { width: 100% !important; max-width: 100% !important; height: auto !important; display: block; }
.gg-figure img { break-inside: avoid; max-height: 420px; }
.gg-chart-notes { margin-top: 8px; gap: 4px; }
.gg-chart-note { padding: 4px 7px; font-size: 8.2pt; line-height: 1.3; }
.gg-chart-note-label { min-width: 58px; font-size: 7.5pt; }
.gg-sources { margin-top: 24px; padding: 14px; break-before: auto; page-break-before: auto; }
.gg-sources-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
.gg-source-card { padding: 6px 7px; grid-template-columns: 22px minmax(0, 1fr); gap: 7px; }
body.gg-tier--in_depth .gg-sources { margin-top: 16px; padding: 12px; break-before: auto; page-break-before: auto; break-inside: auto; page-break-inside: auto; }
body.gg-tier--in_depth .gg-source-card { padding: 7px 8px; grid-template-columns: 24px minmax(0, 1fr); gap: 7px; break-inside: avoid; page-break-inside: avoid; }
.gg-source-title { font-size: 8.3pt; }
.gg-source-meta, .gg-source-link { font-size: 7.4pt; }
/* Preserve the existing print composition for standard reports. */
.gg-section-body--two_column {
  column-count: 1 !important;
  column-width: auto !important;
  display: block !important;
  grid-template-columns: none !important;
  gap: 0;
}
/* In-Depth prose is set in one readable print column; visuals retain their own
   full-width, break-safe presentation units. */
body.gg-tier--in_depth .gg-section-body--two_column {
  column-count: 1 !important;
  column-width: auto !important;
  display: block !important;
  grid-template-columns: none !important;
  gap: 0;
}
body.gg-tier--in_depth .gg-section-body--grid {
  display: block !important;
  grid-template-columns: none !important;
  gap: 0 !important;
}
body.gg-tier--in_depth .gg-section-body--grid > * + * { margin-top: 12px; }
.gg-section-body--grid {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
body.gg-tier--in_depth .gg-sources[data-source-placement="end_of_report"] {
  break-before: auto !important;
  page-break-before: auto !important;
}
.gg-sources h2, .gg-sources-intro { break-after: avoid-page; page-break-after: avoid; }
.gg-section-body--two_column > *, .gg-section-body--grid > * {
  min-width: 0 !important; max-width: 100% !important; width: 100% !important;
}
.gg-report-nav { display: none !important; }
.gg-report-footer { display: none !important; }
.gg-report-index a::after { content: leader(".") target-counter(attr(href), page); color: #46545e; font-size: 8.5pt; }
body.gg-tier--brief .gg-section-heading h2 { font-size: 16pt; }
body.gg-tier--detailed .gg-section-heading h2 { font-size: 18pt; }
body.gg-tier--in_depth .gg-section-heading h2 { font-size: 19pt; }
body.gg-tier--brief .gg-section { margin-bottom: 14px; }
body.gg-tier--in_depth .gg-section { margin-bottom: 14px; }
.gg-footer { display: none !important; }
.gg-section[data-density="sparse"] { padding-top: 0; padding-bottom: 0; }
</style>
"""
    # Final PDF-only overrides: previous CSS layers sometimes reintroduced
    # unbreakable source appendices and split a KPI strip across pages.
    print_css = print_css.replace("</style>", """
<style>
body.gg-tier--brief .gg-dashboard-strip,
body.gg-tier--brief .gg-dashboard-strip .gg-stats-grid {
  break-inside: avoid !important; page-break-inside: avoid !important;
}
/* V7 editorial pagination: Brief is an executive document, not a newspaper.
   Keep prose sections in one readable measure so a page never opens with a
   stranded bullet list while its pull-quote or heading sits on the next page.
   Charts/tables remain atomic visual units. */
body.gg-tier--brief .gg-section-body--two_column,
body.gg-tier--brief .gg-section-body--grid {
  display: block !important;
  grid-template-columns: none !important;
  columns: 1 !important;
  column-count: 1 !important;
  column-width: auto !important;
  width: 100% !important;
}
body.gg-tier--brief .gg-section-body--two_column > *,
body.gg-tier--brief .gg-section-body--grid > * {
  width: 100% !important;
  max-width: 100% !important;
  margin-left: 0 !important;
  margin-right: 0 !important;
}
body.gg-tier--brief .gg-section-body--two_column > .gg-pullquote,
body.gg-tier--brief .gg-section-body--grid > .gg-pullquote {
  margin: 12px 0 14px !important;
}
body.gg-tier--brief .gg-section-body--two_column > .gg-figure,
body.gg-tier--brief .gg-section-body--grid > .gg-figure {
  max-width: 72% !important;
  margin: 14px 0 16px !important;
}
body.gg-tier--brief .gg-section-body--two_column > .gg-list,
body.gg-tier--brief .gg-section-body--grid > .gg-list {
  margin: 8px 0 12px !important;
  padding-left: 18px !important;
}
body.gg-tier--brief .gg-section-body--two_column > .gg-list li,
body.gg-tier--brief .gg-section-body--grid > .gg-list li {
  margin: 0 0 7px !important;
}
body.gg-tier--brief .gg-section {
  break-inside: auto !important;
  page-break-inside: auto !important;
  margin-bottom: 12px !important;
}
body.gg-tier--brief .gg-section + .gg-section {
  margin-top: 8px !important;
}
body.gg-tier--brief .gg-chart-wrap,
body.gg-tier--brief .gg-table-wrap,
body.gg-tier--brief .gg-figure,
body.gg-tier--brief .gg-callout,
body.gg-tier--brief .gg-pullquote {
  break-inside: avoid !important;
  page-break-inside: avoid !important;
}
body.gg-tier--brief .gg-chart-wrap {
  margin: 12px 0 14px !important;
  padding: 11px 13px !important;
}
body.gg-tier--brief .gg-chart-canvas-box {
  min-height: 215px !important;
  height: 215px !important;
}
body.gg-tier--brief .gg-chart-inline-svg {
  min-height: 185px !important;
}
body.gg-tier--brief .gg-table-wrap {
  margin: 12px 0 14px !important;
}
body.gg-tier--brief .gg-source-card {
  font-size: 8pt !important;
}
body.gg-tier--brief .gg-dashboard-strip .gg-stats-grid {
  display: flex !important; flex-wrap: wrap !important; gap: 7px !important;
}
body.gg-tier--brief .gg-dashboard-strip .gg-stat-card {
  flex: 1 1 43% !important; max-width: calc(50% - 4px) !important;
  min-height: 60px !important; break-inside: avoid !important;
}
body.gg-tier--brief .gg-stat-value {
  font-size: 16pt !important; overflow-wrap: normal !important;
  word-break: normal !important; white-space: normal !important;
}
body.gg-tier--brief .gg-cover { min-height: 0 !important; padding: 7px 0 12px !important; }
body.gg-tier--brief .gg-title { font-size: 23pt !important; }
.gg-sources { break-inside: auto !important; page-break-inside: auto !important; }
.gg-sources-grid { align-items: start !important; }
.gg-source-card { break-inside: avoid !important; page-break-inside: avoid !important; }
</style>""", 1)
    print_css = print_css.replace("{short_title}", short_title)
    if is_in_depth:
        print_css = re.sub(
            r'@top-left\s*\{[^}]*\}',
            '@top-left { content: "GROWTH GRADUAL | RESEARCH INTELLIGENCE"; font-family: Arial, sans-serif; font-size: 7.2pt; font-weight: 700; letter-spacing: .08em; color: #294553; }',
            print_css,
            count=1,
        )
    else:
        page_start = print_css.index("@page {")
        page_end = print_css.index("/* Hard A4", page_start)
        legacy_page_furniture = '''@page {
  size: A4;
  margin: 16mm 12mm 18mm 12mm;
  @top-left { content: "GROWTH GRADUAL | RESEARCH INTELLIGENCE"; font-family: system-ui, sans-serif; font-size: 7.5pt; font-weight: 700; letter-spacing: .08em; color: #7a8496; }
  @top-right { content: string(reportTitle); font-family: system-ui, sans-serif; font-size: 7.5pt; color: #9aa2b1; }
  @bottom-left { content: "Growth Gradual | Research Intelligence | {short_title}"; font-family: Arial, sans-serif; font-size: 7.2pt; font-weight: 600; color: #354a54; }
  @bottom-right { content: counter(page); font-family: Arial, sans-serif; font-size: 8pt; font-weight: 700; color: #17384f; }
}
@page :first { @top-left { content: none; } @top-right { content: none; } }
.gg-title { string-set: reportTitle content(); }
'''
        print_css = print_css[:page_start] + legacy_page_furniture + print_css[page_end:]
    # v20: make the print renderer intentionally single-column for prose grids
    # across every tier. A wide table/chart must never inherit a half-page width
    # from an adaptive screen layout. Also keep table words intact and allow long
    # tables to paginate with repeated header rows instead of shrinking off-page.
    final_print_overrides = """
<style id="gg-pdf-v20-layout-qa">
@media print {
  body.gg-tier--brief .gg-section-body--grid,
  body.gg-tier--detailed .gg-section-body--grid,
  body.gg-tier--in_depth .gg-section-body--grid {
    display: block !important; grid-template-columns: none !important;
    columns: 1 !important; column-count: 1 !important; gap: 0 !important;
    width: 100% !important; max-width: 100% !important;
  }
  .gg-section-body--grid > * {
    display: block !important; width: 100% !important; max-width: 100% !important;
    min-width: 0 !important; grid-column: auto !important; box-sizing: border-box !important;
  }
  .gg-section-body--grid > * + * { margin-top: 10pt !important; }
  .gg-section-body--grid .gg-table-wrap,
  .gg-section-body--grid .gg-chart-wrap,
  .gg-section-body--grid .gg-figure,
  .gg-table-wrap, .gg-chart-wrap, .gg-figure {
    width: 100% !important; max-width: 100% !important; min-width: 0 !important;
    margin-left: 0 !important; margin-right: 0 !important; box-sizing: border-box !important;
  }
  .gg-table-wrap { overflow: visible !important; break-inside: auto !important; page-break-inside: auto !important; }
  .gg-table-scroll { display: block !important; overflow: visible !important; width: 100% !important; max-width: 100% !important; }
  table.gg-table { display: table !important; width: 100% !important; max-width: 100% !important; table-layout: auto !important; }
  .gg-table thead { display: table-header-group !important; }
  .gg-table tfoot { display: table-footer-group !important; }
  .gg-table tr { break-inside: avoid-page; page-break-inside: avoid; }
  .gg-table th, .gg-table td {
    white-space: normal !important; word-break: normal !important;
    overflow-wrap: break-word !important; hyphens: auto !important;
  }
  .gg-table--wide th, .gg-table--wide td { padding: 4.5pt 5pt !important; font-size: 8.5pt !important; line-height: 1.25 !important; }
  .gg-table--wide th { font-size: 8pt !important; letter-spacing: .01em !important; }
  .gg-source-url-only { overflow-wrap: anywhere !important; word-break: break-word !important; }
  body.gg-tier--detailed .gg-cover--bold_banner .gg-title,
  body.gg-tier--in_depth .gg-cover--bold_banner .gg-title { font-size: 30pt !important; line-height: 1.06 !important; letter-spacing: -.02em !important; }
  body.gg-tier--detailed .gg-cover--classic .gg-title,
  body.gg-tier--in_depth .gg-cover--classic .gg-title { font-size: 32pt !important; line-height: 1.06 !important; }
  body.gg-tier--detailed .gg-section-body > p,
  body.gg-tier--in_depth .gg-section-body > p { font-size: 10.25pt !important; line-height: 1.47 !important; }
}
</style>
"""
    # V23 premium editorial layer. Keep the HTML report's model-selected content
    # structure, but give every PDF tier a coherent print design: confident page
    # furniture, comfortable reading measure, restrained section hierarchy,
    # repeatable table styling, and a quieter URL-only source appendix.
    # Keep these rules last so older, layered screen styles cannot reintroduce
    # tiny text, all-caps overload, or inconsistent card spacing in the PDF.
    premium_print_overrides = r"""<style id="gg-pdf-v23-premium">
@page {
  size: A4;
  margin: 15mm 13mm 18mm 13mm;
  @top-left {
    content: "GROWTH GRADUAL | RESEARCH INTELLIGENCE";
    font-family: Arial, sans-serif; font-size: 7.2pt; font-weight: 700;
    letter-spacing: .075em; color: #536A73; padding-bottom: 2mm;
    border-bottom: .5pt solid #DCE5DF;
  }
  @top-right { content: none; }
  @bottom-left {
    content: "Growth Gradual | Research Intelligence | {short_title}";
    font-family: Arial, sans-serif; font-size: 7pt; font-weight: 500;
    color: #53636B; padding-top: 2mm; border-top: .5pt solid #DCE5DF;
  }
  @bottom-right {
    content: counter(page); font-family: Arial, sans-serif; font-size: 8pt;
    font-weight: 700; color: #17384F; padding-top: 2mm;
    border-top: .5pt solid #DCE5DF;
  }
}
@page :first {
  @top-left { content: none; border: 0; }
  @top-right { content: none; }
  @bottom-left { content: none; border: 0; }
  @bottom-right { content: none; border: 0; }
}
html, body { background: #fff !important; color: #253640; }
body { font-family: Arial, 'Helvetica Neue', sans-serif !important; font-size: 10.5pt !important; line-height: 1.52 !important; }
main { padding-top: 2mm !important; padding-bottom: 7mm !important; }
.gg-title-band { padding: 7mm 0 5mm !important; margin: 0 0 7mm !important; border-bottom: 2pt solid var(--accent) !important; }
.gg-title-band .gg-title { font-family: Georgia, 'Times New Roman', serif !important; font-size: 27pt !important; line-height: 1.08 !important; letter-spacing: -.025em !important; color: #17384F !important; max-width: 100% !important; }
.gg-title-meta { font-size: 8.5pt !important; color: #61727A !important; }
.gg-cover { margin-top: 0 !important; border-bottom: 2pt solid var(--accent) !important; }
.gg-cover--minimal { padding: 12mm 0 10mm !important; }
.gg-cover--classic { background: #F3F7F4 !important; border: 0 !important; border-left: 4pt solid var(--accent) !important; padding: 6mm 10mm 5mm !important; }
.gg-cover--bold_banner { background: #17384F !important; background-image: none !important; color: #fff !important; padding: 7mm 10mm 6mm !important; border: 0 !important; border-bottom: 4pt solid var(--accent) !important; }
.gg-cover--classic, .gg-cover--bold_banner { min-height: 0 !important; }
.gg-cover--bold_banner .gg-eyebrow,
.gg-cover--bold_banner .gg-title,
.gg-cover--bold_banner .gg-summary,
.gg-cover--bold_banner .gg-date { color: #fff !important; }
.gg-cover--bold_banner .gg-title { font-family: Georgia, 'Times New Roman', serif !important; font-size: 31pt !important; line-height: 1.07 !important; letter-spacing: -.025em !important; }
.gg-cover--data_driven { padding: 14mm 0 10mm !important; gap: 8mm !important; }
.gg-cover__aside { background: #F2F6F3 !important; border-left: 3pt solid var(--accent) !important; padding: 11pt 13pt !important; }
.gg-eyebrow, .gg-report-brandbar { font-size: 7.5pt !important; letter-spacing: .13em !important; }
.gg-dashboard-strip { margin: 5mm 0 7mm !important; }
.gg-dashboard-strip .gg-stats-grid,
.gg-stats-grid { grid-template-columns: repeat(3, minmax(0, 1fr)) !important; gap: 7pt !important; }
.gg-stat-card { min-width: 0 !important; padding: 10pt 11pt !important; border: .65pt solid #DDE6E0 !important; border-radius: 5pt !important; background: #FBFCFB !important; box-shadow: none !important; }
.gg-stat-card::before { width: 2pt !important; }
.gg-stat-value { font-size: 17pt !important; line-height: 1.08 !important; letter-spacing: -.015em !important; color: #17384F !important; }
.gg-stat-label { margin-top: 4pt !important; font-size: 7.1pt !important; line-height: 1.22 !important; letter-spacing: .055em !important; color: #5E7078 !important; }
.gg-stat-change { margin-top: 4pt !important; font-size: 8pt !important; }
.gg-summary-wrap { margin: 0 0 7mm !important; }
.gg-summary-card { padding: 14pt 17pt !important; border: .7pt solid #DEE6E1 !important; border-left: 3pt solid var(--accent) !important; border-radius: 0 7pt 7pt 0 !important; background: #F7F9F7 !important; box-shadow: none !important; }
.gg-summary-heading { font-size: 19pt !important; line-height: 1.15 !important; margin: 0 0 7pt !important; color: #17384F !important; }
.gg-report-index { padding: 13pt 15pt !important; margin: 5mm 0 7mm !important; border: .7pt solid #DEE6E1 !important; border-radius: 5pt !important; background: #FBFCFB !important; box-shadow: none !important; }
.gg-report-index h2 { margin: 0 0 7pt !important; font-size: 15pt !important; color: #17384F !important; }
.gg-report-index ol { gap: 0 18pt !important; }
.gg-report-index li { border-bottom: .5pt solid #E3EAE5 !important; }
/* Paged-media engines disagree on target-counter/leader placement, sometimes
   showing the wrong page number or dropping leaders onto a second line. Keep a
   clean, clickable contents list rather than printing misleading pagination. */
.gg-report-index a { display: grid !important; grid-template-columns: 23pt minmax(0,1fr) !important; gap: 6pt !important; align-items: baseline !important; padding: 6pt 0 !important; font-size: 8.7pt !important; line-height: 1.28 !important; }
.gg-report-index a::before { grid-column: 1; font-size: 7.5pt !important; }
.gg-report-index .gg-report-index-label { grid-column: 2; }
.gg-report-index a::after { content: none !important; display: none !important; }
.gg-section { margin-bottom: 19pt !important; padding-top: 0 !important; break-inside: auto !important; }
.gg-section + .gg-section { margin-top: 0 !important; }
.gg-section-heading { display: grid !important; grid-template-columns: 27pt minmax(0,1fr) !important; gap: 8pt !important; padding: 0 0 7pt !important; margin: 0 0 9pt !important; border-bottom: .75pt solid #D9E3DD !important; break-inside: avoid !important; break-after: avoid-page !important; }
.gg-section-index { width: 23pt !important; height: 23pt !important; font-size: 7.5pt !important; background: #17384F !important; color: #fff !important; }
.gg-section-heading-copy::before { margin: 0 0 3pt !important; font-size: 6.2pt !important; letter-spacing: .13em !important; color: var(--accent) !important; }
.gg-section-heading-copy h2 { max-width: 34ch !important; font-size: 18.5pt !important; line-height: 1.13 !important; letter-spacing: -.018em !important; color: #17384F !important; }
.gg-section-body { width: 100% !important; max-width: 100% !important; }
.gg-section-body > p, .gg-section-body > ul, .gg-section-body > ol { max-width: 100% !important; }
.gg-section-body > p { margin: 0 0 7pt !important; font-size: 10.5pt !important; line-height: 1.54 !important; text-align: justify !important; text-justify: inter-word !important; hyphens: auto !important; orphans: 3 !important; widows: 3 !important; color: #34464F !important; }
.gg-section-body > p + p { margin-top: 0 !important; }
.gg-section-body li { font-size: 10.2pt !important; line-height: 1.46 !important; margin: 0 0 4pt !important; color: #34464F !important; }
.gg-section-h3 { margin: 14pt 0 6pt !important; font-size: 12.4pt !important; line-height: 1.22 !important; color: #17384F !important; break-after: avoid-page !important; }
.gg-subsection-h4 { margin: 10pt 0 4pt !important; font-size: 10.3pt !important; line-height: 1.22 !important; color: #2F7354 !important; font-weight: 700 !important; break-after: avoid-page !important; }
.gg-pullquote { margin: 12pt 0 !important; padding: 10pt 13pt !important; font-family: Georgia, 'Times New Roman', serif !important; font-size: 13pt !important; line-height: 1.38 !important; border-left: 3pt solid var(--accent) !important; background: #F5F8F5 !important; color: #29434D !important; }
.gg-callout, .gg-risk { padding: 10pt 12pt !important; border-color: #DCE5DF !important; border-radius: 4pt !important; box-shadow: none !important; }
.gg-chart-wrap, .gg-table-wrap, .gg-figure, .gg-diagram-wrap { margin: 12pt 0 14pt !important; padding: 10pt 11pt !important; border: .65pt solid #DDE5E0 !important; border-radius: 5pt !important; background: #fff !important; box-shadow: none !important; }
.gg-chart-title { margin-bottom: 7pt !important; font-size: 8pt !important; letter-spacing: .085em !important; color: #2C6B50 !important; }
.gg-chart-notes { margin-top: 7pt !important; padding-top: 6pt !important; border-top: .5pt solid #E1E8E3 !important; }
.gg-chart-note { padding: 4pt 6pt !important; font-size: 7.6pt !important; line-height: 1.32 !important; }
.gg-chart-note-label { min-width: 48pt !important; font-size: 6.8pt !important; }
.gg-chart-note--takeaway { background: #F2F6F3 !important; border-radius: 3pt !important; }
.gg-chart-canvas-box { min-height: 0 !important; }
.gg-chart-inline-svg svg, .gg-pdf-chart-fallback svg { max-width: 100% !important; height: auto !important; }
.gg-figure { text-align: center !important; }
.gg-figure img { max-width: 100% !important; max-height: 360pt !important; object-fit: contain !important; border: 0 !important; }
.gg-figure figcaption { margin-top: 5pt !important; font-size: 8pt !important; line-height: 1.35 !important; font-style: italic !important; color: #65757B !important; }
.gg-table-wrap { padding: 7pt !important; }
.gg-table-scroll { overflow: visible !important; }
table.gg-table { width: 100% !important; max-width: 100% !important; table-layout: auto !important; border-collapse: collapse !important; font-size: 8.8pt !important; line-height: 1.3 !important; }
.gg-table thead { display: table-header-group !important; }
.gg-table tr { break-inside: avoid-page !important; page-break-inside: avoid !important; }
.gg-table th { padding: 6.5pt 7pt !important; background: #17384F !important; color: #fff !important; border: .5pt solid #17384F !important; font-family: Arial, sans-serif !important; font-size: 7.6pt !important; line-height: 1.25 !important; letter-spacing: .035em !important; text-transform: uppercase !important; white-space: normal !important; overflow-wrap: break-word !important; }
.gg-table td { padding: 6pt 7pt !important; background: #fff !important; border-bottom: .5pt solid #DCE5DF !important; font-size: 8.7pt !important; line-height: 1.32 !important; color: #34464F !important; vertical-align: top !important; white-space: normal !important; overflow-wrap: break-word !important; word-break: normal !important; hyphens: auto !important; font-variant-numeric: tabular-nums !important; }
.gg-table tbody tr:nth-child(even) td { background: #F5F8F6 !important; }
.gg-table--wide th { font-size: 7.1pt !important; padding: 5pt 5.5pt !important; }
.gg-table--wide td { font-size: 8pt !important; padding: 5.5pt 5.5pt !important; }
.gg-timeline { gap: 0 !important; }
.gg-timeline-item { grid-template-columns: 76pt minmax(0,1fr) !important; gap: 9pt !important; padding: 8pt 0 !important; border-top: .5pt solid #DFE7E2 !important; }
.gg-timeline-date { font-size: 8pt !important; }
.gg-timeline-title { font-size: 10pt !important; }
.gg-timeline-desc { font-size: 9pt !important; line-height: 1.4 !important; }
.gg-sources { margin-top: 18pt !important; padding: 12pt 0 0 !important; border: 0 !important; border-top: 2pt solid var(--accent) !important; border-radius: 0 !important; background: #fff !important; box-shadow: none !important; break-before: auto !important; }
.gg-sources[data-source-placement="appendix"] { break-before: auto !important; page-break-before: auto !important; }
.gg-sources h2 { margin: 0 0 4pt !important; font-family: Georgia, 'Times New Roman', serif !important; font-size: 18pt !important; line-height: 1.15 !important; color: #17384F !important; }
.gg-sources-count { margin: 0 0 7pt !important; font-size: 8.6pt !important; color: #66767C !important; font-weight: 600 !important; }
.gg-sources-grid { display: block !important; width: 100% !important; }
.gg-source-card--url-only { display: block !important; width: 100% !important; padding: 6pt 0 !important; margin: 0 !important; border: 0 !important; border-bottom: .5pt solid #E3EAE5 !important; border-radius: 0 !important; background: transparent !important; box-shadow: none !important; break-inside: avoid !important; }
body.gg-tier--brief .gg-sources-grid { display: grid !important; grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 3pt 12pt !important; align-items: start !important; }
body.gg-tier--brief .gg-source-card--url-only { padding: 4pt 0 !important; }
.gg-source-url-only, .gg-source-link.gg-source-url-only { display: block !important; width: 100% !important; font-size: 8.6pt !important; line-height: 1.35 !important; color: #286849 !important; text-decoration: underline !important; overflow-wrap: anywhere !important; word-break: normal !important; }
.gg-source-note { margin-top: 5pt !important; padding-top: 5pt !important; font-size: 7.6pt !important; line-height: 1.32 !important; border-top: .5pt solid #E3EAE5 !important; }
.gg-source-identity { display: block !important; margin: 0 0 3pt !important; font-size: 8.3pt !important; line-height: 1.3 !important; }
.gg-source-id { display: inline-block !important; margin-right: 4pt !important; color: #2F7354 !important; font-weight: 700 !important; white-space: nowrap !important; }
.gg-source-title-link, .gg-source-title-text { color: #17384F !important; font-weight: 700 !important; text-decoration: none !important; }
.gg-source-publisher { color: #66767C !important; font-weight: 500 !important; }
.gg-report-footer, .gg-footer { display: none !important; }
.gg-report-nav { display: none !important; }

/* V24 print fit: use the printable page width intentionally without removing
   the restrained margins needed for professional print and page furniture. */
@page { size: A4; margin: 12mm 10mm 14mm 10mm; }
html, body { width: 100% !important; max-width: 100% !important; min-width: 0 !important; }
main, .gg-cover, .gg-report-sections, .gg-section, .gg-section-body,
.gg-block, .gg-chart-wrap, .gg-table-wrap, .gg-chart-canvas-box,
.gg-chart-inline-svg, .gg-figure, .gg-sources {
  width: 100% !important; max-width: 100% !important; min-width: 0 !important;
  margin-left: 0 !important; margin-right: 0 !important; box-sizing: border-box !important;
}
main { padding: 10pt 0 16pt !important; }
.gg-section { margin-bottom: 14pt !important; }
.gg-section-body > p { width: 100% !important; max-width: 100% !important; margin-left: 0 !important; margin-right: 0 !important; }
.gg-chart-wrap, .gg-table-wrap, .gg-figure, .gg-diagram-wrap { margin-top: 9pt !important; margin-bottom: 10pt !important; }
.gg-section-body--grid { display: block !important; grid-template-columns: none !important; gap: 0 !important; }
.gg-section-body--grid > * { display: block !important; width: 100% !important; max-width: 100% !important; min-width: 0 !important; }
.gg-report-index { width: 100% !important; max-width: 100% !important; }
.gg-reveal { opacity: 1 !important; transform: none !important; }
body.gg-tier--brief .gg-section-heading-copy h2 { font-size: 16.5pt !important; }
body.gg-tier--brief .gg-section-body > p { font-size: 10.2pt !important; line-height: 1.47 !important; margin-bottom: 5.5pt !important; }
body.gg-tier--detailed .gg-section-heading-copy h2 { font-size: 18pt !important; }
body.gg-tier--in_depth .gg-section-heading-copy h2 { font-size: 18.5pt !important; }
body.gg-tier--in_depth .gg-section-body > p { font-size: 10.35pt !important; line-height: 1.52 !important; }
body.gg-tier--in_depth .gg-section { margin-bottom: 18pt !important; }
body.gg-tier--in_depth .gg-section-body--grid,
body.gg-tier--detailed .gg-section-body--grid,
body.gg-tier--brief .gg-section-body--grid { display: block !important; grid-template-columns: none !important; }
body.gg-tier--in_depth .gg-section-body--grid > *,
body.gg-tier--detailed .gg-section-body--grid > *,
body.gg-tier--brief .gg-section-body--grid > * { display: block !important; width: 100% !important; max-width: 100% !important; }
</style>"""
    # V25 pagination and visual-polish layer. Keep headings attached to useful
    # following content, allow genuinely long tables to flow across pages,
    # and render a soft, distinct colour for each evidence-backed KPI card.
    # This layer is intentionally last so legacy screen styles cannot undo it.
    premium_print_overrides = premium_print_overrides.replace("</style>", r"""
<style id="gg-pdf-v25-finish">
:root { print-color-adjust: exact !important; -webkit-print-color-adjust: exact !important; }
*, *::before, *::after { print-color-adjust: exact !important; -webkit-print-color-adjust: exact !important; }
/* Avoid orphan section/subsection headings at page bottoms. The corresponding
   first content block is kept with the heading when page space is insufficient. */
h1, h2, h3, h4, .gg-section-heading, .gg-section-heading-copy,
.gg-section-h3, .gg-subsection-h4 {
  break-after: avoid-page !important; page-break-after: avoid !important;
}
.gg-section-heading { break-inside: avoid-page !important; page-break-inside: avoid !important; }
.gg-section-heading + .gg-section-body { break-before: avoid-page !important; page-break-before: avoid !important; }
.gg-section-heading + .gg-section-body > :first-child,
.gg-section-h3 + p, .gg-section-h3 + ul, .gg-section-h3 + ol,
.gg-section-h3 + .gg-table-wrap, .gg-section-h3 + .gg-chart-wrap,
.gg-section-h3 + .gg-callout, .gg-section-h3 + .gg-risk, .gg-section-h3 + blockquote,
.gg-subsection-h4 + p, .gg-subsection-h4 + ul, .gg-subsection-h4 + ol,
.gg-subsection-h4 + .gg-table-wrap, .gg-subsection-h4 + .gg-chart-wrap,
.gg-subsection-h4 + .gg-callout, .gg-subsection-h4 + .gg-risk, .gg-subsection-h4 + blockquote {
  break-before: avoid-page !important; page-break-before: avoid !important;
}
/* Long tables should paginate naturally; short tables (including their
   citation line) are kept together so a source note cannot strand on a page
   by itself. */
.gg-table-wrap, .gg-table-scroll { break-inside: auto !important; page-break-inside: auto !important; overflow: visible !important; }
.gg-table-wrap[data-row-count="1"], .gg-table-wrap[data-row-count="2"],
.gg-table-wrap[data-row-count="3"], .gg-table-wrap[data-row-count="4"] {
  break-inside: avoid-page !important; page-break-inside: avoid !important;
}
.gg-table-wrap .gg-source-note { break-before: avoid-page !important; page-break-before: avoid !important; }
.gg-table { break-inside: auto !important; page-break-inside: auto !important; }
.gg-table thead { display: table-header-group !important; }
.gg-table tr { break-inside: avoid-page !important; page-break-inside: avoid !important; }
.gg-table td, .gg-table th { max-width: 100%; overflow-wrap: anywhere !important; word-break: normal !important; }
/* Compact source appendices should flow after the report instead of forcing a
   nearly empty final page. Larger source lists naturally continue over pages. */
body.gg-tier--in_depth .gg-sources[data-source-placement="end_of_report"],
.gg-sources[data-source-placement="appendix"], .gg-sources[data-source-placement="end_of_report"] {
  break-before: auto !important; page-break-before: auto !important;
}
.gg-sources[data-source-count="1"], .gg-sources[data-source-count="2"],
.gg-sources[data-source-count="3"], .gg-sources[data-source-count="4"],
.gg-sources[data-source-count="5"], .gg-sources[data-source-count="6"] {
  break-inside: auto !important; page-break-inside: auto !important; margin-top: 10pt !important; padding-top: 8pt !important;
}
.gg-sources h2 { break-after: avoid-page !important; page-break-after: avoid !important; }
/* Tiny bibliographies should use a compact editorial treatment so one or two
   URLs can fit on the final content page instead of generating a mostly empty
   source-only page. Larger appendices retain the standard source typography. */
.gg-sources[data-source-count="1"], .gg-sources[data-source-count="2"] {
  margin-top: 5pt !important; padding-top: 4pt !important;
}
.gg-sources[data-source-count="1"] h2, .gg-sources[data-source-count="2"] h2 {
  font-size: 12.5pt !important; line-height: 1.05 !important; margin: 0 0 2pt !important;
}
.gg-sources[data-source-count="1"] .gg-sources-count, .gg-sources[data-source-count="2"] .gg-sources-count {
  font-size: 7.4pt !important; margin: 0 0 3pt !important; line-height: 1.1 !important;
}
.gg-sources[data-source-count="1"] .gg-source-card--url-only,
.gg-sources[data-source-count="2"] .gg-source-card--url-only { padding: 2pt 0 !important; }
.gg-sources[data-source-count="1"] .gg-source-url-only,
.gg-sources[data-source-count="2"] .gg-source-url-only { font-size: 7.4pt !important; line-height: 1.2 !important; }

.gg-sources[data-source-count="1"] { margin-top: 1pt !important; padding-top: 2pt !important; border-top-width: 1pt !important; }
.gg-sources[data-source-count="1"] h2 { font-size: 11pt !important; line-height: 1.02 !important; margin: 0 0 1pt !important; }
.gg-sources[data-source-count="1"] .gg-sources-count { font-size: 7.2pt !important; line-height: 1.05 !important; margin: 0 0 1pt !important; }
.gg-sources[data-source-count="1"] .gg-source-card--url-only { padding: 0 !important; border-bottom: 0 !important; }
.gg-sources[data-source-count="1"] .gg-source-url-only { font-size: 7.6pt !important; line-height: 1.1 !important; }
.gg-sources-count { break-before: avoid-page !important; break-after: avoid-page !important; page-break-after: avoid !important; }
/* Keep chart panels visually crisp without reserving a fixed blank chart height. */
.gg-chart-canvas-box { min-height: 0 !important; height: auto !important; }
.gg-chart-inline-svg, .gg-chart-inline-svg svg, .gg-pdf-chart-fallback svg { height: auto !important; max-height: 330pt !important; }
.gg-chart-wrap { break-inside: avoid-page !important; page-break-inside: avoid !important; }
.gg-figure { break-inside: avoid-page !important; page-break-inside: avoid !important; }
/* Colour-coded KPI cards: distinct pastel fills with accessible ink and a matching rail. */
.gg-stat-card {
  background: var(--kpi-tint, #F7F9F8) !important;
  border: .75pt solid var(--kpi-accent, #DDE6E0) !important;
  border-radius: 6pt !important;
  box-shadow: none !important;
  padding: 10pt 11pt 9pt 13pt !important;
}
.gg-stat-card::before { background: var(--kpi-accent, #2F7354) !important; width: 3pt !important; opacity: 1 !important; }
.gg-stat-value { color: var(--kpi-ink, #17384F) !important; font-size: 17.5pt !important; line-height: 1.08 !important; }
.gg-stat-label { color: var(--kpi-ink, #53636B) !important; opacity: .9 !important; font-size: 7.2pt !important; line-height: 1.24 !important; }
.gg-stat-change { color: var(--kpi-accent, #2F7354) !important; font-weight: 700 !important; }
.gg-stat-card .gg-change-down { color: #A63D37 !important; }
.gg-stat-card .gg-change-up { color: #1C7651 !important; }
/* Better page density without cramping: normalize margins and avoid oversized gaps. */
.gg-section { margin-bottom: 12pt !important; }
.gg-section-body > p { margin-bottom: 6pt !important; line-height: 1.46 !important; }
.gg-section-h3 { margin-top: 10pt !important; margin-bottom: 5pt !important; }
.gg-subsection-h4 { margin-top: 8pt !important; margin-bottom: 4pt !important; }
.gg-chart-wrap, .gg-table-wrap, .gg-figure, .gg-diagram-wrap { margin-top: 8pt !important; margin-bottom: 9pt !important; }
.gg-callout, .gg-risk, .gg-pullquote { break-inside: avoid-page !important; page-break-inside: avoid !important; }
/* A restrained rotating accent on section numbers makes long reports easier to scan. */
.gg-report-sections > .gg-section:nth-child(6n + 1) .gg-section-index { background: #2F62B4 !important; }
.gg-report-sections > .gg-section:nth-child(6n + 2) .gg-section-index { background: #22815A !important; }
.gg-report-sections > .gg-section:nth-child(6n + 3) .gg-section-index { background: #A56B12 !important; }
.gg-report-sections > .gg-section:nth-child(6n + 4) .gg-section-index { background: #7654B8 !important; }
.gg-report-sections > .gg-section:nth-child(6n + 5) .gg-section-index { background: #167C89 !important; }
.gg-report-sections > .gg-section:nth-child(6n) .gg-section-index { background: #B94E43 !important; }
/* Premium semantic accents for findings and risks without painting every block. */
.gg-callout--positive { background: #E9F6EF !important; border-left-color: #27845A !important; }
.gg-callout--warning { background: #FFF4DF !important; border-left-color: #B67A17 !important; }
.gg-callout--negative, .gg-risk { background: #FCEDEB !important; border-left-color: #B94E43 !important; }
.gg-callout--info { background: #EAF2FF !important; border-left-color: #2F62B4 !important; }
.gg-pullquote { background: #F2F6F9 !important; border-left-color: #2F62B4 !important; }
/* Maintain legible text and avoid browser-generated background omission in print. */
.gg-title-band, .gg-cover, .gg-summary-card, .gg-report-index, .gg-stat-card,
.gg-callout, .gg-risk, .gg-pullquote, .gg-table th, .gg-chart-note--takeaway {
  -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important;
}
/* V26 regression fix: risk assessment sections often contain a short prose
   lead-in followed by a table that must paginate to the next page. Do not let
   the section's tinted background/padding paint a giant empty panel on page 4. */
.gg-section-type--risk_assessment,
.gg-section[data-emphasis="critical"].gg-section-type--risk_assessment {
  background: transparent !important; border: 0 !important; box-shadow: none !important;
  padding: 0 !important; margin-bottom: 8pt !important;
}
.gg-section-type--risk_assessment .gg-section-body,
.gg-section-type--risk_assessment .gg-table-wrap,
.gg-section-type--risk_assessment .gg-table-scroll {
  background: transparent !important; break-inside: auto !important;
  page-break-inside: auto !important; overflow: visible !important;
}
.gg-section-type--risk_assessment .gg-table-wrap { padding: 6pt 7pt !important; margin-top: 6pt !important; margin-bottom: 6pt !important; }
.gg-section-type--risk_assessment .gg-table tr { break-inside: avoid-page !important; page-break-inside: avoid !important; }
/* Static SVG charts size to their own content. Fixed-height chart boxes with
   overflow:hidden cut off x-axis labels and legends in the PDF. */
body .gg-chart-wrap .gg-chart-canvas-box.gg-chart-canvas-box--static {
  height: auto !important; min-height: 0 !important; overflow: visible !important;
}
body .gg-chart-canvas-box--static .gg-pdf-chart-fallback,
body .gg-chart-canvas-box--static .gg-pdf-chart-fallback svg {
  max-height: none !important; height: auto !important; width: 100% !important; overflow: visible !important;
}
</style>""")
    premium_print_overrides = premium_print_overrides.replace("{short_title}", short_title)
    # v93 editorial layer — last in the cascade so legacy print layers cannot
    # undo it. Only applies when the document carries body.gg-editorial.
    editorial_print = ""
    if re.search(r'<body[^>]*class=["\'][^"\']*\bgg-editorial\b', html_doc, flags=re.IGNORECASE):
        from routes.report_design import editorial_css as _editorial_css, editorial_print_css as _editorial_print_css
        editorial_print = (
            '<style id="gg-pdf-v93-editorial">' + _editorial_css()
            + _editorial_print_css().replace("{short_title}", short_title) + "</style>"
        )
    # Appended to the premium layer so it stays last in the cascade.
    premium_print_overrides = premium_print_overrides + editorial_print
    return html_doc.replace(
        "</head>",
        print_css.replace("{short_title}", short_title) + final_print_overrides + premium_print_overrides + "</head>",
        1,
    )


def _trim_furniture_only_middle_pages(pdf_bytes: bytes) -> bytes:
    """Remove genuine interior blanks caused by print fragmentation.

    Conservative: keep any page with text in its content area, images, links,
    or substantive drawings. The first and last pages are never removed here.
    PyMuPDF is optional; without it return the PDF untouched.
    """
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(document) < 3:
            return pdf_bytes
        remove = []
        for index in range(1, len(document) - 1):
            page = document[index]
            r = page.rect
            content = fitz.Rect(r.x0 + 24, r.y0 + 67, r.x1 - 24, r.y1 - 61)
            text = (page.get_text("text", clip=content) or "").strip()
            if text or page.get_images(full=True) or page.get_links():
                continue
            drawings = page.get_drawings()
            if len(drawings) > 8:
                continue
            # Do not strip graphics-only reports or image-backed diagrams.
            non_furniture = [d for d in drawings if fitz.Rect(d.get("rect", r)).intersects(content)]
            if non_furniture:
                continue
            remove.append(index)
        if not remove:
            return pdf_bytes
        for index in reversed(remove):
            document.delete_page(index)
        log.warning("PDF: removed %d furniture-only interior page(s)", len(remove))
        return document.tobytes(garbage=3, deflate=True)
    except Exception as exc:
        log.debug("PDF: interior blank-page check unavailable: %s", exc)
        return pdf_bytes


def _trim_trailing_blank_pages(pdf_bytes: bytes) -> bytes:
    """Remove only trailing pages that have running furniture but no report content."""
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        if len(document) < 2:
            return pdf_bytes
        removed = 0
        while len(document) > 1:
            page = document[-1]
            rect = page.rect
            # Exclude the renderer's repeating top header and bottom footer.
            content = fitz.Rect(rect.x0 + 24, rect.y0 + 67, rect.x1 - 24, rect.y1 - 61)
            if (page.get_text("text", clip=content) or "").strip():
                break
            if any(
                image_rect.intersects(content)
                for image in page.get_images(full=True)
                for image_rect in page.get_image_rects(image[0])
            ):
                break
            if any(fitz.Rect(link.get("from", rect)).intersects(content) for link in page.get_links()):
                break
            if any(fitz.Rect(draw.get("rect", rect)).intersects(content) for draw in page.get_drawings()):
                break
            document.delete_page(-1)
            removed += 1
        if not removed:
            return pdf_bytes
        log.warning("PDF: removed %d trailing furniture-only page(s)", removed)
        return document.tobytes(garbage=3, deflate=True)
    except Exception as exc:
        log.debug("PDF: trailing blank-page check unavailable: %s", type(exc).__name__)
        return pdf_bytes

def _pdf_with_chromium(html_doc: str) -> bytes:
    import shutil
    import subprocess
    import tempfile

    candidates = [
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        shutil.which("msedge"),
    ]
    # Windows desktop installs usually do not put Chrome on PATH. Check the
    # standard installation locations so the dynamic HTML renderer works on
    # a normal Windows development machine without any manual PATH changes.
    if os.name == "nt":
        program_files = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")]
        for base in [x for x in program_files if x]:
            candidates.extend([
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
            ])
    chrome = next((c for c in candidates if c and os.path.exists(c)), None)
    if not chrome:
        raise RuntimeError("Chromium/Chrome/Edge is not available for dynamic PDF rendering")
    with tempfile.TemporaryDirectory(prefix="gg_pdf_") as td:
        html_path=os.path.join(td,"report.html"); pdf_path=os.path.join(td,"report.pdf")
        Path(html_path).write_text(html_doc, encoding="utf-8")
        # Chromium's documented flag is --no-pdf-header-footer. The older
        # --print-to-pdf-no-header flag is ignored by current Chrome builds,
        # which caused the temporary file:///.../report.html path to print.
        cmd=[chrome,"--headless","--no-sandbox","--disable-gpu","--disable-dev-shm-usage","--allow-file-access-from-files","--no-pdf-header-footer",f"--print-to-pdf={pdf_path}",f"file://{html_path}"]
        proc=subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if proc.returncode!=0 or not os.path.exists(pdf_path):
            raise RuntimeError(proc.stderr.decode("utf-8","replace")[-2000:])
        data=Path(pdf_path).read_bytes()
        if not data.startswith(b"%PDF-"): raise RuntimeError("Invalid PDF from Chromium")
        return data


def _pdf_with_playwright(html_doc: str) -> bytes:
    """Render the same HTML composition through installed Playwright Chromium.

    The synchronous Playwright API is isolated in a worker thread because the
    FastAPI route is async. This keeps the dynamic HTML/CSS PDF path intact and
    makes the legacy ReportLab renderer a true last resort only.
    """
    result: dict[str, object] = {}

    def _worker() -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1024}, device_scale_factor=1)
                page.set_content(html_doc, wait_until="networkidle", timeout=30_000)
                page.wait_for_timeout(350)
                result["data"] = page.pdf(
                    format="A4", print_background=True, prefer_css_page_size=True,
                    margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"},
                )
                browser.close()
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_worker, name="gg-playwright-pdf", daemon=True)
    thread.start()
    thread.join(40)
    if thread.is_alive():
        raise RuntimeError("Playwright PDF rendering timed out after 40s")
    if "error" in result:
        raise result["error"]  # type: ignore[misc]
    data = result.get("data")
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(b"%PDF-"):
        raise RuntimeError("Playwright did not return a valid PDF")
    return bytes(data)


def _pdf_with_weasyprint(html_doc: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=html_doc, base_url=str(Path.cwd())).write_pdf()


# V7 PREMIUM PDF PAGINATION: editorial Brief flow + shared HTML/print composition.
def build_pdf(report: str, title: str, question: str, summary: str,
              key_stats: list, charts: list, logo_b64: str = "",
              file_images: list | None = None, web_images: list | None = None,
              theme: dict | None = None, sources: object = None,
              presentation: object = None, report_tier: str = "detailed") -> bytes:
    """Render PDF from the same presentation-driven HTML composition as HTML reports."""
    from routes.html_report import build_html_report
    try:
        from routes.report import _sanitize_theme
        safe_theme = _sanitize_theme(theme)
    except Exception:
        safe_theme = theme if isinstance(theme, dict) else None

    if report_tier == "in_depth":
        safe_theme = dict(safe_theme or {})
        safe_theme.update({
            "primaryColor": "#17384F",
            "accentColor": "#387C58",
            "surfaceColor": "#FFFFFF",
            "surfaceAltColor": "#F2F6F3",
            "textColor": "#1D2B33",
            "mutedColor": "#34464F",
            "borderColor": "#CCD8D1",
            "fontFamily": "",
            "chartColors": ["#2F7857", "#167C8A", "#315D86", "#916D3E", "#805574", "#A24E43"],
        })
        theme = safe_theme

    from utils.report_scanability import strip_generation_markers
    report = strip_generation_markers(normalize_report_markdown(report))
    images = _prepare_pdf_images([], web_images, file_images)
    # HTML understands WEB_IMG_n placeholders. Preserve the existing FILE_IMG_n
    # / PAGE_IMG_n report references by appending local file images and mapping
    # their placeholders to the corresponding WEB_IMG slots.
    file_start = sum(1 for img in images if img)
    if file_images:
        local_file_images = []
        for item in file_images:
            if not isinstance(item, dict):
                continue
            payload = str(item.get("data") or "").strip()
            if not payload:
                continue
            local_file_images.append({
                "url": _pdf_data_uri_from_base64(payload, item.get("mimeType") or _image_mime_from_base64(payload)),
                "caption": item.get("name", ""),
            })
        if local_file_images:
            images = [img for img in images if img] + local_file_images
            offset = file_start
            report = re.sub(r"\[(?:FILE_IMG|PAGE_IMG)_(\d+)\]", lambda m: f"[WEB_IMG_{offset + int(m.group(1))}]", report or "")

    html_doc = build_html_report(
        report or "", title or "", question or "Research Report", summary or "",
        key_stats or [], charts or [], images, safe_theme, sources, presentation, report_tier,
    )
    # v93: static SVG charts use the same validated editorial palette as the
    # browser charts so preview and PDF match.
    from routes.report_design import editorial_render_theme as _editorial_render_theme
    theme = dict(theme or {}) if isinstance(theme, dict) else {}
    theme.update(_editorial_render_theme())
    html_doc = _replace_chart_runtime_with_svg(html_doc, theme=theme)
    html_doc = _strip_non_printing_runtime(html_doc)

    # WeasyPrint is preferred because it is deterministic and does not require
    # a browser runtime. Chromium remains a fallback for deployments that have
    # the browser available, preserving the HTML/CSS composition faithfully.
    def _finalize(data: bytes, renderer: str) -> bytes:
        final = _trim_furniture_only_middle_pages(_trim_trailing_blank_pages(data))
        try:
            from pypdf import PdfReader
            pages = len(PdfReader(io.BytesIO(final)).pages)
            log.info("PDF: rendered with %s — pages=%d report_chars=%d", renderer, pages, len(report))
        except Exception:
            pass
        return final

    # Browser-first rendering is important because the report is HTML-first.
    # On Windows, the synchronous Playwright wrapper can fail in worker threads
    # with asyncio NotImplementedError when the active event-loop policy does
    # not support subprocesses (seen in the user's logs). If system Chrome/Edge
    # is installed, render with it directly rather than emitting unhandled
    # Playwright thread-future exceptions on every pagination preflight. On
    # Linux/container deployments retain Playwright-first behavior.
    pw_exc: Exception | None = None
    chrome_exc: Exception | None = None
    if os.name == "nt":
        try:
            return _finalize(_pdf_with_chromium(html_doc), "system Chromium (Windows-first)")
        except Exception as exc:
            chrome_exc = exc
            log.warning("PDF: system Chromium unavailable/failed on Windows; trying Playwright: %s", exc)
        try:
            return _finalize(_pdf_with_playwright(html_doc), "Playwright Chromium")
        except Exception as exc:
            pw_exc = exc
            log.warning("PDF: Playwright Chromium unavailable/failed; trying WeasyPrint: %s", exc)
    else:
        try:
            return _finalize(_pdf_with_playwright(html_doc), "Playwright Chromium")
        except Exception as exc:
            pw_exc = exc
            log.warning("PDF: Playwright Chromium unavailable/failed; trying system Chromium: %s", exc)
        try:
            return _finalize(_pdf_with_chromium(html_doc), "system Chromium")
        except Exception as exc:
            chrome_exc = exc
            log.warning("PDF: system Chromium unavailable/failed; trying WeasyPrint: %s", exc)
    try:
        return _finalize(_pdf_with_weasyprint(html_doc), "WeasyPrint")
    except Exception as exc:
        log.error("PDF: dynamic HTML-to-PDF failed: playwright=%s chromium=%s weasyprint=%s", pw_exc, chrome_exc, exc)
        raise RuntimeError(
            "Dynamic PDF renderer unavailable. Install Playwright Chromium (playwright install chromium) "
            "or ensure Chrome/Edge is installed on PATH; legacy ReportLab fallback is disabled to avoid "
            "producing a structurally different report."
        ) from exc


# ─── Route ────────────────────────────────────────────────────────────────────
def _normalized_numeric_observation_line(value: object) -> str:
    text = re.sub(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", str(value or ""), flags=re.I)
    text = re.sub(r"\bS\d+\b", "", text, flags=re.I)
    return re.sub(r"[^a-z0-9%₹$€£]+", " ", text.casefold()).strip()


def _remove_exact_duplicate_observation_lines(report: str, findings: list[dict]) -> tuple[str, int]:
    """Remove only exact duplicate numeric lines explicitly identified by the PDF audit.

    This conservative repair never edits chart payloads, citations, values or paraphrases.
    It is attempted only when the duplicate line can be matched inside report Markdown.
    """
    duplicate_targets = {
        _normalized_numeric_observation_line(item.get("text"))
        for item in (findings or []) if isinstance(item, dict)
        and "repeats the same numeric observation" in str(item.get("issue") or "").casefold()
    }
    duplicate_targets.discard("")
    if not duplicate_targets:
        return report, 0
    seen: set[str] = set()
    output: list[str] = []
    removed = 0
    metric_line = re.compile(
        r"(?i)\b(?:credit|deposits?|gross npa|net npa|gnpa|nnpa|non[- ]performing|liabilit(?:y|ies)|"
        r"assets?|net profit|profitability|net interest margin|nim|repo rate|interest rate|upi|growth|ratio|capital adequacy|"
        r"return on assets|return on equity|transactions?)\b"
    )
    for line in str(report or "").splitlines():
        norm = _normalized_numeric_observation_line(line)
        candidate = bool(len(line.strip()) >= 24 and metric_line.search(line) and re.search(r"\d", line))
        if candidate and norm in duplicate_targets:
            if norm in seen:
                removed += 1
                continue
            seen.add(norm)
        output.append(line)
    return "\n".join(output), removed


def _repair_rendered_chart_audit(report: str, charts: list, findings: list[dict]) -> tuple[str, list[dict], dict]:
    """Prune only chart points named by the rendered-PDF audit, then renumber markers."""
    mapped_failures: list[dict] = []
    mapped_conflicts: list[dict] = []
    for finding in findings or []:
        if not isinstance(finding, dict) or not str(finding.get("chartIndex", "")).isdigit():
            continue
        issue = str(finding.get("issue") or "").casefold()
        item = {k: v for k, v in finding.items() if k not in {"severity", "issue"}}
        if "chart/table evidence conflict" in issue:
            mapped_conflicts.append(item)
        elif "chart value without verified publisher-page evidence" in issue:
            mapped_failures.append(item)
    if not mapped_failures and not mapped_conflicts:
        return report, charts, {"removedCharts": 0, "prunedPoints": 0, "attempted": False}
    kept, removed_indices, removed_points = prune_unsupported_chart_points(
        charts, {"chartEvidenceFailures": mapped_failures, "chartTableVerificationConflicts": mapped_conflicts},
    )
    changed = bool(removed_indices or removed_points)
    updated_report = report
    if removed_indices:
        removed_set = set(removed_indices)
        mask = [idx not in removed_set for idx in range(1, len(charts) + 1)]
        updated_report = remap_chart_placeholders(updated_report, mask)
    return updated_report, kept, {
        "removedCharts": len(removed_indices), "prunedPoints": removed_points,
        "attempted": True, "changed": changed, "removedChartIndices": removed_indices,
    }


def _audit_rendered_pdf_bytes(pdf_bytes: bytes, report: str, title: str,
                             charts: list, sources: list) -> dict:
    """Validate the final rendered PDF artifact, not just its source Markdown.

    PyMuPDF is the preferred validator because it exposes the rendered PDF text
    layer consistently. Some deployment environments have historically missed
    that package despite having ``pypdf`` installed. In that case, fall back to
    pypdf for document reopening, page counting and text extraction, then run the
    exact same citation, duplicate-observation and chart-evidence gates below.
    A PDF remains blocked if neither parser can read it or any integrity/evidence
    check fails.
    """
    findings: list[dict] = []
    page_text: list[str] | None = None
    page_count = 0
    validation_engine = "unavailable"
    parser_errors: list[str] = []

    # Try both official PyMuPDF import names, but catch failures during ``open``
    # as well as import failures. Some PyMuPDF 2.x installations import as
    # ``pymupdf`` while an internal legacy ``fitz`` dependency is missing; that
    # must not prevent the reliable pypdf fallback from validating the artifact.
    for module_name in ("pymupdf", "fitz"):
        try:
            module = __import__(module_name)
            document = module.open(stream=pdf_bytes, filetype="pdf")
            try:
                candidate_text = [page.get_text("text") or "" for page in document]
                candidate_count = len(document)
            finally:
                document.close()
            page_text, page_count = candidate_text, candidate_count
            validation_engine = module_name
            break
        except Exception as exc:
            parser_errors.append(f"{module_name}: {type(exc).__name__}: {str(exc)[:100]}")

    if page_text is None:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
            page_text = [(page.extract_text() or "") for page in reader.pages]
            page_count = len(reader.pages)
            validation_engine = "pypdf-fallback"
        except Exception as exc:
            parser_errors.append(f"pypdf: {type(exc).__name__}: {str(exc)[:100]}")
            return {
                "status": "FAIL", "blocking": True, "pageCount": 0,
                "extractedTextChars": 0, "validationEngine": "unavailable",
                "findings": [{
                    "severity": "critical",
                    "issue": "The final PDF could not be reopened for validation.",
                    "detail": " | ".join(parser_errors)[:500],
                }],
            }
    rendered_text = "\n".join(page_text)

    if not pdf_bytes.startswith(b"%PDF-") or page_count < 1:
        findings.append({"severity": "critical", "issue": "The final renderer did not produce a valid non-empty PDF."})
    if len(re.sub(r"\s+", "", rendered_text)) < 120:
        findings.append({"severity": "critical", "issue": "The final PDF has too little extractable text to verify the published report."})
    nonempty_pages = sum(1 for text in page_text if len(re.sub(r"\s+", "", text)) >= 20)
    if page_count and nonempty_pages == 0:
        findings.append({"severity": "critical", "issue": "Every rendered PDF page is blank or textless."})

    # Citation identifiers must resolve to the source manifest delivered with
    # this export and remain visible in the actual PDF text layer.
    citation_ids = {sid.upper() for bracket_group in re.findall(
        r"(?<![A-Za-z0-9])\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]", str(report or ""), re.I
    ) for sid in re.findall(r"S\d+", bracket_group, re.I)}
    source_ids = {str(source.get("id") or "").strip().upper() for source in (sources or []) if isinstance(source, dict)}
    unresolved = sorted(citation_ids - source_ids)
    if unresolved:
        findings.append({"severity": "critical", "issue": "Report citation IDs do not resolve to the export source manifest.",
                         "sourceIds": unresolved[:20]})
    rendered_ids = {value.upper() for value in re.findall(r"\bS\d+\b", rendered_text, re.I)}
    missing_from_pdf = sorted(citation_ids - rendered_ids)
    if missing_from_pdf:
        findings.append({"severity": "critical", "issue": "Some report citations disappeared from the rendered PDF text layer.",
                         "sourceIds": missing_from_pdf[:20]})

    # The final PDF must also be checked for repeated numeric claims, because
    # different citation labels do not make a repeated metric/entity/period row
    # a new observation. Inspect the actual extracted page lines after rendering,
    # normalize source IDs and formatting, and fail only on exact repeated lines
    # that name a metric and include numeric evidence.
    metric_line = re.compile(
        r"(?i)\b(?:credit|deposits?|gross npa|net npa|gnpa|nnpa|non[- ]performing|"
        r"liabilit(?:y|ies)|assets?|net profit|profitability|net interest margin|nim|"
        r"repo rate|interest rate|upi|growth|ratio|capital adequacy|return on assets|"
        r"return on equity|transactions?)\b"
    )
    seen_numeric_lines: dict[str, int] = {}
    duplicate_numeric_lines: list[dict] = []
    for line_number, raw_line in enumerate(rendered_text.splitlines(), start=1):
        line = str(raw_line or "").strip()
        # A numbered heading/caption is often emitted twice by the HTML-first
        # pipeline: once as report prose and once as the title of the related
        # chart/table object. Its leading ordinal (e.g. "4.") is not numeric
        # evidence. Strip that ordinal before checking for a numeric observation
        # so headings like "4. Chart – Deposit vs. Credit Growth" do not trip
        # the final-PDF duplicate-observation gate. Keep a leading number out of
        # the normalized comparison too, so genuinely duplicated observations
        # still match even if they appear as differently numbered list items.
        observation_text = re.sub(r"^\s*\d+(?:\.\d+)*[.)]\s+", "", line, count=1)
        if len(line) < 24 or not metric_line.search(observation_text) or not re.search(r"\d", observation_text):
            continue
        normalized = re.sub(r"\[\s*S\d+(?:\s*[,;]\s*S\d+)*\s*\]", "", observation_text, flags=re.I)
        normalized = re.sub(r"\bS\d+\b", "", normalized, flags=re.I)
        normalized = re.sub(r"[^a-z0-9%₹$€£]+", " ", normalized.casefold()).strip()
        if not normalized:
            continue
        if normalized in seen_numeric_lines:
            duplicate_numeric_lines.append({
                "firstLine": seen_numeric_lines[normalized], "duplicateLine": line_number,
                "text": line[:260],
            })
        else:
            seen_numeric_lines[normalized] = line_number
    for item in duplicate_numeric_lines[:20]:
        findings.append({"severity": "critical",
                         "issue": "The final rendered PDF repeats the same numeric observation under different or repeated source attributions.",
                         **item})

    # Re-run the source-backed chart audit against the extracted text of the final
    # PDF. It is separate from pre-render validation because renderer/placeholder
    # cleanup can mutate what is actually delivered. When there are no charts,
    # there is nothing to audit here: skipping avoids turning a text-only export
    # into a failure because of an unrelated optional chart-audit path.
    if isinstance(charts, list) and charts:
        try:
            final_chart_audit = audit_chart_evidence_consistency(
                rendered_text, charts, sources, require_publisher_verification=True,
            )
            for item in (final_chart_audit.get("chartTableVerificationConflicts") or [])[:20]:
                findings.append({"severity": "critical", "issue": "Final PDF contains a chart/table evidence conflict.", **item})
            for item in (final_chart_audit.get("chartEvidenceFailures") or [])[:20]:
                findings.append({"severity": "critical", "issue": "Final PDF contains a chart value without verified publisher-page evidence.", **item})
        except Exception as exc:
            findings.append({"severity": "critical", "issue": "Final PDF chart-evidence validation failed unexpectedly.",
                             "detail": f"{type(exc).__name__}: {str(exc)[:180]}"})

    return {"status": "FAIL" if findings else "PASS", "blocking": bool(findings),
            "pageCount": page_count, "extractedTextChars": len(rendered_text),
            "nonemptyPages": nonempty_pages, "validationEngine": validation_engine,
            "findings": findings[:50]}




_DRAFT_BANNER_TEXT = (
    "> **DRAFT — NOT APPROVED FOR EXTERNAL PUBLICATION.** This report was generated, but unresolved "
    "source-evidence findings prevented publication approval. Review the flagged findings before relying on or sharing it."
)
_DRAFT_LINE_RE = re.compile(
    r"(?im)^[ \t]*>?[ \t]*\**[ \t]*(?:draft\b[^\n]{0,40}(?:not\s+approved|publication|review)|not\s+approved\s+for\s+(?:external\s+)?publication|"
    r"this\s+(?:report|draft)\s+(?:is|has)\s+not\s+(?:been\s+)?approved)[^\n]*$"
)


def _apply_single_draft_banner(report: str) -> str:
    """Put exactly one draft banner at the top; remove repeated draft warnings."""
    text = _DRAFT_LINE_RE.sub("", str(report or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return _DRAFT_BANNER_TEXT + "\n\n" + text


def _pdf_audit_is_structural_failure(pdf_bytes: bytes, audit: dict | None) -> bool:
    """A draft can carry evidence findings, but never a corrupt/empty file."""
    if not pdf_bytes or not bytes(pdf_bytes[:5]).startswith(b"%PDF-"):
        return True
    audit = audit if isinstance(audit, dict) else {}
    try:
        if int(audit.get("pageCount") or 0) <= 0:
            return True
        if int(audit.get("extractedTextChars") or 0) < 40:
            return True
    except (TypeError, ValueError):
        return True
    for finding in audit.get("findings") or []:
        issue = str((finding or {}).get("issue") or "") if isinstance(finding, dict) else str(finding)
        if re.search(r"(?i)could\s+not\s+be\s+reopened|corrupt|not\s+a\s+valid\s+pdf|no\s+pages|empty\s+pdf|unreadable", issue):
            return True
    return False


@router.post("")
async def generate_pdf(request: Request):
    t0 = time.perf_counter()
    try:
        body = await request.json()
    except Exception:
        log.warning("PDF: invalid request body")
        return JSONResponse({"error": "Invalid request body"}, status_code=400)

    report: str     = body.get("report", "")
    if not report:
        log.warning("PDF: no report content in request")
        return JSONResponse({"error": "No report content"}, status_code=400)

    # Defense-in-depth: routes/report.py's failure paths (all keys exhausted,
    # no sources for a live-data question, unparseable model response, etc.)
    # now return a non-2xx status so the frontend's `!r.ok` check catches
    # them before ever calling this endpoint — but a stale frontend build,
    # a direct API caller, or the email-report route could still forward one
    # of those sentinel error strings as if it were real report content.
    # Building a full branded PDF around "All LLM keys exhausted or
    # rate-limited. Try again in a minute." is exactly what produced the
    # broken-looking "Research Intelligence" PDF seen in production — reject
    # it here too instead of rendering it.
    _known_failure_messages = (
        "All LLM keys exhausted or rate-limited. Try again in a minute.",
        "Could not retrieve data for this topic. Please try again.",
        "Invalid request body.",
    )
    if report.strip() in _known_failure_messages or report.strip().startswith("## Report Generation Error"):
        log.warning("PDF: refusing to render known report-generation-failure sentinel as a PDF")
        return JSONResponse(
            {"error": "The report failed to generate, so there's nothing to export yet. Please try generating the report again."},
            status_code=422,
        )

    title: str      = body.get("title", "")
    question: str   = body.get("question", "Research Report")
    summary: str    = body.get("summary", "")
    key_stats: list = body.get("keyStats", [])
    charts: list    = body.get("charts", [])
    logo_b64: str   = body.get("logoB64", "")
    file_images: list = body.get("fileImages", [])  # [{name, mimeType, data}]
    images: list    = body.get("images", [])  # [{url, caption}] from report generation
    theme: dict | None = body.get("theme") if isinstance(body.get("theme"), dict) else None
    presentation = body.get("presentation")
    report_tier = str(body.get("reportTier") or "detailed").strip().lower()
    if report_tier not in ("brief", "detailed", "in_depth"):
        report_tier = "detailed"
    sources = normalise_source_manifest(body.get("sources", []))
    # Normalize the legacy image alias before fallback placement decides
    # whether selected source images already have explicit report positions.
    report = normalize_report_markdown(report)
    report = re.sub(r"\[IMAGE_(\d+)\]", r"[WEB_IMG_\1]", report, flags=re.IGNORECASE)

    # Debug: log images and whether report contains [WEB_IMG_n] placeholders
    import re as _re_dbg
    _web_img_in_report = _re_dbg.findall(r"\[WEB_IMG_\d+\]", report)
    log.info("PDF: images received=%d  [WEB_IMG] placeholders in report=%s", len(images), _web_img_in_report or "none")
    if images:
        for _ii, _img in enumerate(images[:4]):
            log.info("PDF: images[%d] url=%s caption=%r", _ii, str(_img.get("url",""))[:80] if isinstance(_img,dict) else type(_img), str(_img.get("caption",""))[:40] if isinstance(_img,dict) else "")

    # ── Safety: unwrap double-encoded report (LLM sometimes stuffs JSON into report field) ──
    stripped = report.strip()
    if stripped.startswith("{") and '"report"' in stripped:
        try:
            import json as _json
            inner = _json.loads(stripped, strict=False)
            if isinstance(inner.get("report"), str) and len(inner["report"]) > 100:
                log.warning("PDF: unwrapping double-encoded report field")
                report  = inner["report"]
                title   = title or inner.get("title", "")
                summary = summary or inner.get("summary", "")
                if not key_stats:
                    key_stats = inner.get("keyStats", [])
                if not charts:
                    charts = inner.get("charts", [])
                if not theme and isinstance(inner.get("theme"), dict):
                    theme = inner.get("theme")
                if not presentation and isinstance(inner.get("presentation"), dict):
                    presentation = inner.get("presentation")
        except Exception as e:
            log.debug("PDF: report field is not double-encoded JSON, using as-is (%s)", e)

    # Unescape any literal \n sequences
    if "\\n" in report:
        report = report.replace("\\n", "\n")

    # Strip accidental markdown fences
    report = re.sub(r"^```(?:json|markdown)?\s*", "", report.strip())
    report = re.sub(r"```\s*$", "", report).strip()
    # Re-run cleanup at the final renderer boundary after optional JSON unwrapping
    # and escaped-newline normalization. This must be the exact text measured and
    # emitted into the PDF, never an earlier pre-unwrapping representation.
    before_output_sanitize = report
    report = normalize_report_markdown(report)
    if report != before_output_sanitize:
        log.info("PDF: final report sanitation applied before rendering")
    residual_markup = bool(re.search(r"</?(?:div|strong|table|tr|td|p|section)\b", report, re.I))
    residual_placeholder = bool(re.search(
        r"(?i)not\s+verified\s+in\s+retrieved\s+publisher[\s\-‐‑–]*page\s+evidence", report
    ))
    if residual_markup or residual_placeholder:
        log.error("PDF: report text failed final sanitation (markup=%s placeholder=%s)", residual_markup, residual_placeholder)
        return JSONResponse({
            "error": "The report contains unresolved formatting or unsupported-metric placeholders. Regenerate the report before downloading the PDF.",
            "publicationStatus": "FAIL", "exported": False,
        }, status_code=422)

    log.info("PDF: generating — title=%r  tier=%s  report_chars=%d  report_words=%d  charts=%d  keyStats=%d",
             title[:60], report_tier, len(report), len(re.findall(r"\S+", report)), len(charts), len(key_stats))

    # Recoverable draft export: a report that was generated but not approved
    # for publication can still be downloaded for review. Its charts are kept
    # exactly as previewed (no export-time pruning, so preview and PDF match),
    # it carries one explicit draft banner, and structural PDF failures are
    # still refused below.
    draft_export = bool(body.get("draftExport")) or str(body.get("publicationOutcome") or "").casefold() == "draft"
    charts_received = len([c for c in (charts or []) if isinstance(c, dict)])
    if draft_export:
        report = _apply_single_draft_banner(report)

    # PDF chart rendering is intentionally offline-first. The HTML renderer already
    # carries the validated chart specification, and this module can draw charts
    # natively/SVG when PNG bytes are unavailable. Publishing/fetching Datawrapper
    # images during export added 30-120s of avoidable latency and was a major cause
    # of the frontend's "Backend unavailable" export failures. Only decode PNG
    # bytes that were already supplied with the report payload.
    # Self-heal instead of failing the whole export: drop only the charts/points
    # that lack source evidence (or are empty), renumber [CHART_n], then re-audit.
    # Unverified values are still never published; the report just ships without them.
    if draft_export:
        _chart_heal = {"removedCharts": 0, "prunedPoints": 0, "remainingFindings": []}
    else:
        report, charts, _chart_heal = sanitize_charts_for_publication(
            report, charts, sources, question=question, require_publisher_verification=True,
        )
    if _chart_heal["removedCharts"] or _chart_heal["prunedPoints"]:
        log.warning("PDF: export gate removed %d unsupported chart(s) and %d chart point(s) instead of failing; %d chart(s) kept",
                    _chart_heal["removedCharts"], _chart_heal["prunedPoints"], len(charts))
    chart_findings = _chart_heal["remainingFindings"]
    if chart_findings:
        return JSONResponse({
            "error": "This report contains chart values that conflict with verification status or lack supporting source evidence. Resolve the findings before export.",
            "publicationStatus": "FAIL", "findings": chart_findings[:20], "exported": False,
        }, status_code=422)

    async def _hydrate_charts(chs: list) -> list:
        n_png = 0
        for ch in chs or []:
            if not isinstance(ch, dict):
                continue
            dw = ch.get("datawrapper")
            if not isinstance(dw, dict):
                continue
            existing = dw.get("pngBytes")
            if isinstance(existing, str):
                try:
                    decoded = base64.b64decode(existing, validate=True)
                    if decoded[:4] == b"\x89PNG":
                        dw["pngBytes"] = decoded
                        n_png += 1
                    else:
                        dw["pngBytes"] = None
                except Exception:
                    dw["pngBytes"] = None
            elif isinstance(existing, (bytes, bytearray)) and bytes(existing[:4]) == b"\x89PNG":
                dw["pngBytes"] = bytes(existing)
                n_png += 1
            elif existing is not None:
                dw["pngBytes"] = None
        native = max(0, len(chs or []) - n_png)
        log.info("PDF: offline chart hydration — %d/%d supplied PNGs; %d using native/SVG renderer", n_png, len(chs or []), native)
        return chs

    try:
        charts = await _hydrate_charts(charts)
    except Exception as exc:
        log.warning("PDF: chart preparation failed, falling back to native charts: %s", exc)

    # The report step only returns {url, caption} for selected web images —
    # fetch the actual bytes now, right before rendering, same pattern as the
    # Datawrapper PNG hydration above. Each image fetch is independent and
    # failures are skipped rather than failing the whole PDF — a missing
    # photo just means one fewer [WEB_IMG_n] renders, nothing else degrades.
    async def _fetch_web_images(imgs: list, max_count: int = 6, max_bytes: int = 4_000_000) -> list[dict | None]:
        if not imgs:
            return []
        sem = asyncio.Semaphore(3)
        out: list[dict | None] = [None] * min(len(imgs), max_count)

        async def _one(i: int, info: dict):
            url = (info.get("url") or "").strip()
            if not url:
                return
            # AI-generated images (see report.py's _generate_ai_report_images) arrive
            # as inline data URIs, not fetchable HTTP links — decode directly and skip
            # the network round-trip entirely.
            if url.startswith("data:image"):
                try:
                    header, _, b64_payload = url.partition(",")
                    if not b64_payload:
                        log.warning("AI_IMG[%d] malformed data URI", i + 1)
                        return
                    content_bytes = base64.b64decode(b64_payload)
                    # Already-inline images may legitimately be small (icons,
                    # SVG/PNG marks, etc.). Remote downloads have a stricter
                    # minimum-size check below; here reject only empty/tiny junk.
                    if len(content_bytes) > max_bytes or len(content_bytes) < 16:
                        log.debug("AI_IMG[%d] size %d out of range", i + 1, len(content_bytes))
                        return
                    out[i] = {
                        "data": b64_payload,
                        "url": url,
                        "sourceUrl": info.get("sourceUrl") or info.get("sourceURL") or "",
                        "caption": _truncate_words(info.get("caption") or "", 120),
                        **{key: info[key] for key in (
                            "source", "sources", "sourceId", "sourceIds", "sourceTitle",
                            "publisher", "sourceType", "publicationDate", "publication_date",
                            "domain", "method", "methodology", "assetUrl",
                        ) if key in info},
                    }
                    log.info("AI_IMG[%d] decoded %d bytes from inline data URI", i + 1, len(content_bytes))
                except Exception as exc:
                    log.warning("AI_IMG[%d] data URI decode failed: %s", i + 1, exc)
                return
            async with sem:
                try:
                    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                        resp = await client.get(url, headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Referer": "https://growth-gradual.com/",
                        })
                    ctype = resp.headers.get("content-type", "")
                    if not resp.is_success:
                        log.debug("WEB_IMG HTTP %d for %s", resp.status_code, url[:80])
                        return
                    if not ctype.startswith("image/"):
                        # Some CDNs return image bytes with generic content-type — check magic bytes
                        content = resp.content
                        img_magic = (
                            content[:4] == b"\x89PNG" or
                            content[:3] == b"\xff\xd8\xff" or  # JPEG
                            content[:6] in (b"GIF87a", b"GIF89a") or
                            content[:4] == b"RIFF"  # WebP
                        )
                        if not img_magic:
                            log.debug("WEB_IMG non-image content-type %r for %s", ctype[:40], url[:80])
                            return
                        content_bytes = content
                    else:
                        content_bytes = resp.content
                    if not _image_bytes_look_editorial(content_bytes):
                        log.info("WEB_IMG[%d] skipped: icon, banner or unreadable image (%s)", i + 1, url[:80])
                        return
                    if len(content_bytes) > max_bytes or len(content_bytes) < 500:
                        log.debug("WEB_IMG size %d out of range for %s", len(content_bytes), url[:80])
                        return
                    out[i] = {
                        "data": base64.b64encode(content_bytes).decode("ascii"),
                        "url": url,
                        # `url` is the image file, not the publisher page.
                        # Never promote it to sourceUrl; attribution must use a
                        # separately verified publisher page or a registered source ID.
                        "sourceUrl": info.get("sourceUrl") or info.get("sourceURL") or "",
                        "caption": _truncate_words(info.get("caption") or "", 120),
                        **{key: info[key] for key in (
                            "source", "sources", "sourceId", "sourceIds", "sourceTitle",
                            "publisher", "sourceType", "publicationDate", "publication_date",
                            "domain", "method", "methodology",
                        ) if key in info},
                    }
                    log.info("WEB_IMG[%d] fetched %d bytes from %s", i + 1, len(content_bytes), url[:60])
                except Exception as exc:
                    log.warning("WEB_IMG[%d] fetch failed for %s: %s", i + 1, url[:80], exc)

        # httpx timeouts are per network operation, so a slow trickle download
        # (seen: 3 minutes for one image) never trips them. Bound each image and
        # the whole step; a slow image is skipped, never the PDF.
        async def _bounded(i: int, info: dict):
            try:
                await asyncio.wait_for(_one(i, info), timeout=float(os.getenv("PDF_IMAGE_FETCH_TIMEOUT", "10")))
            except asyncio.TimeoutError:
                log.warning("WEB_IMG[%d] fetch exceeded the per-image limit; skipped", i + 1)
        try:
            await asyncio.wait_for(
                asyncio.gather(*[_bounded(i, info) for i, info in enumerate(imgs[:max_count])]),
                timeout=float(os.getenv("PDF_IMAGES_TOTAL_TIMEOUT", "15")),
            )
        except asyncio.TimeoutError:
            log.warning("PDF: web image step exceeded its total limit; continuing with images fetched so far")
        # IMPORTANT: don't compact `out` by dropping the None gaps left by failed
        # fetches — report_text already has [WEB_IMG_n] placeholders baked in
        # against the ORIGINAL 1-based image order. Compacting would shift every
        # placeholder after a failed fetch onto the wrong photo/caption (or, if
        # the count shrinks below a later placeholder's index, silently drop a
        # perfectly good image instead of the one that actually failed). The
        # renderer in build_pdf already treats a None/missing entry as "skip
        # this placeholder" via its try/except, so leaving the gaps is safe.
        n_ok = sum(1 for img in out if img)
        if n_ok:
            log.info("PDF: fetched %d/%d web images", n_ok, len(out))
        return out

    try:
        web_images = await _fetch_web_images(images)
    except Exception as exc:
        log.warning("PDF: web image fetch failed entirely, continuing without images: %s", exc)
        web_images = []

    # Fallback: if no [WEB_IMG_n] placeholders in report but we have fetched images, inject them
    existing_ph = re.findall(r"\[WEB_IMG_\d+\]", report)
    fetched_imgs = [img for img in web_images if img]
    if fetched_imgs and not existing_ph:
        log.info("PDF: no [WEB_IMG_n] placeholders — injecting fallback for %d image(s)", len(fetched_imgs))
        paragraphs = report.split("\n\n")
        n_para = len(paragraphs)
        if len(fetched_imgs) == 1:
            insertion_points = [max(1, n_para // 4)]
        else:
            step = max(1, n_para // (len(fetched_imgs) + 1))
            insertion_points = [step * (k + 1) for k in range(len(fetched_imgs))]
        for img_idx, para_idx in reversed(list(enumerate(insertion_points))):
            if img_idx < len(web_images) and web_images[img_idx]:
                paragraphs.insert(min(para_idx, n_para - 1), f"\n\n[WEB_IMG_{img_idx + 1}]\n")
        report = "\n\n".join(paragraphs)
        log.info("PDF: injected fallback [WEB_IMG_n] placeholders")

    try:
        pdf_bytes = build_pdf(
            report, title, question, summary, key_stats, charts, logo_b64,
            file_images, web_images, theme, sources, presentation,
            report_tier=report_tier,
        )
    except Exception as e:
        import traceback as _tb
        log.error("PDF: build_pdf failed: %s\n%s", e, _tb.format_exc())
        return JSONResponse({"error": f"Failed to generate PDF: {e}"}, status_code=500)

    pdf_bytes = _trim_trailing_blank_pages(pdf_bytes)
    pdf_bytes = _trim_furniture_only_middle_pages(pdf_bytes)
    _rendered_pdf_audit = _audit_rendered_pdf_bytes(pdf_bytes, report, title, charts, sources)

    if draft_export:
        structural = _pdf_audit_is_structural_failure(pdf_bytes, _rendered_pdf_audit)
        log.info("PDF chart stages: received=%d exported=%d draft=true structural_failure=%s audit=%s",
                 charts_received, len(charts or []), structural, _rendered_pdf_audit.get("status"))
        if structural:
            _first = next((str(f.get("issue") or "") for f in (_rendered_pdf_audit.get("findings") or []) if isinstance(f, dict)), "")
            return JSONResponse({
                "error": "The draft PDF could not be produced as a readable document. " + (_first or "The rendered file was empty or unreadable."),
                "publicationStatus": "FAIL", "publicationOutcome": "structural_failure", "failedStage": "pdf_export",
                "exported": False, "renderedPdfAudit": _rendered_pdf_audit,
            }, status_code=422)
        date_filename = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="growth-gradual-report-DRAFT-{date_filename}.pdf"',
                "Cache-Control": "no-store",
                "X-GG-Publication-Status": "DRAFT",
                "X-GG-Charts-Exported": str(len(charts or [])),
                "X-GG-Audit-Findings": str(len(_rendered_pdf_audit.get("findings") or [])),
            },
        )

    # Renderer-specific text extraction can expose a chart point that did not fail
    # the pre-render gate. Repair only the implicated chart/point and render once
    # more; publication checks stay enabled and the replacement PDF must pass the
    # same complete final-byte audit. Never suppress an unresolved finding.
    if _rendered_pdf_audit.get("blocking"):
        _audit_findings = list(_rendered_pdf_audit.get("findings") or [])
        _repaired_report, _repaired_charts, _repair_meta = _repair_rendered_chart_audit(
            report, charts, _audit_findings,
        )
        if _repair_meta.get("changed"):
            log.warning(
                "PDF: rendered-PDF audit triggered bounded chart repair; removed charts=%d pruned points=%d",
                _repair_meta.get("removedCharts", 0), _repair_meta.get("prunedPoints", 0),
            )
            report = normalize_report_markdown(_repaired_report)
            charts = _repaired_charts
            try:
                pdf_bytes = build_pdf(
                    report, title, question, summary, key_stats, charts, logo_b64,
                    file_images, web_images, theme, sources, presentation,
                    report_tier=report_tier,
                )
                pdf_bytes = _trim_trailing_blank_pages(pdf_bytes)
                pdf_bytes = _trim_furniture_only_middle_pages(pdf_bytes)
                _rendered_pdf_audit = _audit_rendered_pdf_bytes(pdf_bytes, report, title, charts, sources)
                log.info(
                    "PDF: rendered-PDF audit after bounded chart repair status=%s findings=%d",
                    _rendered_pdf_audit.get("status"), len(_rendered_pdf_audit.get("findings") or []),
                )
            except Exception as _repair_render_exc:
                log.error("PDF: bounded chart repair render failed: %s", type(_repair_render_exc).__name__)

    # Second safe repair: if the PDF audit found an exact duplicate numeric
    # observation and that duplicate can be matched to a full Markdown line,
    # remove only the repeated line, re-render and re-run the complete audit.
    # If the duplicate is only a renderer/source-appendix artifact, no edit is
    # made and the publication gate remains blocking.
    if _rendered_pdf_audit.get("blocking"):
        _duplicate_repair_report, _duplicate_lines_removed = _remove_exact_duplicate_observation_lines(
            report, list(_rendered_pdf_audit.get("findings") or []),
        )
        if _duplicate_lines_removed:
            log.warning("PDF: rendered-PDF audit identified %d exact duplicate report observation line(s); repairing and re-rendering", _duplicate_lines_removed)
            report = normalize_report_markdown(_duplicate_repair_report)
            try:
                pdf_bytes = build_pdf(
                    report, title, question, summary, key_stats, charts, logo_b64,
                    file_images, web_images, theme, sources, presentation,
                    report_tier=report_tier,
                )
                pdf_bytes = _trim_trailing_blank_pages(pdf_bytes)
                pdf_bytes = _trim_furniture_only_middle_pages(pdf_bytes)
                _rendered_pdf_audit = _audit_rendered_pdf_bytes(pdf_bytes, report, title, charts, sources)
                log.info(
                    "PDF: rendered-PDF audit after duplicate-line repair status=%s findings=%d",
                    _rendered_pdf_audit.get("status"), len(_rendered_pdf_audit.get("findings") or []),
                )
            except Exception as _duplicate_repair_exc:
                log.error("PDF: duplicate-line repair render failed: %s", type(_duplicate_repair_exc).__name__)

    if _rendered_pdf_audit.get("blocking"):
        _audit_findings = list(_rendered_pdf_audit.get("findings") or [])[:20]
        for _finding in _audit_findings:
            log.error(
                "PDF: final rendered-PDF audit finding severity=%s issue=%s details=%s",
                _finding.get("severity", "critical"), str(_finding.get("issue") or "unknown")[:220],
                json.dumps({k: v for k, v in _finding.items() if k not in {"severity", "issue"}}, ensure_ascii=False, default=str)[:500],
            )
        _first_issue = str((_audit_findings[0] or {}).get("issue") or "unresolved integrity/evidence finding") if _audit_findings else "unresolved integrity/evidence finding"
        return JSONResponse({
            "error": "The rendered PDF still fails final integrity/evidence validation after a safe repair attempt. " + _first_issue,
            "publicationStatus": "FAIL", "exported": False,
            "renderedPdfAudit": _rendered_pdf_audit,
            "findings": _audit_findings,
        }, status_code=422)
    elapsed = (time.perf_counter() - t0) * 1000
    log.info("PDF: done — %.1f KB in %.0fms; final audit=PASS pages=%d extracted_text=%d chars", len(pdf_bytes) / 1024, elapsed,
             _rendered_pdf_audit.get("pageCount", 0), _rendered_pdf_audit.get("extractedTextChars", 0))
    log.info("PDF chart stages: received=%d after_export_gate=%d exported=%d", charts_received,
             charts_received - int(_chart_heal.get("removedCharts") or 0), len(charts or []))

    date_filename = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="growth-gradual-report-{date_filename}.pdf"',
            "Cache-Control": "no-store",
            "X-GG-Publication-Status": "PUBLISHED",
            "X-GG-Charts-Exported": str(len(charts or [])),
        },
    )
