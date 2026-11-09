"""One merged source list (Stage D): clean titles, publisher, reliability and a one-line reason.

The renderer owns the only source list in a report ("Data Sources"). Each row is
keyed by its [S#] id so every in-text citation resolves to a row, and carries
the reliability assessment that used to be a separate appendix.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_JUNK_TITLE_RE = re.compile(
    r"^(?:untitled|home|index|document|pdf|download|aigeneratedcontent|view|page|null|none)$", re.I)
_HOST_PREFIX_RE = re.compile(r"^(?:www\d?|ftp|img|cdn|static|assets|nsearchives|archives|docs|files|media|m)\.", re.I)


def _host(url: str) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").casefold()
    except ValueError:
        return ""


def display_publisher(source: dict) -> str:
    """Publisher name, or the registrable part of the domain ("nsearchives.nseindia.com" -> "nseindia.com")."""
    publisher = str(source.get("publisher") or "").strip()
    host = _host(source.get("publisher_page_url") or source.get("url") or "") or str(source.get("domain") or "").casefold()
    if publisher and "." not in publisher:
        return publisher
    name = publisher or host
    while _HOST_PREFIX_RE.match(name) and name.count(".") >= 2:
        name = _HOST_PREFIX_RE.sub("", name, count=1)
    return name


def title_is_junk(title: str, source: dict) -> bool:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if len(text) < 4:
        return True
    host = _host(source.get("url") or "")
    if text.casefold() in {host, str(source.get("domain") or "").casefold(), display_publisher(source).casefold()}:
        return True
    if re.fullmatch(r"[\w.-]+\.(?:com|in|org|net|gov|co|io)(?:\.[a-z]{2})?", text, re.I):
        return True  # a bare domain
    compact = text.replace(" ", "")
    if _JUNK_TITLE_RE.match(compact):
        return True
    if re.fullmatch(r"[A-Z0-9]{12,}", compact) and " " not in text:
        return True  # glued capitals ("AIGENERATEDCONTENT")
    if re.match(r"^(?:INR|USD|Rs\.?|₹|\$)?\s*[\d.,]+\s*[a-z]{0,3}$", text, re.I):
        return True  # a value, not a title ("INR295.3b")
    half = len(text) // 2
    if len(text) > 20 and text[:half].strip() == text[half:].strip():
        return True  # the same phrase twice
    words = text.split()
    if len(words) >= 4 and len(set(w.casefold() for w in words)) <= len(words) // 2:
        return True
    return False


def _title_from_url(url: str) -> str:
    path = unquote(urlparse(str(url or "")).path or "")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"\.(?:pdf|html?|aspx?|php|cms)$", "", name, flags=re.I)
    name = re.sub(r"[_+]+", " ", name)
    name = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -")
    if len(name) < 6 or re.fullmatch(r"[\da-f-]{16,}|\d+", name, re.I):
        return ""
    return name[:120]


def clean_title(source: dict) -> str:
    """The source's title; junk titles fall back to the page title, the file name, then the publisher."""
    for candidate in (source.get("title"), source.get("publisher_page_title")):
        text = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if text and not title_is_junk(text, source):
            return text[:180]
    from_url = _title_from_url(source.get("publisher_page_url") or source.get("url") or "")
    if from_url and not title_is_junk(from_url, source):
        return from_url
    publisher = display_publisher(source)
    return f"{publisher} document" if publisher else "Untitled source"


def short_reason(source: dict, max_len: int = 90) -> str:
    """First clause of the reliability note ("regulator data; dated 2026-10" -> one short line)."""
    note = re.sub(r"\s+", " ", str(source.get("reliability_note") or "")).strip()
    if not note:
        return ""
    first = re.split(r"(?<=[.;])\s+|\s+\|\s+|;\s*", note)[0].rstrip(".;")
    # "primary official (regulator / exchange / official statistics)" -> the reason in brackets;
    # the tier itself is shown separately.
    bracket = re.search(r"\(([^)]{4,})\)", first)
    if bracket:
        first = bracket.group(1).strip()
    return first if len(first) <= max_len else first[: max_len - 1].rstrip(" ,;") + "…"


def source_number(source_id: str) -> int | None:
    match = re.fullmatch(r"S(\d+)", str(source_id or "").strip().upper())
    return int(match.group(1)) if match else None


def display_date(raw) -> str:
    """'2025-06-26', 'Jun 26, 2025', '26 June 2025' -> '2025-06-26' (ISO, as before); never a cut string ('Jun 26, 20')."""
    from datetime import datetime
    text = str(raw or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%d-%m-%Y", "%Y-%m"):
        for candidate in (text[:10], text[:12], text[:18], text):
            try:
                parsed = datetime.strptime(candidate.strip(), fmt)
                return f"{parsed:%Y-%m-%d}" if fmt != "%Y-%m" else f"{parsed:%Y-%m}"
            except ValueError:
                continue
    import re as _re
    year = _re.search(r"\b(?:19|20)\d{2}\b", text)
    return text[:24] if year else ""


def is_calculated(source: dict) -> bool:
    """A dataset computed in this report (no public page): never shown as a 'primary official' source."""
    url = str(source.get("url") or "")
    blob = " ".join(str(source.get(k) or "") for k in ("reliability_note", "publisher", "title", "kind", "evidence_origin"))
    return not url.startswith(("http://", "https://")) and bool(re.search(r"(?i)calculated|internal dataset|growth gradual market data", blob))


def metadata_parts(source: dict) -> list[str]:
    if is_calculated(source):
        # Stage I: an internal calculated dataset is labelled as such, not "primary official, reliability 1.00".
        publisher = display_publisher(source)
        return [x for x in (publisher, "Calculated in this report from the market data named in the title") if x]
    parts = []
    publisher = display_publisher(source)
    if publisher:
        parts.append(publisher)
    date = display_date(source.get("publication_date"))
    parts.append(f"Published: {date}" if date else "Undated")
    tier = str(source.get("authority_tier") or "").replace("_", " ").strip()
    if tier and tier != "unknown":
        parts.append(f"Tier: {tier}")
    score = source.get("reliability_score")
    try:
        parts.append(f"Reliability: {float(score):.2f}")
    except (TypeError, ValueError):
        pass
    reason = short_reason(source)
    if reason:
        parts.append(reason)
    return parts
