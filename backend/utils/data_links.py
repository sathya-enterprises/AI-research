"""Follow data-file links (XLSX / CSV / PDF) found on fetched pages, for any topic.

Official publishers usually put the year-by-year numbers in a linked workbook or report rather than on the
page itself (a ministry's "physical progress" page links an XLSX; a statistics office links a CSV). The
fetcher returns each page's data-file links with their anchor text (``pageDataLinks``). This module picks
the few that match what the report must measure, preferring spreadsheets and official parents, and turns
them into source rows; the caller fetches them through the same relevance gate as any page.

Matching: a link must share a word with the measure plan (what is measured), or two words with the
question when there is no plan. Nothing here knows a topic, a country or a host.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote, urlparse

from utils.primary_sources import file_extension, is_primary

_STOP = set("""
about above after again against also among analysis annual based been before being below between both data
does doing during each from further have having here into just latest like made major many more most much over
past report reports same should some such than that their them then there these they this those through under
until very what when where which while will with within would year years your growth trend trends leading main
total number share level levels rate rates value values change changes compared across other overall
""".split())
_DATA_WORDS = re.compile(r"(?i)\b(statistic|statistics|data|dataset|table|tables|progress|series|annual|"
                         r"monthly|quarterly|time.?series|indicator|bulletin|factsheet|fact sheet|yearbook|database)\b")
_FORMAT_SCORE = {".xlsx": 3, ".xlsm": 3, ".csv": 3, ".pdf": 1}
_FETCHABLE = set(_FORMAT_SCORE)  # legacy .xls / .ods are not readable without extra libraries
_SEPARATORS = re.compile(r"[_\-.+%]+")
_DATA_CONTEXT_UNIT_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:%|per\s?cent|GW|MW|KW|TW|TWh|GWh|MWh|kWh|BU|"
    r"years?|yrs?|tonnes?|metric\s+tons?|mt|kt|kg|km|ha|"
    r"USD|INR|EUR|GBP|JPY|dollars?|rupees?|euros?|pounds?|yen|Rs\.?|"
    r"\u20b9|\u20ac|\u00a3|\u00a5|"
    r"lakh\s+crores?|lakh\s+crs?\.?|crores?|crs?\.?|lakhs?|"
    r"million|billion|trillion|thousand|bn|mn)(?![a-z0-9])"
)
_CONTEXT_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")


def _words(text: str) -> set[str]:
    words = {w for w in re.findall(r"[a-z][a-z0-9]{3,}", str(text or "").casefold()) if w not in _STOP}
    # a plural and its singular match the same link text
    return words | {w[:-1] for w in words if w.endswith("s") and len(w) > 4}


def link_terms(question: str, measures: list[str] | None = None) -> tuple[set[str], set[str]]:
    """(measure words, question words): content words of 4+ letters."""
    return _words(" ".join(str(m or "") for m in measures or [])), _words(question)


def _link_words(link: dict) -> set[str]:
    name = unquote(urlparse(str(link.get("url") or "")).path).rsplit("/", 1)[-1]
    return _words(f"{link.get('text') or ''} {_context_label(link.get('context') or '')} {_SEPARATORS.sub(' ', name)}")


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def _data_parent(parent: dict, terms: set[str]) -> bool:
    """A page that is itself about the measure and presents data (official host, or a statistics/data page).

    Its file links are often named only "Year Review 2024" or "Annual report", with no measure words."""
    label = f"{parent.get('title') or ''} {parent.get('publisherPageTitle') or ''} {parent.get('url') or ''}"
    if not (is_primary(str(parent.get("url") or "")) or _DATA_WORDS.search(label)):
        return False
    body = _words(str(parent.get("pageContent") or "")[:20000])
    return len(body & terms) >= 2


def parent_data_context(text: object, limit: int = 1200) -> str:
    """Keep only unit-bearing captions and table headers from a fetched parent page.

    Linked CSV/XLSX files often omit units that appear in the publisher page's
    column headings. This context carries those labels to the child file; it
    deliberately excludes numeric observations so the file remains the value
    source of truth.
    """
    lines = [re.sub(r"\s+", " ", line).strip() for line in str(text or "").splitlines()]
    selected: list[str] = []
    seen: set[str] = set()

    def has_observation(line: str) -> bool:
        tokens = _CONTEXT_NUMBER_RE.findall(line)
        if not tokens:
            return False
        # Years in a caption/header are metadata. Any other numeric token is a
        # value, so do not transport that line into child-source context.
        return any(not _YEAR_ONLY_RE.fullmatch(token.replace(",", "")) for token in tokens)

    for index, line in enumerate(lines):
        if not line or len(line) > 700 or not _DATA_CONTEXT_UNIT_RE.search(line) or has_observation(line):
            continue
        if "|" in line:
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if any(has_observation(cell) for cell in cells):
                continue
        if index:
            heading = lines[index - 1]
            if (heading and "|" not in heading and len(heading) <= 180
                    and not has_observation(heading) and heading.casefold() not in seen):
                selected.append(heading)
                seen.add(heading.casefold())
        if line.casefold() not in seen:
            selected.append(line)
            seen.add(line.casefold())
        if sum(len(item) + 1 for item in selected) >= limit:
            break
    return "\n".join(selected)[:max(0, limit)]


def score_link(link: dict, parent: dict, measure_terms: set[str], question_terms: set[str]) -> int:
    """0 means skip."""
    ext = file_extension(str(link.get("url") or ""))
    if ext not in _FETCHABLE:
        return 0
    words = _link_words(link)
    measure_hits = len(words & measure_terms)
    question_hits = len(words & question_terms)
    if (measure_terms and measure_hits == 0) or (not measure_terms and question_hits < 2):
        # a data page about the measure may name its files only by year or kind ("Year Review 2024")
        label = f"{link.get('text') or ''} {link.get('url') or ''}"
        if not ((_YEAR.search(label) or _DATA_WORDS.search(label)) and _data_parent(parent, measure_terms or question_terms)):
            return 0
        newest = max((int(y) for y in _YEAR.findall(label)), default=0)
        return 1 + _FORMAT_SCORE.get(ext, 0) + (1 if newest >= date.today().year - 2 else 0)
    score = 3 * min(measure_hits, 3) + min(question_hits, 3) + _FORMAT_SCORE.get(ext, 0)
    if is_primary(str(parent.get("url") or "")) or is_primary(str(link.get("url") or "")):
        score += 3
    if _DATA_WORDS.search(f"{link.get('text') or ''} {link.get('url') or ''}"):
        score += 2
    return score


_LABEL_START = re.compile(r"\b(?:Table|Figure|Chart|Exhibit|Annex|Annexure|Appendix|Statement|Schedule)\s+[\w.\-]+",
                          re.I)
_FORMAT_WORDS = re.compile(r"(?i)(?:\s*\b(?:available formats?|download|xlsx?|csv|pdf)\b\s*:?)+\s*$")


def _context_label(context: str, size: int = 120) -> str:
    """The label printed before a bare "XLS" link: from the last "Table 3.1 ..." if there is one, else the
    trailing words (never starting mid-word); format words ("Available formats: XLS") are dropped."""
    text = _FORMAT_WORDS.sub("", str(context or "")).strip()
    starts = list(_LABEL_START.finditer(text))
    if starts and len(text) - starts[-1].start() <= 2 * size:
        return text[starts[-1].start():][: 2 * size].strip()
    tail = text[-size:]
    if len(text) > size and " " in tail:
        tail = tail.split(" ", 1)[1]
    return tail.strip(" .:-|")


def pick_links(rows: list[dict], *, measure_terms: set[str], question_terms: set[str], limit: int,
               existing_urls: set[str], per_parent: int = 2) -> list[dict]:
    """New source rows for the best data-file links on successfully fetched pages (best first)."""
    if limit <= 0 or not (measure_terms or question_terms):
        return []
    seen = {str(u or "").rstrip("/").casefold() for u in existing_urls}
    scored: list[tuple[int, int, dict, dict]] = []
    for parent in rows or []:
        if not isinstance(parent, dict) or parent.get("pageFetchStatus") != "ok":
            continue
        for link in parent.get("pageDataLinks") or []:
            score = score_link(link, parent, measure_terms, question_terms)
            if score:
                scored.append((score, len(scored), link, parent))
    scored.sort(key=lambda t: (-t[0], t[1]))
    picked: list[dict] = []
    per: dict[str, int] = {}
    for score, _, link, parent in scored:
        url = str(link.get("url") or "")
        key = url.rstrip("/").casefold()
        parent_url = str(parent.get("url") or "")
        if key in seen or per.get(parent_url, 0) >= per_parent:
            continue
        seen.add(key)
        per[parent_url] = per.get(parent_url, 0) + 1
        name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
        title = str(link.get("text") or "").strip() or name
        context = str(link.get("context") or "").strip()
        if len(title) < 12 and context:  # "XLS" -> the table label printed beside the link
            title = _context_label(context)
        parent_title = str(parent.get("publisherPageTitle") or parent.get("title") or "").strip()
        parent_context = parent_data_context(parent.get("pageContent"))
        if len(title) < 12 and parent_title:
            title = f"{title} ({parent_title[:120]})"
        picked.append({
            "url": url, "title": title,
            # Preserve the page that establishes what a generic-named CSV/XLSX contains.
            # The file remains the evidence source; this is contextual provenance for its rows.
            "publisherPageTitle": parent_title[:180],
            "publisher": parent.get("publisherPagePublisher") or parent.get("publisher") or "",
            "snippet": "", "fullContent": "",
            "parentUrl": parent_url, "discoveredVia": "data_link", "dataLinkScore": score,
            "searchQuery": parent.get("searchQuery") or "",
            "parentDataContext": parent_context,
        })
        if len(picked) >= limit:
            break
    return picked


def link_limit(report_tier: str) -> int:
    return {"detailed": 4, "in_depth": 8}.get(str(report_tier or "detailed"), 4)
