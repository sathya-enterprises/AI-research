"""Transparent source-coverage diagnostics for long-form research reports.

This module does not decide whether a report is publishable. It records whether
an evidence collection job reached its intended breadth, so a long document
cannot hide a thin or poorly verified research base.
"""
from __future__ import annotations

import re
import calendar
from datetime import date, timedelta
from urllib.parse import urlparse, unquote

_OFFICIAL_HOSTS = (
    "sebi.gov.in", "rbi.org.in", "nsdl.co.in", "cdslindia.com",
    "nseindia.com", "bseindia.com", "dpiit.gov.in", "mospi.gov.in",
    "dea.gov.in", "finmin.nic.in", "pib.gov.in", "commerce.gov.in",
    "mea.gov.in", "sansad.in",
    "imf.org", "worldbank.org", "bis.org", "oecd.org",
)

_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}


def _requested_count(value: str) -> int:
    token = str(value or "").strip().casefold()
    return _COUNT_WORDS.get(token, int(token) if token.isdigit() else 0)


def indian_fy_quarter(day: date) -> tuple[str, date, date]:
    """Return the Indian April–March fiscal-quarter label and inclusive dates."""
    if day.month >= 4:
        fiscal_end_year = day.year + 1
        month_in_fy = day.month - 3
    else:
        fiscal_end_year = day.year
        month_in_fy = day.month + 9
    quarter = (month_in_fy - 1) // 3 + 1
    start_month_in_fy = (quarter - 1) * 3 + 1
    start_month = start_month_in_fy + 3 if start_month_in_fy <= 9 else start_month_in_fy - 9
    start_year = fiscal_end_year - 1 if start_month >= 4 else fiscal_end_year
    start = date(start_year, start_month, 1)
    end_month = start_month + 2
    end_year = start_year
    if end_month > 12:
        end_month -= 12
        end_year += 1
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    return f"Q{quarter} FY{str(fiscal_end_year)[-2:]}", start, end


def requested_indian_fy_quarters(question: str, as_of_date: date | None = None) -> list[tuple[str, date, date]]:
    """List the completed Indian fiscal quarters requested by a relative-window prompt."""
    match = re.search(r"\b(?:past|last)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+quarters?\b", str(question or ""), re.I)
    if not match:
        return []
    count = _requested_count(match.group(1))
    if count <= 0:
        return []
    today = as_of_date or date.today()
    current_label, current_start, current_end = indian_fy_quarter(today)
    last_complete_end = current_start - timedelta(days=1) if today < current_end else current_end
    periods: list[tuple[str, date, date]] = []
    probe = last_complete_end
    for _ in range(count):
        period = indian_fy_quarter(probe)
        periods.append(period)
        probe = period[1] - timedelta(days=1)
    periods.reverse()
    return periods


def requested_quarter_count(question: str, as_of_date: date | None = None, default: int = 8) -> int:
    """Number of completed quarters a relative-window prompt asks for.

    "last 4 quarters" -> 4; "past two years" -> 8 (capped at 12); otherwise ``default``.
    Data fetches and search queries use this so they match the window that the
    requested-window chart gate later enforces.
    """
    periods = requested_indian_fy_quarters(question, as_of_date)
    if periods:
        return len(periods)
    match = re.search(r"\b(?:past|last)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+years?\b", str(question or ""), re.I)
    if match:
        years = _requested_count(match.group(1))
        if years > 0:
            return max(1, min(12, years * 4))
    return default


def _subtract_calendar_years(day: date, years: int) -> date:
    year = day.year - years
    try:
        return date(year, day.month, day.day)
    except ValueError:
        return date(year, day.month, calendar.monthrange(year, day.month)[1])


def _requested_rolling_year_window(question: str, as_of_date: date) -> tuple[date, date] | None:
    match = re.search(r"\b(?:past|last)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+years?\b", str(question or ""), re.I)
    if not match:
        return None
    years = _requested_count(match.group(1))
    if years <= 0:
        return None
    return _subtract_calendar_years(as_of_date, years), as_of_date


def _source_url(row: dict) -> str:
    return str(row.get("url") or row.get("canonicalUrl") or row.get("canonical_url") or "").strip()


def _fetched(row: dict) -> bool:
    status = str(row.get("pageFetchStatus") or row.get("page_fetch_status") or row.get("fetchStatus") or "").casefold()
    origin = str(row.get("evidenceOrigin") or row.get("evidence_origin") or "").casefold()
    return status in {"ok", "success", "fetched"} and origin not in {"search_extract", "search_result", "snippet"}


def _quarter_label_pattern(label: str) -> re.Pattern:
    match = re.fullmatch(r"Q([1-4]) FY(\d{2})", str(label or "").strip(), re.I)
    if not match:
        return re.compile(r"(?!)")
    quarter, short_year = match.groups()
    short = int(short_year)
    full_year = (1900 if short >= 70 else 2000) + short
    return re.compile(
        rf"\bQ{quarter}\s*FY\s*'?((?:{full_year})|(?:{short_year}))\b",
        re.I,
    )


def _source_has_verified_page(row: dict) -> bool:
    status = str(row.get("pageFetchStatus") or row.get("page_fetch_status") or "").casefold()
    origin = str(row.get("evidenceExcerptOrigin") or row.get("evidenceOrigin") or row.get("evidence_origin") or "").casefold()
    has_publisher_text = any(row.get(key) for key in (
        "pageContent", "page_content", "publisherEvidenceExcerpt", "publisher_evidence_excerpt",
    ))
    has_excerpt = any(row.get(key) for key in ("evidenceExcerpt", "evidence_excerpt"))
    fetched = status in {"ok", "success", "fetched"}
    publisher_origin = "publisher_page" in origin
    # A fetched page body or publisher excerpt is direct evidence. A generic
    # evidence excerpt is direct only when its provenance explicitly says so;
    # search snippets must never count as exchange coverage.
    return bool(
        (has_publisher_text and fetched)
        or (has_publisher_text and publisher_origin)
        or (has_excerpt and publisher_origin)
    )


def _source_host(row: dict) -> str:
    return (urlparse(_source_url(row)).hostname or "").casefold().removeprefix("www.")


def _official_exchange_source_ids(sources: object) -> dict[str, set[str]]:
    rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    source_ids: dict[str, set[str]] = {"nse": set(), "bse": set()}
    for row in rows:
        if not isinstance(row, dict) or not _source_has_verified_page(row):
            continue
        host = _source_host(row)
        source_id = str(row.get("id") or "").strip().upper()
        if not source_id:
            continue
        if host == "nseindia.com" or host.endswith(".nseindia.com"):
            source_ids["nse"].add(source_id)
        if host == "bseindia.com" or host.endswith(".bseindia.com"):
            source_ids["bse"].add(source_id)
    return source_ids


def _official_exchange_coverage(sources: object) -> dict[str, bool]:
    source_ids = _official_exchange_source_ids(sources)
    return {exchange: bool(ids) for exchange, ids in source_ids.items()}


def _official_fta_signature_source(sources: object, cited_ids: set[str] | None = None) -> bool:
    rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    official_domains = ("commerce.gov.in", "pib.gov.in", "mea.gov.in", "egazette.gov.in", "sansad.in")
    agreement_terms = re.compile(r"\b(?:fta|free trade|cepa|ecta|tepa|trade agreement)\b", re.I)
    signature_terms = re.compile(r"\b(?:sign(?:ed|ing)?|signature)\b", re.I)
    required_ids = {str(value).strip().upper() for value in cited_ids or set() if str(value).strip()}
    if cited_ids is not None and not required_ids:
        return False
    for row in rows:
        if not isinstance(row, dict) or not _source_has_verified_page(row):
            continue
        source_id = str(row.get("id") or "").strip().upper()
        if cited_ids is not None and source_id not in required_ids:
            continue
        host = _source_host(row)
        if not any(host == domain or host.endswith("." + domain) for domain in official_domains):
            continue
        # The fetched page itself counts, not only the short excerpt kept for display (live run F: the
        # excerpt of a Lok Sabha answer was an MSME paragraph while its annexure listed the signed FTAs).
        text = " ".join(str(row.get(key) or "") for key in (
            "title", "snippet", "pageContent", "page_content", "publisherEvidenceExcerpt",
            "publisher_evidence_excerpt", "evidenceExcerpt", "evidence_excerpt", "fullContent", "full_content",
            "_publisherRawFetchedContent", "_publisherStructuredTableEvidence",
        ))
        if agreement_terms.search(text) and signature_terms.search(text):
            return True
    return False


_CALENDAR_DATE_RE = re.compile(
    r"(?i)(?:\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|"
    r"\b\d{1,2}[./-]\d{1,2}[./-](?:19|20)\d{2}\b|"
    r"\b\d{1,2}\s*(?:st|nd|rd|th)?[\s./-]+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\.?[,]?[\s./-]+(?:19|20)\d{2}\b|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[\s./-]+"
    r"\d{1,2}\s*(?:st|nd|rd|th)?[,]?\s+(?:19|20)\d{2}\b)"
)


def _parse_calendar_date(value: object) -> date | None:
    """Parse the date formats accepted by the FTA signature-window audit."""
    text = str(value or "")
    iso = re.search(r"\b((?:19|20)\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text)
    if iso:
        try:
            return date(*(int(part) for part in iso.groups()))
        except ValueError:
            return None

    numeric_day_first = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:19|20)\d{2})\b", text)
    if numeric_day_first:
        day_text, month_text, year_text = numeric_day_first.groups()
        try:
            return date(int(year_text), int(month_text), int(day_text))
        except ValueError:
            return None

    month_numbers = {
        name.casefold(): index
        for index, name in enumerate(calendar.month_name) if name
    }
    month_numbers.update({
        name.casefold(): index
        for index, name in enumerate(calendar.month_abbr) if name
    })
    month_numbers["sept"] = 9
    day_first = re.search(
        r"\b(\d{1,2})\s*(?:st|nd|rd|th)?[\s./-]+([A-Za-z]+)\.?,?[\s./-]+((?:19|20)\d{2})\b",
        text,
    )
    month_first = re.search(
        r"\b([A-Za-z]+)\.?[\s./-]+(\d{1,2})\s*(?:st|nd|rd|th)?[,]?[\s./-]+((?:19|20)\d{2})\b",
        text,
    )
    match = day_first or month_first
    if not match:
        return None
    if day_first:
        day_text, month_text, year_text = match.groups()
    else:
        month_text, day_text, year_text = match.groups()
    month = month_numbers.get(month_text.casefold())
    if month is None:
        return None
    try:
        return date(int(year_text), month, int(day_text))
    except ValueError:
        return None


def extract_verified_fta_signatures(
    question: str,
    sources: object,
    *,
    as_of_date: date | None = None,
) -> list[dict]:
    """Extract direct official signing events inside a requested rolling year.

    A government page can mention an older signature date while announcing an
    implementation date. Prefer an explicit date in a sentence that says the
    agreement was signed; use the page date only for a direct signing release.
    This prevents later implementation notices and general FTA summaries from
    being misclassified as new signatures.
    """
    q = str(question or "")
    if not (
        re.search(r"\b(?:ftas?|free[- ]trade agreements?)\b", q, re.I)
        and re.search(r"\bsign(?:ed|ing)?\b", q, re.I)
    ):
        return []
    today = as_of_date or date.today()
    window = _requested_rolling_year_window(q, today)
    if not window:
        return []
    window_start, window_end = window
    rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    official_domains = ("commerce.gov.in", "pib.gov.in", "mea.gov.in", "egazette.gov.in", "sansad.in")
    agreement_terms = re.compile(r"\b(?:fta|free trade agreement|cepa|ecta|tepa|ceta|trade agreement)\b", re.I)
    active_signing = re.compile(r"\b(?:signed|signs|sign|signing)\b", re.I)
    future_signing = re.compile(
        r"\b(?:will|would|may|expected|scheduled|set|likely|plans?\s+to)\s+(?:be\s+)?sign(?:ed|ing)?\b"
        r"|\bto\s+be\s+signed\b|\bprospect\s+of\s+.*\bsigning\b",
        re.I,
    )
    explicit_signature_action = re.compile(
        r"\bIndia\b[^.!?\n]{0,160}\b(?:today\s+)?(?:signed|signs|sign)\b[^.!?\n]{0,160}"
        r"\b(?:FTA|CEPA|ECTA|TEPA|CETA|free trade agreement|trade agreement)\b"
        r"|\b(?:FTA|CEPA|ECTA|TEPA|CETA|free trade agreement|trade agreement)\b[^.!?\n]{0,120}"
        r"\b(?:was|were|has\s+been|have\s+been)\s+(?:formally\s+|officially\s+)?signed\b",
        re.I,
    )
    dated_signature_fact = re.compile(
        r"\bIndia\b[^.!?\n]{0,180}\b(?:today\s+)?signed\b[^.!?\n]{0,180}"
        r"\b(?:FTA|CEPA|ECTA|TEPA|CETA|free trade agreement|trade agreement)\b"
        r"|\b(?:FTA|CEPA|ECTA|TEPA|CETA|free trade agreement|trade agreement)\b[^.!?\n]{0,140}"
        r"\b(?:was|were|has\s+been|have\s+been)\s+(?:formally\s+|officially\s+)?signed\b",
        re.I,
    )
    partner_pattern = re.compile(
        r"\b[Ii]ndia\s*(?:[-\u2013\u2014]\s*|and\s+)(?:the\s+)?"
        r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})"
        r"(?=.{0,140}\b(?:Free Trade Agreement|FTA|CEPA|ECTA|TEPA|CETA)\b)",
    )
    found: dict[tuple[str, str], dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not _source_has_verified_page(row):
            continue
        source_id = str(row.get("id") or row.get("sourceId") or "").strip().upper()
        if not source_id:
            continue
        host = _source_host(row)
        if not any(host == domain or host.endswith("." + domain) for domain in official_domains):
            continue
        title = str(row.get("title") or row.get("publisher_page_title") or "").strip()
        publisher_text = next((str(row.get(key) or "").strip() for key in (
            "pageContent", "page_content", "publisherEvidenceExcerpt", "publisher_evidence_excerpt",
            "evidenceExcerpt", "evidence_excerpt",
        ) if str(row.get(key) or "").strip()), "")
        if not publisher_text:
            continue
        lead_text = publisher_text[:8_000]
        title_has_agreement = bool(agreement_terms.search(title))
        title_is_signing_release = bool(title_has_agreement and active_signing.search(title) and not future_signing.search(title))
        lead_is_signing_release = bool(
            agreement_terms.search(lead_text)
            and explicit_signature_action.search(lead_text)
            and not future_signing.search(lead_text[:2_500])
        )
        direct_signing_release = title_is_signing_release or lead_is_signing_release

        explicit_event_dates: list[date] = []
        explicit_signature_date_seen = False
        for match in _CALENDAR_DATE_RE.finditer(lead_text):
            parsed = _parse_calendar_date(match.group(0))
            if not parsed:
                continue
            sentence_start = max(
                lead_text.rfind(delimiter, 0, match.start())
                for delimiter in (".", "!", "?", "\n")
            ) + 1
            sentence_end_candidates = [
                position for delimiter in (".", "!", "?", "\n")
                if (position := lead_text.find(delimiter, match.end())) >= 0
            ]
            sentence_end = min(sentence_end_candidates) if sentence_end_candidates else len(lead_text)
            sentence = lead_text[sentence_start:sentence_end]
            if not dated_signature_fact.search(sentence) or future_signing.search(sentence):
                continue
            if not (agreement_terms.search(sentence) or title_has_agreement):
                continue
            explicit_signature_date_seen = True
            if window_start <= parsed <= window_end and parsed not in explicit_event_dates:
                explicit_event_dates.append(parsed)

        event_dates = list(explicit_event_dates)
        if not event_dates and direct_signing_release and not explicit_signature_date_seen:
            # Direct announcements commonly use "today signed" and print the
            # event date at the very top. Only this narrowly classified release
            # may use its publication/header date as the event date.
            header_candidates: list[date] = []
            for key in ("publication_date", "publicationDate", "published", "publishedDate", "publication_date_source"):
                parsed = _parse_calendar_date(row.get(key))
                if parsed and parsed not in header_candidates:
                    header_candidates.append(parsed)
            for match in _CALENDAR_DATE_RE.finditer(publisher_text[:900]):
                parsed = _parse_calendar_date(match.group(0))
                if parsed and parsed not in header_candidates:
                    header_candidates.append(parsed)
            source_url = unquote(_source_url(row))
            for match in _CALENDAR_DATE_RE.finditer(source_url):
                parsed = _parse_calendar_date(match.group(0))
                if parsed and parsed not in header_candidates:
                    header_candidates.append(parsed)
            event_dates = [candidate for candidate in header_candidates if window_start <= candidate <= window_end]
        if not event_dates:
            continue

        partner_match = partner_pattern.search(title) or partner_pattern.search(lead_text)
        partner = re.sub(r"\s+", " ", partner_match.group(1)).strip() if partner_match else ""
        if not partner:
            continue
        agreement = f"India–{partner} FTA"
        for event_date in event_dates:
            key = (event_date.isoformat(), partner.casefold())
            event = {
                "source_id": source_id,
                "source_title": title[:240],
                "source_url": _source_url(row),
                "partner": partner,
                "agreement": agreement,
                "signature_date": event_date,
                "direct_signing_release": direct_signing_release,
                "publisher_evidence_excerpt": publisher_text[:2_500],
            }
            previous = found.get(key)
            if previous is None or (
                bool(event["direct_signing_release"]) and not bool(previous.get("direct_signing_release"))
            ) or (
                bool(event["direct_signing_release"]) == bool(previous.get("direct_signing_release"))
                and len(event["publisher_evidence_excerpt"]) > len(previous["publisher_evidence_excerpt"])
            ):
                found[key] = event
    return sorted(found.values(), key=lambda item: (item["signature_date"], item["partner"].casefold()))


def _citation_ids(text: object) -> set[str]:
    return {value.upper() for value in re.findall(r"(?<![A-Za-z0-9])S\d+\b", str(text or ""), re.I)}


def _date_forms(day: date) -> set[str]:
    abbreviated = day.strftime("%b")
    full = day.strftime("%B")
    year = str(day.year)
    number = str(day.day)
    padded = f"{day.day:02d}"
    return {
        day.isoformat(),
        f"{abbreviated} {number}, {year}", f"{abbreviated} {number} {year}",
        f"{full} {number}, {year}", f"{full} {number} {year}",
        f"{number} {abbreviated} {year}", f"{number} {full} {year}",
        f"{abbreviated} {padded}, {year}", f"{abbreviated} {padded} {year}",
        f"{full} {padded}, {year}", f"{full} {padded} {year}",
        f"{padded} {abbreviated} {year}", f"{padded} {full} {year}",
    }


def _contains_date(report_text: str, day: date) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(report_text or "").casefold()).strip()
    return any(re.sub(r"[^a-z0-9]+", " ", form.casefold()).strip() in normalized for form in _date_forms(day))


def audit_requested_report_scope(
    question: str,
    report_text: str,
    sources: object = None,
    *,
    as_of_date: date | None = None,
) -> dict:
    """Check explicit time and completeness requirements that totals cannot measure.

    This audit is deliberately fail-closed for named, time-bounded tasks. It does
    not infer transactions or agreements; it checks that the report represents
    the requested periods and distinguishes event dates from later status dates.
    """
    q = str(question or "")
    body = str(report_text or "")
    today = as_of_date or date.today()
    flags: list[str] = []
    checks: dict = {}

    promoter_task = bool(re.search(r"\bpromoter", q, re.I) and re.search(
        r"\b(?:stake\s+sales?|exits?|sell(?:ing)?|shareholding|bulk\s+deals?|block\s+deals?)\b", q, re.I
    ))
    quarters = requested_indian_fy_quarters(q, today) if promoter_task else []
    if quarters:
        lines = body.splitlines()
        quarter_rows: dict[str, list[tuple[str, str, set[str]]]] = {
            label: [] for label, _, _ in quarters
        }
        transaction_table_found = False
        no_exit_rows_missing_exchange_evidence: set[str] = set()
        event_rows_missing_exchange_evidence: set[str] = set()
        unknown_rows_missing_citations: set[str] = set()
        official_ids_by_exchange = _official_exchange_source_ids(sources)
        for index, header_line in enumerate(lines[:-1]):
            if header_line.count("|") < 2:
                continue
            separator = lines[index + 1]
            if separator.count("|") < 2 or not re.fullmatch(
                r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*", separator
            ):
                continue
            header_cells = [
                re.sub(r"\s+", " ", cell.casefold()).strip()
                for cell in header_line.strip().strip("|").split("|")
            ]
            has_period_column = any(re.search(r"\b(?:quarter|period|fiscal quarter)\b", cell) for cell in header_cells)
            has_exit_column = any(re.search(
                r"\b(?:promoter\s+)?(?:exits?|stake\s+sales?|sales?|transactions?|disclosures?|filings?|disposals?)\b"
                r"|\b(?:promoter\s+)?(?:activity|event)\b",
                cell,
            ) for cell in header_cells)
            if not (has_period_column and has_exit_column):
                continue

            transaction_table_found = True
            row_index = index + 2
            while row_index < len(lines) and lines[row_index].count("|") >= 2:
                row = lines[row_index].strip()
                row_index += 1
                if re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*", row):
                    continue
                row_cells = [cell.strip() for cell in row.strip("|").split("|")]
                row_text = " | ".join(row_cells)
                cited_ids = _citation_ids(row_text)
                explicit_no_exit = bool(re.search(
                    r"\bno\s+(?:(?:verified|confirmed|reported|disclosed|recorded)\s+){0,2}"
                    r"(?:promoter\s+)?(?:exits?|sales?|disposals?|transactions?|events?)\b",
                    row_text,
                    re.I,
                ))
                unknown_activity = bool(re.search(
                    r"(?i)\bunknown\s*,\s*not\s+zero\b|\bactivity\s+(?:remains\s+)?unknown\b|"
                    r"\bcoverage\s+(?:is\s+)?unknown\b|\btransaction\s+activity\s+not\s+resolved\b",
                    row_text,
                ))
                event_action = bool(re.search(
                    r"\b(?:sold|sell|sells|sale|sales|exited?|exit|dispos(?:al|ed)|divest\w*|"
                    r"offload\w*|offer\s+for\s+sale|OFS|block\s+deal|bulk\s+deal|share\s+transfer)\b",
                    row_text,
                    re.I,
                ))
                if not unknown_activity and not explicit_no_exit and not event_action:
                    continue

                exchanges_cited = {
                    exchange for exchange, ids in official_ids_by_exchange.items()
                    if cited_ids.intersection(ids)
                }
                for label, _, _ in quarters:
                    if not _quarter_label_pattern(label).search(row_text):
                        continue
                    if unknown_activity:
                        quarter_rows[label].append((row, "unknown", cited_ids))
                        if not cited_ids:
                            unknown_rows_missing_citations.add(label)
                    elif explicit_no_exit:
                        if not {"nse", "bse"}.issubset(exchanges_cited):
                            no_exit_rows_missing_exchange_evidence.add(label)
                            quarter_rows[label].append((row, "unverified_no_exit", cited_ids))
                        else:
                            quarter_rows[label].append((row, "no_verified_exit", cited_ids))
                    elif event_action:
                        if not exchanges_cited:
                            event_rows_missing_exchange_evidence.add(label)
                            quarter_rows[label].append((row, "unverified_transaction", cited_ids))
                        else:
                            quarter_rows[label].append((row, "transaction", cited_ids))

        covered = [label for label, _, _ in quarters if quarter_rows[label]]
        missing = [label for label, _, _ in quarters if label not in covered]
        uncited = [
            label for label in covered
            if not any(citations for _, _, citations in quarter_rows[label])
        ]
        checks["promoterQuarterCoverage"] = {
            "requested": len(quarters),
            "representedInTable": len(covered),
            "quarters": [label for label, _, _ in quarters],
            "coveredQuarters": covered,
            "missingQuarters": missing,
            "uncitedQuarters": uncited,
            "transactionTableFound": transaction_table_found,
            "noVerifiedExitRowsMissingBothExchangeCitations": [
                label for label, _, _ in quarters if label in no_exit_rows_missing_exchange_evidence
            ],
            "transactionRowsMissingExchangeCitations": [
                label for label, _, _ in quarters if label in event_rows_missing_exchange_evidence
            ],
            "unknownRowsMissingCitations": [
                label for label, _, _ in quarters if label in unknown_rows_missing_citations
            ],
        }
        if not transaction_table_found:
            flags.append(
                "The promoter analysis needs a quarter-by-quarter transaction or verified-exit coverage table; "
                "ownership snapshots alone do not establish promoter sales or exits."
            )
        if missing:
            flags.append(
                "The promoter-exit table does not represent every requested completed quarter; "
                f"missing {', '.join(missing)}. Add a sourced row or an explicit no-verified-event row for each quarter."
            )
        if uncited:
            flags.append(
                "The promoter period-coverage rows need a source citation for "
                f"{', '.join(uncited)}."
            )
        if no_exit_rows_missing_exchange_evidence:
            flags.append(
                "A quarter with no reported promoter exits must cite verified NSE and BSE evidence for "
                f"{', '.join(label for label, _, _ in quarters if label in no_exit_rows_missing_exchange_evidence)}."
            )
        if event_rows_missing_exchange_evidence:
            flags.append(
                "Promoter transaction rows need a cited verified NSE or BSE disclosure for "
                f"{', '.join(label for label, _, _ in quarters if label in event_rows_missing_exchange_evidence)}."
            )
        if unknown_rows_missing_citations:
            flags.append(
                "Quarter rows marked unknown need a citation to a direct page from the retrieved quarter sample for "
                f"{', '.join(label for label, _, _ in quarters if label in unknown_rows_missing_citations)}."
            )
        exhaustive = bool(re.search(r"\b(?:all|every|exhaustive|complete)\b", q, re.I))
        exchange_coverage = _official_exchange_coverage(sources)
        checks["officialExchangeCoverage"] = exchange_coverage
        if exhaustive and not all(exchange_coverage.values()):
            missing_exchanges = [name.upper() for name, found in exchange_coverage.items() if not found]
            flags.append(
                "An exhaustive promoter-exit list needs verified disclosure coverage from both NSE and BSE; "
                f"missing {', '.join(missing_exchanges)} publisher-page evidence."
            )
        if exhaustive:
            rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
            complete_registry_ids = [
                str(row.get("id") or "").strip().upper()
                for row in rows
                if isinstance(row, dict)
                and _source_has_verified_page(row)
                and (
                    _source_host(row) == "nseindia.com"
                    or _source_host(row).endswith(".nseindia.com")
                    or _source_host(row) == "bseindia.com"
                    or _source_host(row).endswith(".bseindia.com")
                )
                and (
                    row.get("registryCoverageComplete") is True
                    or row.get("registry_coverage_complete") is True
                    or str(row.get("coverageScope") or row.get("coverage_scope") or "").casefold()
                    in {"complete_registry", "complete_exchange_registry", "all_disclosures"}
                )
            ]
            checks["exhaustiveCoverage"] = {
                "requested": True,
                "established": bool(complete_registry_ids),
                "verifiedRegistrySourceIds": sorted(set(value for value in complete_registry_ids if value)),
            }
            if not complete_registry_ids:
                flags.append(
                    "The question asks for an exhaustive promoter-exit universe, but the retrieved research sample has no source marked as a complete exchange disclosure registry."
                )

    fta_task = bool(re.search(r"\b(?:ftas?|free[- ]trade agreements?)\b", q, re.I) and re.search(r"\bsign(?:ed|ing)?\b", q, re.I))
    fta_window = _requested_rolling_year_window(q, today) if fta_task else None
    if fta_window:
        window_start, window_end = fta_window
        explicit_window = _contains_date(body, window_start) and _contains_date(body, window_end)
        if not explicit_window:
            flags.append(
                "The FTA analysis does not state the exact rolling signing window "
                f"{window_start.isoformat()} through {window_end.isoformat()}."
            )

        lines = body.splitlines()
        signature_table = False
        signature_dates_in_window: list[str] = []
        date_proof_lines: list[str] = []
        for index, line in enumerate(lines):
            if line.count("|") < 2:
                continue
            headers = [re.sub(r"\s+", " ", cell.casefold()).strip() for cell in line.strip().strip("|").split("|")]
            date_column = next((i for i, cell in enumerate(headers) if "date" in cell and re.search(r"\b(?:sign|signature)\w*\b", cell)), None)
            has_agreement_column = any(re.search(r"\b(?:fta|agreement|instrument|partner|country)\b", cell) for cell in headers)
            if date_column is None or not has_agreement_column:
                continue
            for candidate in lines[index + 1:]:
                if candidate.count("|") < 2:
                    break
                cells = [cell.strip() for cell in candidate.strip().strip("|").split("|")]
                parsed_date = _parse_calendar_date(cells[date_column]) if date_column < len(cells) else None
                if parsed_date and window_start <= parsed_date <= window_end:
                    signature_table = True
                    signature_dates_in_window.append(parsed_date.isoformat())
                    date_proof_lines.append(candidate)

        explicit_signed_date_lines = [
            line for line in lines
            if re.search(r"(?i)\bsigned\s+(?:on|date\s*[:=])\s*", line)
            and (parsed_date := _parse_calendar_date(line))
            and window_start <= parsed_date <= window_end
        ]
        explicit_signed_dates = bool(explicit_signed_date_lines)
        signature_dates_in_window.extend(
            parsed_date.isoformat()
            for line in explicit_signed_date_lines
            if (parsed_date := _parse_calendar_date(line))
        )
        date_proof_lines.extend(explicit_signed_date_lines)
        no_verified_signing_lines = [
            line for line in lines
            if re.search(
                r"(?i)\b(?:no|none|not\s+one)\b.{0,100}\b(?:fta|free[- ]trade agreement|trade agreement)\b.{0,100}\b(?:signed|verified|confirmed)\b|"
                r"\bno\b.{0,60}\b(?:fta|free[- ]trade agreement|trade agreement)\b.{0,60}\bsigned\b",
                line,
            )
        ]
        no_verified_signing = bool(no_verified_signing_lines)
        no_signing_proof_lines = list(no_verified_signing_lines)
        for index, line in enumerate(lines):
            if line in no_verified_signing_lines:
                no_signing_proof_lines.extend(lines[max(0, index - 1):index])
                no_signing_proof_lines.extend(lines[index + 1:index + 2])
        has_signature_result = signature_table or explicit_signed_dates or no_verified_signing
        proof_lines = date_proof_lines if signature_table or explicit_signed_dates else no_signing_proof_lines
        proof_citations: set[str] = set()
        for line in proof_lines:
            proof_citations.update(_citation_ids(line))
        official_signature_source = _official_fta_signature_source(sources, proof_citations)
        checks["ftaSignatureCoverage"] = {
            "windowStart": window_start.isoformat(),
            "windowEnd": window_end.isoformat(),
            "explicitWindow": explicit_window,
            "datedSignatureTable": signature_table,
            "signatureDatesInWindow": sorted(set(signature_dates_in_window)),
            "explicitSignedDate": explicit_signed_dates,
            "explicitNoVerifiedSigning": no_verified_signing,
            "citedSourceIds": sorted(proof_citations),
            "officialCitedSource": official_signature_source,
        }
        if not has_signature_result:
            flags.append(
                "The FTA list needs an in-window dated signature row, or a source-backed statement that no signing was verified in the window."
            )
        if not official_signature_source:
            flags.append("An in-window FTA signature claim or no-signing result must cite a verified official Indian source.")
        status_header_or_detail = bool(re.search(
            r"(?i)\b(?:entered into force|came into force|effective date|implementation status|implemented on|operational from)\b",
            body,
        ))
        if status_header_or_detail and not re.search(r"(?i)\b(?:signed date|date signed|signature date|signed on)\b", body):
            flags.append("The report discusses implementation/effectiveness dates without separating them from agreement signing dates.")

    return {
        "applicable": bool(checks),
        "status": "incomplete" if flags else ("covered" if checks else "not_applicable"),
        "blocking": bool(flags),
        "flags": flags,
        **checks,
        "note": "Scope checks verify requested periods and coverage disclosures; they do not establish that unlisted events never occurred.",
    }


def audit_research_coverage(
    sources: object,
    *,
    tier: str = "detailed",
    query_count: int = 0,
    requested_pages: int | None = None,
    rendered_pages: int | None = None,
    question: str = "",
) -> dict:
    """Return factual coverage counts plus recommendations; never invent missing data."""
    rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    unique: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _source_url(row)
        if not url:
            continue
        key = url.casefold().rstrip("/")
        if key.startswith("internal://"):
            key = "internal:" + str(row.get("title") or key).casefold()
        unique.setdefault(key, row)
    domains: set[str] = set()
    official: set[str] = set()
    verified = 0
    for row in unique.values():
        url = _source_url(row)
        host = (urlparse(url).hostname or "").casefold()
        if host.startswith("www."):
            host = host[4:]
        if host:
            domains.add(host)
            if any(host == d or host.endswith("." + d) for d in _OFFICIAL_HOSTS):
                official.add(host)
        if _fetched(row):
            verified += 1
    source_count = len(unique)
    norm_tier = str(tier or "detailed").casefold()
    if norm_tier == "in_depth":
        min_sources, min_domains, min_fetched, min_queries = 35, 10, 20, 8
    elif norm_tier == "detailed":
        min_sources, min_domains, min_fetched, min_queries = 20, 8, 10, 5
    else:
        min_sources, min_domains, min_fetched, min_queries = 8, 4, 3, 2
    gaps: list[str] = []
    if source_count < min_sources:
        gaps.append(f"Unique source count {source_count}/{min_sources}")
    if len(domains) < min_domains:
        gaps.append(f"Distinct publisher domains {len(domains)}/{min_domains}")
    if verified < min_fetched:
        gaps.append(f"Publisher pages verified {verified}/{min_fetched}")
    if int(query_count or 0) < min_queries:
        gaps.append(f"Independent search angles {int(query_count or 0)}/{min_queries}")
    if not official:
        gaps.append("No official/regulatory source domain detected")
    if requested_pages and rendered_pages is not None and rendered_pages < requested_pages:
        gaps.append(f"Rendered pages {rendered_pages}/{requested_pages}")

    # For an FII/FPI-flow hypothesis, source totals alone are not enough: a
    # collection of 40 equity-outflow articles could still omit primary issues,
    # debt, FDI or the expert's attributed claim. Track substantive dimensions
    # independently and report gaps without pretending search absence is proof
    # that a channel has no activity.
    question_text = str(question or "").casefold()
    fpi_topic = bool(re.search(r"\b(?:fii|fiis|fpi|fpis|foreign institutional investors?|foreign portfolio investors?)\b", question_text) and
                     re.search(r"\b(?:india|indian|nifty|sensex|nse|bse)\b", question_text))
    topic_dimension_coverage: dict[str, bool] = {}
    if fpi_topic:
        corpus = " ".join(
            " ".join(str(row.get(k) or "") for k in ("title", "snippet", "fullContent", "pageContent", "page_content"))
            for row in unique.values()
        ).casefold()
        patterns = {
            "portfolioFlows": r"\b(?:fii|fiis|fpi|fpis|foreign portfolio|foreign institutional|net equity|outflows?|inflows?|portfolio investment)\b",
            "frontlineOwnership": r"\b(?:nifty ?50|sensex|frontline stocks?|shareholding pattern|foreign shareholding|secondary market|ownership)\b",
            "primaryMarket": r"\b(?:ipo|ipos|qip|qips|primary market|public issue|public offering|new issuance)\b",
            "directInvestmentFDI": r"\b(?:foreign direct investment|\bfdi\b|dpiit|greenfield|brownfield)\b",
            "debtInvestment": r"\b(?:debt securities|government securities|corporate bonds?|far|vrr|bond inflows?|debt investment)\b",
            "sectoralAllocation": r"\b(?:sectoral|sector wise|sector-wise|financial services|capital goods|information technology|automobiles?|healthcare)\b",
            "macroDrivers": r"\b(?:rupee|currency depreciation|us treasury|bond yields?|emerging markets?|risk.off|valuation|earnings growth|geopolitical)\b",
        }
        if re.search(r"\bgautam duggad\b", question_text):
            patterns["GautamDuggadClaim"] = r"\bgautam duggad\b"
        topic_dimension_coverage = {name: bool(re.search(pattern, corpus, re.I)) for name, pattern in patterns.items()}
        missing_dimensions = [name for name, found in topic_dimension_coverage.items() if not found]
        # The FPI question specifically requests a multi-channel explanation;
        # surface each uncovered channel as a gap rather than automatically
        # blocking publication or interpreting it as zero investment.
        for dimension in missing_dimensions:
            gaps.append(f"FPI research dimension not represented in retrieved sources: {dimension}")
    else:
        missing_dimensions = []

    return {
        "tier": norm_tier,
        "status": "adequate" if not gaps else "thin",
        "targetSources": {"minimum": min_sources, "preferredMaximum": 50 if norm_tier == "in_depth" else min_sources},
        "queryAngleCount": int(query_count or 0),
        "uniqueSourceCount": source_count,
        "distinctPublisherDomains": len(domains),
        "publisherPagesVerified": verified,
        "officialSourceCount": len(official),
        "officialSourceDomains": sorted(official),
        "topicDimensionCoverage": topic_dimension_coverage,
        "missingTopicDimensions": missing_dimensions,
        "requestedPages": requested_pages,
        "renderedPages": rendered_pages,
        "coverageGaps": gaps,
        "note": "Coverage counts are diagnostic; they do not prove each claim is correct or that every relevant perspective was found.",
    }
