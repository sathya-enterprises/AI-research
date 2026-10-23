"""Client house styles for exported reports.

Two selectable styles reproduce the client's reference documents:

* ``market_currents`` — Growth Gradual LLP's branded monthly-intelligence look
  (navy cover with logo and headline numbers, beige pages, "SECTION 01"
  headings, KPI cards, navy Desk View callouts, dated timelines, navy-header
  tables with signed badges, ARN footer and closing disclaimer).
* ``feature_article`` — a long-read editorial feature (kicker, large headline,
  hero image, standfirst, date and read time, narrative prose, infographic
  charts).

A style is a layer over the shared HTML/PDF renderer: it changes the writer
instructions, the cover, the section heading treatment, CSS and print page
furniture. Charts, citations, tables and every evidence gate are unchanged.
``None`` keeps the original editorial look for stored reports and old callers.
"""
from __future__ import annotations

import base64
import html
import os
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

REPORT_STYLES: tuple[str, ...] = ("market_currents", "feature_article")
DEFAULT_REPORT_STYLE = "market_currents"

FIRM_NAME = "Growth Gradual LLP"


def brand_arn() -> str:
    return os.getenv("REPORT_BRAND_ARN", "ARN-351151, Validity Jan'29").strip()


def normalize_report_style(value: object, default: str | None = None) -> str | None:
    key = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    aliases = {"market": "market_currents", "currents": "market_currents", "house": "market_currents",
               "feature": "feature_article", "article": "feature_article", "editorial_feature": "feature_article"}
    key = aliases.get(key, key)
    return key if key in REPORT_STYLES else default


# ── monthly market review ───────────────────────────────────────────────────
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"]
# Full month names or their standard abbreviations only, so "Mar" never
# matches "Market"; "May" counts only when capitalised (not the modal verb).
_MONTH_RE = re.compile(
    r"\b(" + "|".join(sorted({n for m in _MONTHS for n in (m, m[:3])} | {"Sept"}, key=len, reverse=True))
    + r")\b\.?(?:\s*['’]?\s*(\d{4}|\d{2})\b)?",
    re.I,
)


def _month_match(text: str) -> re.Match | None:
    for match in _MONTH_RE.finditer(text):
        if match.group(1).lower() != "may" or match.group(1) == "May":
            return match
    return None


_REVIEW_WORDS = r"(?:review|report|recap|wrap|wrap-?up|update|round-?up|summary|outlook|currents|commentary)"


def is_monthly_market_review(text: object) -> bool:
    """A request for a whole-market review of one month (the Market Currents format)."""
    value = str(text or "")
    if re.search(r"\bmarket\s+currents\b", value, re.I):
        return True
    if not re.search(r"\bmarkets?\b", value, re.I) or not re.search(rf"\b{_REVIEW_WORDS}\b", value, re.I):
        return False
    return bool(re.search(r"\bmonth(?:ly)?\b|\blast\s+month\b|\bthis\s+month\b", value, re.I) or _month_match(value))


def review_period(text: object, today: date | None = None) -> tuple[str, int]:
    """(Month name, year) the review covers: a month named in the request, else
    the last completed calendar month."""
    today = today or date.today()
    match = _month_match(str(text or ""))
    if match:
        month_index = [m[:3].lower() for m in _MONTHS].index(match.group(1)[:3].lower())
        year_text = match.group(2)
        if year_text:
            year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        else:
            year = today.year if month_index + 1 <= today.month else today.year - 1
        return _MONTHS[month_index], year
    month, year = (12, today.year - 1) if today.month == 1 else (today.month - 1, today.year)
    return _MONTHS[month - 1], year


def running_title(style: str | None, title: object, question: object, today: date | None = None) -> str:
    if style == "market_currents" and is_monthly_market_review(f"{question} {title}"):
        month, year = review_period(f"{question} {title}", today)
        return f"Market Currents – {month[:3]}’{str(year)[-2:]}"
    text = re.sub(r"\s+", " ", str(title or question or "Research Report")).strip()
    for separator in (" — ", " – ", " | ", ": "):
        if separator in text and len(text.split(separator, 1)[0]) >= 18:
            text = text.split(separator, 1)[0]
            break
    return text if len(text) <= 60 else " ".join(text.split()[:8]) + "…"


MONTHLY_REVIEW_SECTIONS: list[tuple[str, str]] = [
    ("Executive Summary",
     "Open with a 2–4 sentence overview of the month. Then a `> **Desk View:**` callout. Then `### What Mattered Most in {month}` as a numbered list of exactly four items, each opening with a bold one-line takeaway followed by one or two sentences of evidence."),
    ("Index Performance — The Month in One Chart",
     "One lead paragraph on the benchmark's path through the month. A line chart of the Nifty 50 (and Sensex if sourced) at the key sessions, with the session labels as dates. Then `### Benchmark Levels at Key Sessions` as a table: Date | Event | Nifty 50 | Sensex."),
    ("Macro & Geopolitical Backdrop",
     "`### Timeline of the Month` as a dated list (`- **Jun 5** — event`). Then `### RBI Policy Snapshot` as a table of policy rate, growth and inflation forecasts and other sourced macro markers. Close with a `> **Why This Matters for Your Portfolio:**` callout."),
    ("Sectoral Performance",
     "One lead paragraph. A horizontal bar chart of monthly return by sectoral index (%). Then `### Month-End Sector Trend Board` as a table: Sector | Trend, where Trend starts with ▲, ▼ or ■ followed by one word (Firm, Steady, Mixed, Soft, Weak). Close with a `> **Sector Lens:**` callout."),
    ("Institutional Flows — FII vs DII",
     "One lead paragraph. A bar chart of FII net flows by month (₹ crore). `### Where FIIs Were Actually Buying` (sector evidence). `### Promoters Are Voting With Their Own Cash` as a table: Company | Action | Detail, if buybacks/promoter buying are sourced. Close with a `> **Desk View:**` callout."),
    ("International Market Action",
     "Subsections `### United States`, `### Asia`, `### Europe`, each with sourced index closes/returns and the month's key events (dated list where useful). Close with a `> **Why This Matters for Your Portfolio:**` callout."),
    ("Broader Market & Primary Market (IPO) Activity",
     "`### Broader Market Snapshot` (mid/small-cap returns, breadth). `### {month} {year} IPO Scorecard`: count of listings and how many gained, and tables of top listing-day gainers and losers with signed % returns. Close with a `> **Desk View:**` callout."),
    ("Currency, Commodities & the Year-to-Date Scoreboard",
     "`### Currency & Commodities` (USD/INR, Brent crude, gold) with one interpretive paragraph. `### {year} Scoreboard — Where {month} Fits In` as a table: Metric | Year to date | Context. Close with a `> **Bigger Picture:**` callout."),
    ("Policy, Trade & Theme of the Month",
     "The month's most consequential policy, trade or structural theme for Indian markets (for example trade agreements, regulatory changes, a sector policy). Use a dated list for milestones and a table for figures. Close with a `> **Sector & Portfolio Read-Through:**` callout."),
    ("Outlook & Strategic Takeaways",
     "One lead paragraph naming the swing factors for next month. `### Key Watch-Points for {next_month} {next_year}` as bullets each opening with a bold label. `### Positioning Considerations for HNI Portfolios` as bullets. End with a `> **Closing Note from the Desk:**` callout."),
]

_BRIEF_MONTHLY_SECTIONS = [
    ("Executive Summary", "Overview, a `> **Desk View:**` callout and a numbered 'What Mattered Most' list."),
    ("Markets, Sectors & Flows", "Index path, sector returns and FII/DII flows with one chart and one table."),
    ("Macro, Global & Commodities", "Dated timeline of the month's macro/global events and a currency/commodity table."),
    ("Outlook & Strategic Takeaways", "Watch-points and positioning, ending with a `> **Closing Note from the Desk:**` callout."),
]


def _monthly_section_block(report_tier: str, month: str, year: int) -> str:
    month_index = _MONTHS.index(month)
    next_month = _MONTHS[(month_index + 1) % 12]
    next_year = year + (1 if month_index == 11 else 0)
    sections = _BRIEF_MONTHLY_SECTIONS if report_tier == "brief" else MONTHLY_REVIEW_SECTIONS
    lines = []
    for number, (heading, guidance) in enumerate(sections, 1):
        guidance = guidance.format(month=month, year=year, next_month=next_month, next_year=next_year)
        lines.append(f"{number}. `## {heading}` — {guidance}")
    return "\n".join(lines)


def writer_style_contract(style: str | None, question: str, report_tier: str, today: date | None = None) -> str:
    """Prompt block describing the selected house style to the report writer."""
    if style == "market_currents":
        common = (
            "\n\nHOUSE STYLE — GROWTH GRADUAL LLP 'MARKET CURRENTS' (client-branded research for HNI clients):\n"
            "- Voice: an institutional research desk briefing its private clients — measured, specific, evidence-led; no hype, no first-person singular.\n"
            "- Each `##` section opens with one short lead paragraph (2–4 sentences) before any subheading, list, table or chart.\n"
            "- Use `###` subheadings for named sub-topics inside a section.\n"
            "- Callouts: write a desk callout as ONE blockquote line `> **Desk View:** text` (other labels: Why This Matters for Your Portfolio, Sector Lens, Bigger Picture, Sector & Portfolio Read-Through, Closing Note from the Desk). At most one callout per section; never put a citation-free claim in it.\n"
            "- Dated events: a bullet list where every item starts with the bold date, e.g. `- **Jun 11** — The US launches fresh strikes… [S3]`. These render as a timeline.\n"
            "- Tables: keep signed changes with an explicit sign (`+6.41%`, `-9.56%`) so they render as green/red badges; trend columns may start with ▲, ▼ or ■.\n"
            "- keyStats: 4–8 headline figures. label in the form `NIFTY 50 (CLOSE)`, value like `23,865.75`, change like `+1.67% MoM` or a 3–6 word context note. The first four appear on the cover, so lead with the most important.\n"
            "- Do not write a disclaimer, sign-off or 'About Growth Gradual' section; the renderer adds the firm's disclaimer.\n"
        )
        if is_monthly_market_review(question):
            month, year = review_period(question, today)
            common += (
                f"\nMONTHLY REVIEW STRUCTURE (mandatory; this overrides the planner's section list and the instruction not to impose a fixed template):\n"
                f"This is the Market Currents review for {month} {year}. Title the report exactly `India : Market Currents — {month} {year}`. "
                f"Write these `##` sections in this order with these exact headings, covering {month} {year} only (use prior months only as comparison):\n"
                + _monthly_section_block(report_tier, month, year)
                + "\nIf the evidence cannot support part of a section, keep the section, say briefly what was not available, and do not invent figures.\n"
            )
        return common
    if style == "feature_article":
        return (
            "\n\nHOUSE STYLE — LONG-READ FEATURE ARTICLE (magazine-style explanatory journalism):\n"
            "- title: a sharp headline, at most 14 words; it may be a short sourced quotation in single quotes if a named person in the sources said it.\n"
            "- summary: the standfirst — one or two sentences under the headline that state the tension and the argument.\n"
            "- kicker: add a top-level JSON field `kicker` with a 2–4 word label for the story (e.g. `A HARD SELL`).\n"
            "- Structure: narrative, not a research memo. No 'Executive Summary', 'Introduction', 'Conclusion' or 'Key Findings' headings. "
            "The first `##` section has a short evocative heading and opens with a concrete lede (a recent event, number or scene) before stepping back to the bigger question. "
            "Later `##` headings are short phrases that advance the argument (e.g. 'Less about AI, more about the rupee').\n"
            "- Paragraphs: 2–5 sentences, flowing prose with transitions. Use bullet lists and tables sparingly — only where a reader needs exact figures side by side.\n"
            "- Quotes: quote named people only verbatim from the supplied sources, attributed in the sentence ('said Sanjeev Prasad, managing director at …') with the citation. Never invent or paraphrase into quotation marks. A single standout quote may be set as a pull quote: `> “quote” — Name, Role`.\n"
            "- Charts: 1–3 explanatory charts placed exactly where the prose discusses them; each chart title is a short punchy phrase (e.g. 'Still pricier') and its caption/subtitle states the finding.\n"
            "- keyStats: optional; at most four, used only if a figure anchors the story.\n"
            "- End with a closing paragraph that looks ahead or returns to the opening tension; no summary bullet list.\n"
        )
    return ""


# ── rendering ───────────────────────────────────────────────────────────────
MARKET_THEME = {
    "primaryColor": "#0E1F3D", "accentColor": "#B8873A", "surfaceColor": "#FBF9F5",
    "surfaceAltColor": "#F4F0E8", "textColor": "#1E2A3A", "mutedColor": "#5B6372",
    "borderColor": "#E2DCD0", "fontFamily": "",
    "chartColors": ["#0E1F3D", "#B8873A", "#1E7A4C", "#B5382C", "#4F6D8F", "#7A8B5A"],
    "barSignColors": {"pos": "#1E7A4C", "neg": "#B5382C"},
}
FEATURE_THEME = {
    "primaryColor": "#1F1F1F", "accentColor": "#C0392B", "surfaceColor": "#FFFFFF",
    "surfaceAltColor": "#F4F1EC", "textColor": "#1F1F1F", "mutedColor": "#5E5A55",
    "borderColor": "#DDD8D0", "fontFamily": "",
    "chartColors": ["#F5A11C", "#3B82F6", "#1F1F1F", "#C0392B", "#2E9E6E", "#8E6BC7"],
}


def house_theme(style: str | None) -> dict:
    return dict(MARKET_THEME if style == "market_currents" else FEATURE_THEME if style == "feature_article" else {})


def house_style_from_html(html_doc: str) -> str | None:
    match = re.search(r'<body[^>]*\bdata-report-style=["\']([a-z_]+)', html_doc or "", re.I)
    return normalize_report_style(match.group(1)) if match else None


@lru_cache(maxsize=1)
def logo_data_uri() -> str:
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "frontend" / "public" / "growth-gradual-logo.jpg",
                      Path(__file__).resolve().parents[1] / "assets" / "growth-gradual-logo.jpg"):
        if candidate.exists():
            return "data:image/jpeg;base64," + base64.b64encode(candidate.read_bytes()).decode("ascii")
    return ""


def _plain(text: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\[S\d+\]", "", str(text or ""))).strip()


def _first_sentences(text: object, limit: int = 240) -> str:
    value = _plain(text)
    if len(value) <= limit:
        return value
    cut = value[:limit]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:stop + 1] if stop > 80 else cut.rsplit(" ", 1)[0] + "…").strip()


def _sign_class(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text.startswith(("-", "−", "–", "▼")):
            return "neg"
        if text.startswith(("+", "▲")):
            return "pos"
    return "flat"


def render_house_cover(style: str | None, *, title: str, summary: str, question: str, date_str: str,
                       key_stats: list, report_text: str = "", images: list | None = None,
                       kicker: str = "", today: date | None = None) -> str:
    if style == "market_currents":
        monthly = is_monthly_market_review(f"{question} {title}")
        if monthly:
            month, year = review_period(f"{question} {title}", today)
            eyebrow, heading, period = "Monthly Market Intelligence", "India : Market Currents", f"{month} {year}"
            dek = f"A curated review of equity, currency and commodity market action for the month prepared exclusively for clients of <b>{FIRM_NAME}</b>."
        else:
            eyebrow, heading, period = "Research Intelligence", html.escape(_plain(title) or "Research Report"), date_str
            dek = html.escape(_first_sentences(summary)) if summary else f"Prepared exclusively for clients of <b>{FIRM_NAME}</b>."
        tiles = []
        for stat in [s for s in (key_stats or []) if isinstance(s, dict) and str(s.get("value") or "").strip()][:4]:
            tone = _sign_class(stat.get("change"), stat.get("value"))
            label = " · ".join(x for x in (_plain(stat.get("label")), _plain(stat.get("change"))) if x)
            tiles.append(f'<div class="gg-house-cover__stat"><div class="gg-house-cover__stat-value gg-tone--{tone}">{html.escape(_plain(stat.get("value")))}</div>'
                         f'<div class="gg-house-cover__stat-label">{html.escape(label)}</div></div>')
        logo = logo_data_uri()
        logo_html = f'<div class="gg-house-cover__logo"><img src="{logo}" alt="{FIRM_NAME}" /></div>' if logo else ""
        return (
            '<header id="report-cover" class="gg-cover gg-house-cover">'
            f'{logo_html}<p class="gg-house-cover__kicker">{eyebrow}</p>'
            f'<h1 class="gg-title gg-house-cover__title">{heading}</h1>'
            f'<p class="gg-house-cover__period">{html.escape(period)}</p>'
            f'<p class="gg-house-cover__dek">{dek}</p>'
            + (f'<div class="gg-house-cover__stats">{"".join(tiles)}</div>' if tiles else "")
            + '</header>'
        )
    if style == "feature_article":
        words = len(re.findall(r"\w+", _plain(report_text)))
        minutes = max(1, round(words / 220))
        hero = ""
        first = (images or [None])[0]
        if isinstance(first, dict) and first.get("url"):
            caption = _plain(first.get("caption") or "")
            hero = (f'<figure class="gg-feature-hero"><img src="{html.escape(str(first["url"]), quote=True)}" alt="{html.escape(caption, quote=True)}" />'
                    + (f'<figcaption>{html.escape(caption)}</figcaption>' if caption else "") + '</figure>')
        kicker_text = _plain(kicker) or "Feature"
        return (
            '<header id="report-cover" class="gg-cover gg-feature-head">'
            f'<p class="gg-feature-kicker">{html.escape(kicker_text)}</p>'
            f'<h1 class="gg-title gg-feature-title">{html.escape(_plain(title) or "Untitled feature")}</h1>'
            f'{hero}'
            + (f'<p class="gg-feature-standfirst">{html.escape(_plain(summary))}</p>' if summary else "")
            + f'<p class="gg-feature-meta">{html.escape(date_str)} &nbsp;/&nbsp; {minutes} min read &nbsp;·&nbsp; {FIRM_NAME}</p>'
            '</header>'
        )
    return ""


_CALLOUT_RE = re.compile(
    r'<blockquote class="gg-reveal gg-pullquote" data-reveal>\s*<strong>([^<]{2,70}?)\s*:?\s*</strong>\s*[:—–-]?\s*(.*?)</blockquote>',
    re.S,
)
_LIST_RE = re.compile(r'<ul class="gg-reveal gg-list" data-reveal>(.*?)</ul>', re.S)
_ITEM_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
_DATE_LABEL_RE = re.compile(
    r"^(?:(?:\d{1,2}\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s+\d{1,2})?"
    r"(?:\s*[-–—]\s*(?:\d{1,2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+\d{1,2})?))?(?:,?\s*['’]?\d{2,4})?"
    r"(?:\s*\([^)]{1,16}\))?"
    r"|(?:q[1-4]|h[12])\s*(?:fy)?\s*['’]?\d{2,4}|fy\s*['’]?\d{2,4}|\d{4}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
    r"|(?:through|by|mid|early|late|end|week of)[\s-][\w\s’']{1,14})$",
    re.I,
)
_SIGNED_CELL_RE = re.compile(r'(<td class="[^"]*">)\s*([+\-−–]\s?[₹$]?\d[\d,]*(?:\.\d+)?\s?(?:%|pp|bps|x)?|[▲▼■][^<]{0,24})\s*(</td>)')


def _timeline(match: re.Match) -> str:
    items = _ITEM_RE.findall(match.group(1))
    parsed = []
    for item in items:
        lead = re.match(r"\s*<strong>([^<]{1,32})</strong>\s*[:—–-]?\s*(.*)$", item, re.S)
        if not lead or not _DATE_LABEL_RE.match(lead.group(1).strip()):
            return match.group(0)
        parsed.append((lead.group(1).strip(), lead.group(2).strip()))
    if len(parsed) < 2:
        return match.group(0)
    rows = "".join(
        f'<li><span class="gg-house-timeline__date">{date_label}</span><span class="gg-house-timeline__dot" aria-hidden="true"></span>'
        f'<div class="gg-house-timeline__text">{text}</div></li>'
        for date_label, text in parsed
    )
    return f'<ol class="gg-house-timeline">{rows}</ol>'


def _badge(match: re.Match) -> str:
    value = match.group(2).strip()
    return f'{match.group(1)}<span class="gg-badge gg-badge--{_sign_class(value)}">{value}</span>{match.group(3)}'


def transform_section_html(style: str | None, body_html: str) -> str:
    """Turn labelled blockquotes into callouts, dated bullet lists into
    timelines and signed table cells into badges for a house style."""
    if not style or not body_html:
        return body_html
    body_html = _CALLOUT_RE.sub(
        lambda m: (f'<aside class="gg-house-callout"><div class="gg-house-callout__kicker">{m.group(1).strip()}</div>'
                   f'<p>{m.group(2).strip()}</p></aside>'),
        body_html,
    )
    body_html = _LIST_RE.sub(_timeline, body_html)
    if style == "market_currents":
        body_html = _SIGNED_CELL_RE.sub(_badge, body_html)
    return body_html


def house_closing(style: str | None) -> str:
    if style != "market_currents":
        return ""
    return (
        '<section class="gg-house-disclaimer" aria-label="Disclaimer">'
        f'<p><strong>Disclaimer:</strong> This report has been prepared by the Research Desk of {FIRM_NAME} for private circulation to its clients only, '
        'and is not intended for public distribution or as an offer or solicitation to buy or sell any security. Data and figures referenced in this report '
        'have been compiled from publicly available sources, including those listed in this report, and while believed to be reliable, have not been '
        f'independently verified by {FIRM_NAME}; no guarantee, representation or warranty, express or implied, is made as to their accuracy, completeness '
        'or correctness. Figures marked with an asterisk (*) are indicative estimates derived from reported changes rather than directly reported values. '
        'The intent of this report is not recommendatory in nature. All information and opinions are subject to change without notice. The report is '
        'prepared solely for informational purposes and does not constitute an offer document or a solicitation of an offer to buy, sell or subscribe for '
        'securities or other financial instruments. Though disseminated to all clients simultaneously, not all clients may receive this report at the same time.</p>'
        '<p>Investments in securities markets are subject to market risks; please read all scheme-related and offer documents carefully before investing. '
        'Past performance is not indicative of future results. This document does not constitute investment advice, and clients should consult their '
        'relationship manager or a SEBI-registered investment adviser before making any investment decisions. '
        f'{FIRM_NAME}, its partners and employees shall not be liable for any direct or indirect loss arising from the use of this report.</p>'
        '</section>'
    )


_MARKET_CSS = r"""
body.gg-house--market_currents {
  --mc-navy: #0E1F3D; --mc-gold: #C49A4A; --mc-gold-soft: #D9B77A; --mc-paper: #F4F0E8; --mc-card: #FAF8F3;
  --mc-rule: #E2DCD0; --mc-ink: #1E2A3A; --mc-muted: #5B6372; --mc-green: #1E7A4C; --mc-red: #B5382C; --mc-bronze: #B8873A;
  --mc-serif: 'Lora', Georgia, 'Times New Roman', serif; --mc-sans: 'Inter', 'Segoe UI', Arial, sans-serif;
  background: var(--mc-paper) !important; color: var(--mc-ink) !important;
}
body.gg-house--market_currents main { background: transparent !important; }
body.gg-house--market_currents main, body.gg-house--market_currents .gg-section-body p,
body.gg-house--market_currents .gg-section-body li { font-family: var(--mc-serif) !important; color: #222 !important; line-height: 1.65 !important; }
body.gg-house--market_currents .gg-report-footer { display: none !important; }

/* cover */
body.gg-house--market_currents .gg-house-cover {
  background-color: var(--mc-navy) !important; background-image: radial-gradient(rgba(255,255,255,.045) 1px, transparent 1.2px) !important;
  background-size: 4px 4px !important; color: #fff !important; display: flex !important; flex-direction: column !important;
  min-height: 96vh; padding: 56px 52px 60px !important; margin: 0 !important; border: 0 !important; box-shadow: none !important;
}
body.gg-house--market_currents .gg-house-cover__logo { background: #fff; width: 270px; padding: 22px 20px; box-shadow: 0 2px 10px rgba(0,0,0,.35); }
body.gg-house--market_currents .gg-house-cover__logo img { display: block; width: 100%; height: auto; }
body.gg-house--market_currents .gg-house-cover__kicker { margin: 72px 0 0 !important; font: 500 9px var(--mc-sans) !important; letter-spacing: .34em !important; text-transform: uppercase; color: var(--mc-gold) !important; }
body.gg-house--market_currents .gg-house-cover__title { margin: 30px 0 4px !important; font: 600 26px/1.15 var(--mc-serif) !important; letter-spacing: 0 !important; color: var(--mc-gold-soft) !important; max-width: 100% !important; }
body.gg-house--market_currents .gg-house-cover__period { margin: 0 !important; font: 400 22px var(--mc-sans) !important; color: #fff !important; }
body.gg-house--market_currents .gg-house-cover__dek { margin: 26px 0 0 !important; max-width: 560px; font: 400 12px/1.5 'Courier New', Courier, monospace !important; color: #E7E9EE !important; }
body.gg-house--market_currents .gg-house-cover__dek b { color: #fff; }
body.gg-house--market_currents .gg-house-cover__stats { margin-top: auto !important; padding-top: 48px; display: grid !important; grid-template-columns: repeat(4, minmax(0, 1fr)) !important; gap: 22px !important; }
body.gg-house--market_currents .gg-house-cover__stat-value { font: 700 20px var(--mc-sans) !important; }
body.gg-house--market_currents .gg-house-cover__stat-label { margin-top: 6px; font: 500 9px/1.4 var(--mc-sans) !important; letter-spacing: .12em !important; text-transform: uppercase; color: #C9D0DC !important; }
body.gg-house--market_currents .gg-tone--pos { color: #7FD39F !important; }
body.gg-house--market_currents .gg-tone--neg { color: #F0857A !important; }
body.gg-house--market_currents .gg-tone--flat { color: #fff !important; }

/* section headings */
body.gg-house--market_currents .gg-section { background: transparent !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; }
body.gg-house--market_currents .gg-section-heading { display: block !important; padding: 0 0 10px !important; margin: 0 0 18px !important; border: 0 !important; border-bottom: 2px solid var(--mc-navy) !important; background: none !important; }
body.gg-house--market_currents .gg-section-index, body.gg-house--market_currents .gg-section-kicker::before,
body.gg-house--market_currents .gg-chart-wrap > .gg-chart-title::before { display: none !important; content: none !important; }
body.gg-house--market_currents .gg-section-heading-copy { padding: 0 !important; }
body.gg-house--market_currents .gg-section-kicker { font: 700 9px var(--mc-sans) !important; letter-spacing: .3em !important; text-transform: uppercase !important; color: var(--mc-gold) !important; margin: 0 0 6px !important; }
body.gg-house--market_currents .gg-section-heading h2 { font: 500 22px/1.2 var(--mc-serif) !important; color: #111 !important; letter-spacing: 0 !important; margin: 0 !important; }
body.gg-house--market_currents .gg-section-h3, body.gg-house--market_currents .gg-subsection-h4 {
  font: 700 17px/1.3 var(--mc-sans) !important; color: var(--mc-navy) !important; letter-spacing: 0 !important; text-transform: none !important; margin: 24px 0 10px !important; border: 0 !important; }

/* lists */
body.gg-house--market_currents ol.gg-list { list-style: none !important; counter-reset: mc-item; padding-left: 0 !important; }
body.gg-house--market_currents ol.gg-list > li { counter-increment: mc-item; position: relative; padding-left: 30px !important; margin: 0 0 12px !important; }
body.gg-house--market_currents ol.gg-list > li::before { content: counter(mc-item); position: absolute; left: 0; top: 3px; width: 17px; height: 17px; background: var(--mc-navy); color: #fff; font: 700 9px/17px var(--mc-sans); text-align: center; }
body.gg-house--market_currents ul.gg-list > li::marker { color: var(--mc-gold); }

/* callouts */
body.gg-house--market_currents .gg-house-callout { background: var(--mc-navy) !important; color: #fff !important; padding: 16px 20px 18px !important; margin: 22px 0 !important; border: 0 !important; break-inside: avoid; page-break-inside: avoid; }
body.gg-house--market_currents .gg-house-callout__kicker { font: 700 9px var(--mc-sans); letter-spacing: .24em; text-transform: uppercase; color: var(--mc-gold); margin-bottom: 10px; }
body.gg-house--market_currents .gg-house-callout p { margin: 0 !important; font: 400 13px/1.7 var(--mc-serif) !important; color: #F2F2F2 !important; }
body.gg-house--market_currents .gg-house-callout a, body.gg-house--market_currents .gg-house-callout a *,
body.gg-house--market_currents .gg-house-callout sup { color: var(--mc-gold-soft) !important; background: transparent !important; }
body.gg-house--market_currents blockquote.gg-pullquote { border-left: 3px solid var(--mc-gold) !important; background: var(--mc-card) !important; color: #222 !important; font: italic 400 14px/1.6 var(--mc-serif) !important; padding: 12px 16px !important; }

/* KPI cards */
body.gg-house--market_currents .gg-stats-grid { display: grid !important; grid-template-columns: repeat(4, minmax(0, 1fr)) !important; gap: 12px !important; margin: 22px 0 !important; }
body.gg-house--market_currents .gg-stat-card { display: flex !important; flex-direction: column !important; background: var(--mc-card) !important; border: 1px solid var(--mc-rule) !important; border-top: 3px solid var(--mc-navy) !important; border-radius: 0 !important; box-shadow: none !important; padding: 12px 14px !important; }
body.gg-house--market_currents .gg-stat-label { order: -1; margin: 0 0 6px !important; font: 600 8px/1.3 var(--mc-sans) !important; letter-spacing: .16em !important; text-transform: uppercase !important; color: #6B7280 !important; }
body.gg-house--market_currents .gg-stat-value { font: 700 21px/1.15 var(--mc-sans) !important; color: var(--mc-navy) !important; }
body.gg-house--market_currents .gg-stat-change { margin-top: 5px !important; font: 600 9px var(--mc-sans) !important; color: var(--mc-muted) !important; }
body.gg-house--market_currents .gg-stat-change.gg-change-up { color: var(--mc-green) !important; }
body.gg-house--market_currents .gg-stat-change.gg-change-down { color: var(--mc-red) !important; }

/* tables */
body.gg-house--market_currents .gg-table-wrap { background: transparent !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; }
body.gg-house--market_currents .gg-table { border-collapse: collapse !important; width: 100% !important; border: 0 !important; }
body.gg-house--market_currents .gg-table thead th { background: var(--mc-navy) !important; color: #fff !important; font: 700 9px var(--mc-sans) !important; letter-spacing: .1em !important; text-transform: uppercase !important; padding: 10px 12px !important; border: 0 !important; text-align: left !important; }
body.gg-house--market_currents .gg-table tbody td { background: #FBF9F5 !important; color: #222 !important; font: 400 11px/1.45 var(--mc-sans) !important; padding: 9px 12px !important; border: 0 !important; border-bottom: 1px solid #E6E0D4 !important; }
body.gg-house--market_currents .gg-table tbody tr:nth-child(even) td { background: #F5F1EA !important; }
body.gg-house--market_currents .gg-badge { display: inline-block; padding: 2px 8px; border-radius: 2px; font: 700 9.5px var(--mc-sans); white-space: nowrap; }
body.gg-house--market_currents .gg-badge--pos { background: #E1F0E6; color: var(--mc-green); }
body.gg-house--market_currents .gg-badge--neg { background: #F8E0DC; color: var(--mc-red); }
body.gg-house--market_currents .gg-badge--flat { background: #EEE8DC; color: #6B5B3E; }

/* timeline */
body.gg-house--market_currents .gg-house-timeline { list-style: none !important; margin: 14px 0 18px !important; padding: 0 !important; }
body.gg-house--market_currents .gg-house-timeline li { position: relative; display: grid; grid-template-columns: 86px 16px minmax(0, 1fr); column-gap: 12px; padding: 0 0 14px !important; margin: 0 !important; break-inside: avoid; }
body.gg-house--market_currents .gg-house-timeline li::after { content: ""; position: absolute; left: 105px; top: 18px; bottom: 0; width: 1px; background: #D8CDB8; }
body.gg-house--market_currents .gg-house-timeline li:last-child::after { display: none; }
body.gg-house--market_currents .gg-house-timeline__date { font: 700 11px/1.5 var(--mc-serif); color: var(--mc-navy); text-align: right; }
body.gg-house--market_currents .gg-house-timeline__dot { width: 14px; height: 14px; margin-top: 2px; border-radius: 50%; background: radial-gradient(circle at 40% 40%, #E9C987, var(--mc-bronze)); box-shadow: 0 0 0 3px #EFE6D3; }
body.gg-house--market_currents .gg-house-timeline__text { font: 400 12px/1.6 var(--mc-serif); color: #333; }

/* charts, figures, sources */
body.gg-house--market_currents .gg-chart-wrap, body.gg-house--market_currents .gg-chart-canvas-box,
body.gg-house--market_currents .gg-pdf-chart-fallback, body.gg-house--market_currents .gg-report-sections,
body.gg-house--market_currents .gg-sources, body.gg-house--market_currents .gg-source-list,
body.gg-house--market_currents .gg-source-line { background: transparent !important; border-color: var(--mc-rule) !important; box-shadow: none !important; }
body.gg-house--market_currents .gg-chart-wrap { border: 0 !important; padding: 0 !important; }
body.gg-house--market_currents .gg-pdf-chart-fallback svg > rect:first-child { fill: transparent !important; }
body.gg-house--market_currents .gg-sources h2 { font: 500 20px var(--mc-serif) !important; color: #111 !important; border-bottom: 2px solid var(--mc-navy) !important; padding-bottom: 8px !important; }
body.gg-house--market_currents .gg-chart-title { font: 700 17px var(--mc-sans) !important; color: var(--mc-navy) !important; text-transform: none !important; letter-spacing: 0 !important; }
body.gg-house--market_currents .gg-source-note, body.gg-house--market_currents .gg-chart-caption { font: italic 400 10px var(--mc-serif) !important; color: #7A7F8A !important; }
body.gg-house--market_currents .gg-house-kpis { margin-top: 8px; }
body.gg-house--market_currents .gg-house-disclaimer { margin-top: 36px; padding-top: 14px; border-top: 1px solid #CFC7B6; }
body.gg-house--market_currents .gg-house-disclaimer p { font: 400 9px/1.6 var(--mc-serif) !important; color: #555 !important; text-align: justify; margin: 0 0 8px !important; }
"""

_FEATURE_CSS = r"""
body.gg-house--feature_article {
  --fa-ink: #1F1F1F; --fa-muted: #5E5A55; --fa-red: #C0392B; --fa-card: #F4F1EC; --fa-rule: #DDD8D0;
  --fa-serif: 'Lora', Georgia, 'Times New Roman', serif; --fa-sans: 'Inter', 'Helvetica Neue', Arial, sans-serif;
  background: #fff !important; color: var(--fa-ink) !important;
}
body.gg-house--feature_article main { max-width: 760px !important; margin: 0 auto !important; background: #fff !important; }
body.gg-house--feature_article .gg-report-footer, body.gg-house--feature_article .gg-report-nav { display: none !important; }
body.gg-house--feature_article .gg-section-body p, body.gg-house--feature_article .gg-section-body li { font: 400 17px/1.7 var(--fa-serif) !important; color: var(--fa-ink) !important; }
body.gg-house--feature_article .gg-feature-head { background: #fff !important; border: 0 !important; box-shadow: none !important; padding: 48px 0 18px !important; margin: 0 auto !important; max-width: 760px !important; display: block !important; min-height: 0 !important; }
body.gg-house--feature_article .gg-feature-kicker { margin: 0 0 28px !important; font: 700 10px var(--fa-sans) !important; letter-spacing: .04em; text-transform: uppercase; color: var(--fa-ink) !important; }
body.gg-house--feature_article .gg-feature-title { margin: 0 0 30px !important; font: 400 46px/1.1 var(--fa-sans) !important; letter-spacing: -.01em !important; color: #2B2420 !important; max-width: 100% !important; }
body.gg-house--feature_article .gg-feature-hero { margin: 0 0 8px !important; }
body.gg-house--feature_article .gg-feature-hero img { display: block; width: 100%; max-height: 420px; object-fit: cover; }
body.gg-house--feature_article .gg-feature-hero figcaption { font: 400 11px var(--fa-sans); color: var(--fa-muted); margin-top: 4px; }
body.gg-house--feature_article .gg-feature-standfirst { margin: 10px 0 22px !important; font: 400 18px/1.55 var(--fa-serif) !important; color: #2A2A2A !important; }
body.gg-house--feature_article .gg-feature-meta { margin: 0 !important; padding-bottom: 18px; border-bottom: 1px solid var(--fa-rule); font: 400 13px var(--fa-serif) !important; color: var(--fa-muted) !important; }
body.gg-house--feature_article .gg-section { background: transparent !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; margin: 26px 0 0 !important; }
body.gg-house--feature_article .gg-section-heading { display: block !important; border: 0 !important; background: none !important; padding: 0 !important; margin: 30px 0 10px !important; }
body.gg-house--feature_article .gg-section-index, body.gg-house--feature_article .gg-section-kicker,
body.gg-house--feature_article .gg-chart-wrap > .gg-chart-title::before { display: none !important; content: none !important; }
body.gg-house--feature_article .gg-section-heading h2 { font: 700 21px/1.3 var(--fa-serif) !important; color: var(--fa-ink) !important; letter-spacing: 0 !important; text-transform: none !important; margin: 0 !important; }
body.gg-house--feature_article .gg-section-h3, body.gg-house--feature_article .gg-subsection-h4 { font: 700 17px/1.35 var(--fa-serif) !important; color: var(--fa-ink) !important; text-transform: none !important; letter-spacing: 0 !important; margin: 22px 0 6px !important; }
body.gg-house--feature_article blockquote.gg-pullquote { margin: 26px 0 !important; padding: 4px 0 4px 20px !important; border: 0 !important; border-left: 3px solid var(--fa-red) !important; background: none !important; font: italic 400 22px/1.45 var(--fa-serif) !important; color: #2B2420 !important; }
body.gg-house--feature_article .gg-house-callout { background: var(--fa-card) !important; padding: 16px 20px !important; margin: 22px 0 !important; break-inside: avoid; }
body.gg-house--feature_article .gg-house-callout__kicker { font: 700 10px var(--fa-sans); letter-spacing: .12em; text-transform: uppercase; color: var(--fa-red); margin-bottom: 6px; }
body.gg-house--feature_article .gg-house-callout p { margin: 0 !important; font: 400 15px/1.6 var(--fa-serif) !important; }
body.gg-house--feature_article .gg-chart-wrap { background: var(--fa-card) !important; border: 0 !important; box-shadow: none !important; padding: 20px 22px 14px !important; margin: 26px 0 !important; }
body.gg-house--feature_article .gg-chart-canvas-box, body.gg-house--feature_article .gg-pdf-chart-fallback { background: transparent !important; }
body.gg-house--feature_article .gg-pdf-chart-fallback svg > rect:first-child { fill: transparent !important; }
body.gg-house--feature_article .gg-chart-title { text-align: center !important; font: 800 26px/1.15 var(--fa-sans) !important; color: #111 !important; text-transform: none !important; letter-spacing: -.01em !important; }
body.gg-house--feature_article .gg-source-note, body.gg-house--feature_article .gg-chart-caption { font: 400 11px var(--fa-sans) !important; color: var(--fa-muted) !important; }
body.gg-house--feature_article .gg-stats-grid { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)) !important; gap: 14px !important; }
body.gg-house--feature_article .gg-stat-card { background: var(--fa-card) !important; border: 0 !important; border-radius: 0 !important; box-shadow: none !important; }
body.gg-house--feature_article .gg-stat-value { color: var(--fa-ink) !important; font-family: var(--fa-sans) !important; }
body.gg-house--feature_article .gg-table thead th { background: #fff !important; color: var(--fa-ink) !important; border-bottom: 2px solid var(--fa-ink) !important; font: 700 11px var(--fa-sans) !important; text-transform: none !important; }
body.gg-house--feature_article .gg-table tbody td { background: #fff !important; border-bottom: 1px solid var(--fa-rule) !important; font: 400 12.5px var(--fa-sans) !important; }
body.gg-house--feature_article .gg-house-timeline { list-style: none; padding: 0; margin: 16px 0; }
body.gg-house--feature_article .gg-house-timeline li { display: grid; grid-template-columns: 90px 0 minmax(0, 1fr); column-gap: 14px; padding: 8px 0; border-top: 1px solid var(--fa-rule); }
body.gg-house--feature_article .gg-house-timeline__date { font: 700 13px var(--fa-sans); color: var(--fa-red); }
body.gg-house--feature_article .gg-house-timeline__text { font: 400 15px/1.6 var(--fa-serif); }
"""


def _scoped(css: str) -> str:
    # The editorial layer scopes its rules as `html body.gg-editorial ...`; the
    # same specificity here, loaded later, lets the house style win.
    return css.replace("body.gg-house--", "html body.gg-house--")


def house_css(style: str | None) -> str:
    return _scoped(_MARKET_CSS if style == "market_currents" else _FEATURE_CSS if style == "feature_article" else "")


def _css_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def house_print_css(style: str | None, running: str) -> str:
    """Print page furniture; appended last in the PDF cascade."""
    if style == "market_currents":
        arn = _css_string(brand_arn())
        return r"""
@page {
  size: A4; margin: 15mm 14mm 20mm 14mm; background: #F4F0E8;
  @top-left { content: none; border: 0; } @top-center { content: none; border: 0; } @top-right { content: none; border: 0; }
  @bottom-left { content: "@@RUNNING@@"; font: 500 7.5pt Inter, Arial, sans-serif; color: #2B2B2B; border-top: .5pt solid #D9D2C3; padding-top: 2mm; vertical-align: top; }
  @bottom-center { content: counter(page) "\A @@ARN@@"; white-space: pre; font: 400 7pt/1.5 Inter, Arial, sans-serif; color: #2B2B2B; border-top: .5pt solid #D9D2C3; padding-top: 2mm; vertical-align: top; }
  @bottom-right { content: "@@FIRM@@"; font: 500 7.5pt Inter, Arial, sans-serif; color: #2B2B2B; border-top: .5pt solid #D9D2C3; padding-top: 2mm; vertical-align: top; }
}
@page :first {
  margin: 0;
  @bottom-left { content: none; border: 0; } @bottom-center { content: none; border: 0; } @bottom-right { content: none; border: 0; }
}
html:has(body[data-report-style="market_currents"]), body.gg-house--market_currents { background: #F4F0E8 !important; }
body.gg-house--market_currents { font-size: 10pt !important; }
body.gg-house--market_currents main { padding: 0 !important; max-width: none !important; }
body.gg-house--market_currents .gg-house-cover { width: 210mm !important; height: 297mm !important; min-height: 297mm !important; padding: 15mm 13mm 18mm !important; break-after: page !important; page-break-after: always !important; }
body.gg-house--market_currents .gg-house-cover__logo { width: 64mm; padding: 6mm 5mm; }
body.gg-house--market_currents .gg-house-cover__kicker { margin-top: 20mm !important; font-size: 7pt !important; }
body.gg-house--market_currents .gg-house-cover__title { font-size: 17pt !important; margin-top: 9mm !important; }
body.gg-house--market_currents .gg-house-cover__period { font-size: 16pt !important; }
body.gg-house--market_currents .gg-house-cover__dek { font-size: 9pt !important; }
body.gg-house--market_currents .gg-house-cover__stat-value { font-size: 14pt !important; }
body.gg-house--market_currents .gg-house-cover__stat-label { font-size: 6.5pt !important; }
body.gg-house--market_currents .gg-report-sections > .gg-section { break-before: auto !important; page-break-before: auto !important; margin-bottom: 24px !important; }
body.gg-house--market_currents .gg-report-sections > .gg-section:first-child { break-before: auto !important; page-break-before: auto !important; }
body.gg-house--market_currents .gg-section-heading { break-after: avoid !important; page-break-after: avoid !important; break-inside: avoid !important; }
body.gg-house--market_currents .gg-chart-wrap,
body.gg-house--market_currents .gg-house-callout,
body.gg-house--market_currents .gg-table-wrap,
body.gg-house--market_currents .gg-stat-card { break-inside: avoid !important; page-break-inside: avoid !important; }
body.gg-house--market_currents .gg-sources, body.gg-house--market_currents #data-sources { break-before: page; }
body.gg-house--market_currents .gg-section-heading h2 { font-size: 15.5pt !important; }
body.gg-house--market_currents .gg-section-body p, body.gg-house--market_currents .gg-section-body li { font-size: 10pt !important; }
body.gg-house--market_currents .gg-house-callout p { font-size: 9.8pt !important; }
body.gg-house--market_currents .gg-section-h3, body.gg-house--market_currents .gg-subsection-h4 { font-size: 12.5pt !important; }
body.gg-house--market_currents .gg-stat-value { font-size: 15pt !important; }
body.gg-house--market_currents .gg-house-disclaimer p { font-size: 6.8pt !important; }
""".replace("body.gg-house--", "html body.gg-house--").replace("@@ARN@@", arn).replace("@@FIRM@@", _css_string(FIRM_NAME)).replace("@@RUNNING@@", _css_string(running))
    if style == "feature_article":
        return r"""
@page {
  size: A4; margin: 18mm 20mm 18mm 20mm;
  @top-left { content: none; border: 0; } @top-center { content: none; border: 0; } @top-right { content: none; border: 0; }
  @bottom-left { content: none; border: 0; } @bottom-right { content: none; border: 0; }
  @bottom-center { content: counter(page); font: 400 8pt Inter, Arial, sans-serif; color: #777; border: 0; }
}
@page :first { @bottom-center { content: none; } }
html:has(body[data-report-style="feature_article"]), body.gg-house--feature_article { background: #fff !important; }
body.gg-house--feature_article main { max-width: none !important; padding: 0 !important; }
body.gg-house--feature_article .gg-feature-head { padding-top: 6mm !important; max-width: none !important; }
body.gg-house--feature_article .gg-feature-title { font-size: 30pt !important; }
body.gg-house--feature_article .gg-feature-standfirst { font-size: 12pt !important; }
body.gg-house--feature_article .gg-section-body p, body.gg-house--feature_article .gg-section-body li { font-size: 11.5pt !important; }
body.gg-house--feature_article .gg-section-heading h2 { font-size: 14pt !important; }
body.gg-house--feature_article blockquote.gg-pullquote { font-size: 15pt !important; }
body.gg-house--feature_article .gg-chart-title { font-size: 17pt !important; }
""".replace("body.gg-house--", "html body.gg-house--")
    return ""
