"""Extract narrowly scoped promoter-sale facts from fetched exchange filings.

The extractor is intentionally conservative: it never treats a search snippet,
shareholding snapshot, pledge, proposed offer, or inter-promoter transfer as a
completed promoter exit. Missing dates and reasons stay explicitly unknown.
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from utils.deep_research_coverage import (
    _CALENDAR_DATE_RE,
    _parse_calendar_date,
    requested_indian_fy_quarters,
)


_ACTION_RE = re.compile(
    r"\b(?:sold|sell(?:s|ing)?|dispos(?:al|ed)|divest\w*|offload\w*|"
    r"transfer(?:red)?|sale|offer\s+for\s+sale|OFS)\b", re.I,
)
_PROMOTER_RE = re.compile(r"\b(?:promoters?|promoter\s+group|promoter\s+seller)\b", re.I)
_SELLER_RE = re.compile(r"\b(?:seller|sellers|selling\s+shareholders?)\b", re.I)
_PROMOTER_ACTOR_RE = re.compile(
    r"\bpromoters?\b(?:\s+(?:group|shareholders?))?[^.!?;\n]{0,90}"
    r"\b(?:propos\w*|intend\w*|will|shall|sold|sell(?:s|ing)?|"
    r"dispos\w*|divest\w*|offload\w*|transfer(?:red|s|ring)?)\b",
    re.I,
)
_SHARE_ASSET_RE = re.compile(
    r"\b(?:equity\s+shares?|shares?|shareholding|share\s+capital|paid[- ]up\s+capital|stake|holdings?)\b",
    re.I,
)
_NEGATIVE_RE = re.compile(
    r"\b(?:no|not|never|without)\b.{0,80}\b(?:sold|sell|sale|dispos(?:al|ed)|"
    r"divest\w*|offload\w*|transfer(?:red)?)\b", re.I,
)
_PROPOSED_RE = re.compile(
    r"\b(?:propos\w*|intend\w*|intention|notice of|offer for sale|OFS|"
    r"will sell|to sell|to be sold|shall sell)\b", re.I,
)
_COMPLETED_RE = re.compile(
    r"\b(?:has\s+sold|have\s+sold|had\s+sold|sold\s+or\s+disposed|"
    r"has\s+disposed|have\s+disposed|disposed\s+of|has\s+divested|have\s+divested|"
    r"divested\s+(?:its|their|his|her)\s+holding|has\s+transferred|have\s+transferred|"
    r"transferred\s+(?:the\s+)?(?:entire\s+)?(?:shareholding|shares?|holding)|"
    r"completed\s+(?:the\s+)?(?:transfer|sale)|consummat\w*|"
    r"\b(?:sold|disposed|divested|transferred)\b)", re.I,
)
_INTER_SE_RE = re.compile(r"\binter[- ]se\b|between\s+(?:the\s+)?promoters?", re.I)
_AGREEMENT_RE = re.compile(
    r"\b(?:share purchase agreement|share sale agreement|SPA|agreement to sell|"
    r"entered into.{0,60}agreement)\b", re.I,
)
_GENERIC_TITLE_RE = re.compile(
    r"(?i)^(?:date\s*:|subject\s*:|sub\s*:|dear\b|to\s*,|the manager\b|"
    r"bseindia\b|nseindia\b|notice\s*$|covering letter\b|disclosure\s+under\b)"
)
_COMPANY_SUFFIX_RE = re.compile(
    r"(?i)\b(?:limited|ltd\.?|industries|technologies|sciences|retail|pharmaceuticals|"
    r"pharma|corporation|corporate|company|enterprises|motors|finance|financial|chemicals|"
    r"defence|defense|textiles|power|energy|foods|cement|metals|systems)\b"
)


def _is_official_exchange_url(value: object) -> bool:
    host = (urlparse(str(value or "")).hostname or "").casefold().removeprefix("www.")
    return any(host == domain or host.endswith("." + domain) for domain in ("nseindia.com", "bseindia.com"))


def _direct_page_text(row: dict) -> str:
    status = str(row.get("pageFetchStatus") or row.get("page_fetch_status") or "").casefold()
    if status not in {"ok", "success", "fetched"}:
        return ""
    return str(
        row.get("pageContent") or row.get("page_content")
        or row.get("publisherEvidenceExcerpt") or row.get("publisher_evidence_excerpt") or ""
    ).strip()


def _normalize_space(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _action_contexts(text: str):
    for action in _ACTION_RE.finditer(text[:100_000]):
        left = max(0, action.start() - 260)
        right = min(len(text), action.end() + 260)
        context = text[left:right]
        if _NEGATIVE_RE.search(context):
            continue
        inter_se_context = bool(_INTER_SE_RE.search(context))
        direct_role = bool(
            _PROMOTER_RE.search(context)
            and _SHARE_ASSET_RE.search(context)
            and (_SELLER_RE.search(context) or _PROMOTER_ACTOR_RE.search(context) or inter_se_context)
        )
        if direct_role:
            yield action, left, right, context
            continue

        # Some exchange PDFs introduce the promoter sellers in a paragraph,
        # then list several trades as date-led bullets. Carry that role only
        # when a completed-action date is immediately attached to this bullet.
        date_match = _date_match_near_action(text, action.start(), action.end())
        inherited_context = text[max(0, action.start() - 500):min(len(text), action.end() + 320)]
        inherited_role = bool(
            date_match
            and min(abs(date_match[1] - action.end()), abs(action.start() - date_match[2])) <= 120
            and re.search(r"(?i)\b(?:sold|disposed|divested|offloaded|transferred)\b", context)
            and _PROMOTER_ACTOR_RE.search(inherited_context)
            and _SHARE_ASSET_RE.search(context)
        )
        if not inherited_role:
            continue
        yield action, max(0, action.start() - 500), min(len(text), action.end() + 320), inherited_context


def _date_match_near_action(text: str, action_start: int, action_end: int) -> tuple[date, int, int] | None:
    candidates: list[tuple[int, int, date, int, int]] = []
    search_start = max(0, action_start - 300)
    search_end = min(len(text), action_end + 300)
    for match in _CALENDAR_DATE_RE.finditer(text[search_start:search_end]):
        absolute_start = search_start + match.start()
        absolute_end = search_start + match.end()
        parsed = _parse_calendar_date(match.group(0))
        if not parsed:
            continue
        before = text[max(0, absolute_start - 100):absolute_start]
        after = text[absolute_end:min(len(text), absolute_end + 100)]
        before_clause_start = max(before.rfind("."), before.rfind("!"), before.rfind("?")) + 1
        before_clause = before[before_clause_start:]
        after_boundary = re.search(r"[.!?](?:\s|$)", after)
        after_clause = after[:after_boundary.start()] if after_boundary else after
        # PDF tables can place the transfer date on a line shortly before a
        # repeated table heading (for example, "Share Transfer"). Keep a wider
        # local range while still tying the date to the same evidence passage.
        date_near_action = min(abs(absolute_start - action_end), abs(action_start - absolute_end)) <= 360
        explicit_date_role = bool(
            re.search(r"(?i)\b(?:on|as of|as at|as on)\s*$", before)
            or re.search(r"(?i)\bdate of (?:transfer|sale|transaction)\b[^\n]{0,60}$", before)
            or re.search(r"(?i)\b(?:transaction|transfer|sale) date\b[^\n]{0,60}$", before)
            or re.search(r"(?i)^\s*(?:on|as of|as at)\b", after)
        )
        agreement_date = bool(re.search(
            r"(?i)\b(?:SPA|share\s+purchase\s+agreement|share\s+sale\s+agreement|"
            r"agreement|letter\s+of\s+offer|public\s+announcement)\s+(?:dated|of|on)\s*$",
            before_clause[-140:],
        ))
        explicit_completion = bool(re.search(
            r"(?i)\b(?:completed|consummat\w*|completion of (?:the )?(?:transfer|sale|transaction)|"
            r"date of (?:transfer|sale|transaction))\b",
            before_clause[-140:] + " " + after_clause[:100],
        ))
        if not date_near_action or not explicit_date_role:
            continue
        if agreement_date and not explicit_completion:
            continue
        distance = 0 if absolute_start <= action_end and absolute_end >= action_start else min(
            abs(absolute_start - action_start), abs(absolute_end - action_end),
        )
        candidates.append((0 if explicit_completion else 1, distance, parsed, absolute_start, absolute_end))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    _completion_rank, _distance, parsed, absolute_start, absolute_end = candidates[0]
    return parsed, absolute_start, absolute_end


def _date_near_action(text: str, action_start: int, action_end: int) -> date | None:
    """Compatibility wrapper for callers that only need the parsed date."""
    match = _date_match_near_action(text, action_start, action_end)
    return match[0] if match else None


def _filing_date(row: dict, text: str) -> date | None:
    for key in ("published", "publishedDate", "publication_date", "publicationDate"):
        value = row.get(key)
        parsed = next((
            _parse_calendar_date(match.group(0))
            for match in _CALENDAR_DATE_RE.finditer(str(value or ""))
            if _parse_calendar_date(match.group(0))
        ), None)
        if parsed:
            return parsed
    title = str(row.get("title") or row.get("publisherPageTitle") or row.get("publisher_page_title") or "")
    title_date = next((
        _parse_calendar_date(match.group(0))
        for match in _CALENDAR_DATE_RE.finditer(title)
        if _parse_calendar_date(match.group(0))
    ), None)
    if title_date:
        return title_date
    header_match = re.search(
        r"(?im)^\s*(?:date|filing\s+date|disclosure\s+date)\s*[:\-]\s*([^\n|]{6,50})",
        text[:2_000],
    )
    if header_match:
        return next((
            _parse_calendar_date(match.group(0))
            for match in _CALENDAR_DATE_RE.finditer(header_match.group(1))
            if _parse_calendar_date(match.group(0))
        ), None)
    today_match = re.search(r"(?i)\btoday\s*\(?\s*([^\n),]{6,40})", text[:1_200])
    if today_match:
        return next((
            _parse_calendar_date(match.group(0))
            for match in _CALENDAR_DATE_RE.finditer(today_match.group(1))
            if _parse_calendar_date(match.group(0))
        ), None)
    return None


def _company_name(row: dict, text: str, context: str) -> str:
    for value in (
        row.get("company"), row.get("companyName"), row.get("company_name"),
        row.get("issuer"), row.get("entityName"), row.get("entity_name"), row.get("title"),
    ):
        title = _normalize_space(value).strip(" ,;:-_&")
        if not title or _GENERIC_TITLE_RE.search(title):
            continue
        if re.search(r"(?i)\b(?:department|corporate\s+services|listing|compliance|exchange\s+plaza|stock\s+exchange)\b", title):
            continue
        if title.casefold().strip(" .,:;-_") in {"company", "the company", "target company", "our company"}:
            continue
        if re.match(r"(?i)^(?:bse limited|national stock exchange|nse limited|the manager|the listing department)\b", title):
            continue
        if _COMPANY_SUFFIX_RE.search(title) and len(title) <= 110:
            return title.strip(" ,;:-")
    for candidate_text in (context, text[:12_000]):
        patterns = (
            r"(?i)(?=\b([A-Z][A-Za-z0-9&.,'()/-]*(?:\s+[A-Z][A-Za-z0-9&.,'()/-]*){0,7}\s+(?:Limited|Ltd\.?))\s*\(\s*(?:the\s+)?[\W_]*Company[\W_]*\s*\))",
            r"(?i)\b(?:promoters?|promoter\s+seller|seller|sale|shares?|stake)\b[^.!?\n]{0,160}?\b(?:of|in)\s+([A-Z][A-Za-z0-9&.,'()/-]*(?:\s+[A-Z][A-Za-z0-9&.,'()/-]*){0,7}\s+(?:Limited|Ltd\.?|Industries|Technologies|Sciences|Retail|Pharma|Corporation|Company))\b",
            r"(?i)(?:sale|shares?|stake|promoter|seller)[^.!?\n]{0,100}?\bof\s+([A-Z][A-Za-z0-9&.,'()/-]*(?:\s+[A-Z][A-Za-z0-9&.,'()/-]*){0,7}\s+(?:Limited|Ltd\.?|Industries|Technologies|Sciences|Retail|Corporation|Company))\b",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, candidate_text):
                candidate = _normalize_space(match.group(1)).strip(" ,;:-")
                candidate = re.sub(
                    r"(?i)^(?:(?:the\s+)?board\s+of\s+directors|directors)\s+of\s+|^(?:of|in|by)\s+",
                    "", candidate,
                )
                candidate = re.sub(
                    r"(?i)^(?:equity\s+shares?\s+of|shares?\s+of|stake\s+in|our\s+company)\s+",
                    "", candidate,
                )
                if (
                    candidate.casefold() not in {"company", "the company", "target company", "our company"}
                    and not re.search(r"(?i)\b(?:the\s+company|target\s+company|control\s+of|board|directors\s+of|shares?\s+of|shareholding\s+of)\b", candidate)
                    and not re.search(r"(?i)\b(?:BSE\s+Limited|National\s+Stock\s+Exchange|NSE\s+Limited|TC\s+are\s+Listed|stock\s+exchange)\b", candidate)
                ):
                    return candidate
    letterhead_pattern = re.compile(
        r"(?im)^\s*(?:\[PDF page \d+\]\s*)?([A-Z][A-Za-z0-9&.,'()/-]*(?:\s+[A-Z][A-Za-z0-9&.,'()/-]*){0,7}\s+(?:Limited|Ltd\.?|Industries|Technologies|Sciences|Retail|Pharma|Corporation|Company))\s*$",
    )
    for letterhead in letterhead_pattern.finditer(text[:4_000]):
        candidate = _normalize_space(letterhead.group(1)).strip(" ,;:-")
        if not re.search(r"(?i)\b(?:BSE|NSE|National Stock Exchange|stock exchange|department|corporate services|listing|compliance)\b", candidate):
            return candidate
    return "Company not identified in fetched filing text"


def _seller_name(text: str, context: str) -> str:
    patterns = (
        r"(?i)\bI\s*,?\s*((?:Mr\.?|Mrs\.?|Ms\.?|Smt\.?|Shri\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})\s*,?\s*\([^)]*\bpromoter\s+seller\b",
        r"(?i)\bI\s*,?\s*((?:Mr\.?|Mrs\.?|Ms\.?|Smt\.?|Shri\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})\s*\([^)]*\b(?:promoter\s+)?seller\b",
        r"(?i)\b((?:Mr\.?|Mrs\.?|Ms\.?|Smt\.?|Shri\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})\s*,?\s*\((?:the\s+)?promoter\s+seller\)",
        r"(?i)\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,5})\s*\([^)]*\bSeller\b",
        r"(?i)\b([A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*){0,5})\s+is\s+(?:the\s+)?promoter\s+(?:and\s+)?(?:the\s+)?seller\b",
        r"(?i)\bSeller\s*\([^)]*\)\s*[-–:]\s*([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,4})",
        r"(?is)\bpromoters?\s+of\s+the\s+company,?\s+namely\s+([\s\S]{2,140}?)\s+(?:have|has)\s+sold\b",
        r"(?is)\bpromoters?\b[^.!?]{0,70}?\bnamely\s+([\s\S]{2,140}?)\s+(?:have|has|sold|are|were)\b",
        r"(?i)promoters?\s+(?:of\s+the\s+company\s+)?(?:namely|being)\s+([^.;\n]{2,110})",
    )
    for candidate_text in (context, text[:4_000]):
        for pattern in patterns:
            match = re.search(pattern, candidate_text)
            if match:
                candidate = _normalize_space(match.group(1)).strip(" ,;:-")
                candidate = re.sub(r"(?i)\s+(?:regarding|in relation to)\s+(?:the\s+)?sale\b.*$", "", candidate)
                if re.search(r"(?i)\b(?:is|are)\s+the\s+promoter|of\s+the\s+company\b", candidate):
                    continue
                return candidate
    return "Promoter seller named in filing; individual not isolated"


def _shares_and_percentage(context: str) -> tuple[str, str]:
    number = r"(\d[\d,]*(?:\.\d+)?)"
    shares = re.search(
        rf"(?i)\btotal(?:ing)?\s+(?:of\s+)?{number}(?:\s*\([^)]{{0,140}}\))?\s+(?:equity\s+)?shares?\b",
        context,
    ) or re.search(
        rf"(?i)\b(?:sold(?:\s+or\s+disposed)?|sell(?:s|ing)?|sale\s+of|"
        rf"dispos(?:ed|al)\s+(?:of\s+)?|transfer(?:red)?\s+(?:of\s+)?)"
        rf"\s*(?:up\s+to\s+)?{number}(?:\s*\([^)]{{0,140}}\))?\s+(?:equity\s+)?shares?\b",
        context,
    )
    percentage = None
    if shares:
        percentage = re.search(r"\b(\d+(?:\.\d+)?)\s*%", context[shares.end():shares.end() + 120])
    return (
        shares.group(1) if shares else "Not stated in extracted transaction passage",
        f"{percentage.group(1)}%" if percentage else "Not stated in extracted transaction passage",
    )


def _transaction_detail_after_date(text: str, date_start: int, date_end: int) -> str:
    """Return the date-led transaction detail without importing a later bullet."""
    end = min(len(text), date_end + 420)
    for match in _CALENDAR_DATE_RE.finditer(text, date_end, end):
        end = match.start()
        break
    return text[date_start:end]


def _stated_reason(context: str) -> str:
    match = re.search(
        r"(?i)\b(?:to comply with|in order to|for achieving|because|due to|on account of|"
        r"for the purpose of|as part of|towards meeting)\b[^.!?\n]{0,180}",
        context,
    )
    if not match:
        return "The fetched transaction passage does not state a reason."
    return _normalize_space(match.group(0))


def _event_status(text: str, action: re.Match[str]) -> str:
    local_start = max(0, action.start() - 260)
    local_end = min(len(text), action.end() + 260)
    local = text[local_start:local_end]
    if _INTER_SE_RE.search(local):
        return "inter_se_transfer"
    preceding = text[max(0, action.start() - 80):action.start()]
    future_action = bool(re.search(
        r"(?i)\b(?:propos\w*|intend\w*|will|shall|to\s+be)\b[^.!?\n]{0,55}$",
        preceding,
    ))
    future_sale_clause = bool(re.search(
        r"(?i)\b(?:propos\w*|intend\w*|will|shall)\b[^.!?]{0,160}"
        r"\b(?:sell|sold|sale|dispos\w*|divest\w*|transfer\w*)\b",
        local,
    ))
    proposed = bool(_PROPOSED_RE.search(local))
    completed = bool(_COMPLETED_RE.search(local)) and not future_action
    if future_action or future_sale_clause or (proposed and not completed):
        return "proposed"
    if completed:
        return "completed"
    if _AGREEMENT_RE.search(local):
        return "agreement_only"
    return "sale_disclosure_status_unclear"


def extract_verified_promoter_events(
    question: str,
    sources: object,
    *,
    as_of_date: date | None = None,
) -> list[dict]:
    """Return deduplicated, direct NSE/BSE promoter-sale passages.

    Events without an exact transaction date are retained as disclosures but are
    not assigned to a transaction quarter. The caller can present them without
    turning the filing date or SPA date into a sale date.
    """
    quarters = requested_indian_fy_quarters(question, as_of_date)
    if not quarters:
        return []
    window_start, window_end = quarters[0][1], quarters[-1][2]
    rows = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    best_by_event: dict[tuple[str, str, str], tuple[int, dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("pageFetchFinalUrl") or row.get("pageFetchUrl") or row.get("url") or ""
        if not _is_official_exchange_url(url):
            continue
        body = _direct_page_text(row)
        if len(body) < 180:
            continue
        source_id = str(row.get("id") or row.get("sourceId") or "").strip().upper()
        if not re.fullmatch(r"S\d+", source_id):
            continue
        title = _normalize_space(row.get("title") or row.get("publisherPageTitle") or row.get("publisher_page_title"))
        disclosure_date = _filing_date(row, body)
        for action, left, right, context in _action_contexts(body):
            status = _event_status(body, action)
            date_match = _date_match_near_action(body, action.start(), action.end()) if status == "completed" else None
            transaction_date = date_match[0] if date_match else None
            if status in {"proposed", "agreement_only", "inter_se_transfer"}:
                # A notice/SPA date is useful as a disclosure date, but it is not
                # presented as a completed transaction date.
                transaction_date = None
            if transaction_date and not (window_start <= transaction_date <= window_end):
                continue
            if not transaction_date and disclosure_date and not (window_start <= disclosure_date <= window_end):
                continue
            detail_context = (
                _transaction_detail_after_date(body, date_match[1], date_match[2])
                if transaction_date and date_match else ""
            )
            local_context = " ".join(part for part in (detail_context, context) if part)
            company = _company_name(row, body, local_context)
            seller = _seller_name(body, local_context)
            detail_shares, detail_percentage = _shares_and_percentage(detail_context)
            context_shares, context_percentage = _shares_and_percentage(context)
            shares = detail_shares if detail_shares != "Not stated in extracted transaction passage" else context_shares
            percentage = detail_percentage if detail_percentage != "Not stated in extracted transaction passage" else context_percentage
            reason = _stated_reason(local_context)
            if re.search(r"(?i)\b(?:entire|all)\s+(?:the\s+)?(?:promoter\s+)?(?:shareholding|holding|stake)\b|\bnil shares\b", local_context):
                exit_size = "Full exit stated in the filing"
            else:
                exit_size = "Partial or extent not established in the extracted passage"
            quarter = None
            if transaction_date:
                quarter = next((label for label, start, end in quarters if start <= transaction_date <= end), None)
                if quarter is None:
                    continue
            excerpt = _normalize_space(context)
            excerpt = excerpt[:720]
            event = {
                "source_id": source_id,
                "url": str(row.get("url") or url),
                "title": title,
                "company": company,
                "seller": seller,
                "status": status,
                "event_date": transaction_date,
                "disclosure_date": disclosure_date,
                "quarter": quarter,
                "shares": shares,
                "percentage": percentage,
                "exit_size": exit_size,
                "reason": reason,
                "excerpt": excerpt,
            }
            score = (
                40 if status == "completed" else 28 if status == "proposed" else 18 if status == "agreement_only" else 12
            ) + (24 if transaction_date else 0) + (8 if shares != "Not stated in extracted transaction passage" else 0) + (
                5 if percentage != "Not stated in extracted transaction passage" else 0
            ) + (3 if company != "Company not identified in fetched filing text" else 0)
            event_key = (
                source_id,
                status if transaction_date else "undated",
                transaction_date.isoformat() if transaction_date else "",
            )
            previous = best_by_event.get(event_key)
            if previous is None or score > previous[0] or (score == previous[0] and len(excerpt) > len(previous[1]["excerpt"])):
                best_by_event[event_key] = (score, event)

    source_ids_with_dated_completion = {
        event["source_id"] for _score, event in best_by_event.values()
        if event.get("status") == "completed" and event.get("event_date")
    }
    unique_events: dict[tuple[str, str, str, str, str, str], dict] = {}
    for _score, event in best_by_event.values():
        if (
            event.get("source_id") in source_ids_with_dated_completion
            and not event.get("event_date")
        ):
            continue
        signature = (
            event.get("status") or "",
            event.get("event_date").isoformat() if event.get("event_date") else "",
            str(event.get("company") or "").casefold(),
            str(event.get("seller") or "").casefold(),
            str(event.get("shares") or "").casefold(),
            str(event.get("excerpt") or "")[:150].casefold(),
        )
        existing = unique_events.get(signature)
        if existing is None or event["source_id"] < existing["source_id"]:
            unique_events[signature] = event
    return sorted(
        unique_events.values(),
        key=lambda event: (
            event.get("event_date") or event.get("disclosure_date") or date.max,
            event.get("source_id") or "",
        ),
    )


def promoter_event_counts(events: list[dict], quarters: list[tuple[str, date, date]]) -> dict[str, int]:
    """Count only completed sales with a verified event date in each quarter."""
    return {
        label: sum(1 for event in events if event.get("status") == "completed" and event.get("quarter") == label)
        for label, _start, _end in quarters
    }


def render_promoter_evidence_report(
    question: str,
    events: list[dict],
    publisher_sources: list[dict],
    *,
    as_of_date: date | None = None,
) -> tuple[str, dict]:
    """Render a conservative report from the direct promoter-filing ledger."""
    quarters = requested_indian_fy_quarters(question, as_of_date)
    if not quarters:
        return "", {"applicable": False}
    counts = promoter_event_counts(events, quarters)
    quarter_labels = [label for label, _start, _end in quarters]
    start_date, end_date = quarters[0][1], quarters[-1][2]

    exchange_ids: dict[str, str] = {}
    source_by_id: dict[str, dict] = {}
    quarter_page_ids: dict[str, list[str]] = {label: [] for label, _start, _end in quarters}
    for source in publisher_sources or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip().upper()
        url = source.get("pageFetchFinalUrl") or source.get("pageFetchUrl") or source.get("url") or ""
        if not re.fullmatch(r"S\d+", source_id) or not _is_official_exchange_url(url) or not _direct_page_text(source):
            continue
        source_by_id[source_id] = source
        host = (urlparse(str(url)).hostname or "").casefold()
        if (host == "nseindia.com" or host.endswith(".nseindia.com")) and "nse" not in exchange_ids:
            exchange_ids["nse"] = source_id
        if (host == "bseindia.com" or host.endswith(".bseindia.com")) and "bse" not in exchange_ids:
            exchange_ids["bse"] = source_id
        filing_day = _filing_date(source, _direct_page_text(source))
        if filing_day:
            quarter = next((label for label, start, end in quarters if start <= filing_day <= end), None)
            if quarter:
                quarter_page_ids[quarter].append(source_id)

    dated_completed = [
        event for event in events
        if event.get("status") == "completed" and event.get("event_date") and event.get("quarter")
    ]
    completed_date_unknown = [
        event for event in events
        if event.get("status") == "completed" and not event.get("event_date")
    ]
    status_unclear = [event for event in events if event.get("status") == "sale_disclosure_status_unclear"]
    proposed = [event for event in events if event.get("status") == "proposed"]
    agreement_only = [event for event in events if event.get("status") == "agreement_only"]
    inter_se = [event for event in events if event.get("status") == "inter_se_transfer"]

    def _refs(ids) -> str:
        return " ".join(f"[{source_id}]" for source_id in dict.fromkeys(
            str(value).strip().upper() for value in ids if re.fullmatch(r"S\d+", str(value).strip().upper())
        ))

    def _display_event(event: dict) -> str:
        event_date = event.get("event_date")
        date_text = event_date.strftime("%d %b %Y") if event_date else "event date not stated"
        company = _normalize_space(event.get("company")) or "Issuer not resolved from filing"
        seller = _normalize_space(event.get("seller")) or "Seller not isolated in filing"
        shares = _normalize_space(event.get("shares"))
        percentage = _normalize_space(event.get("percentage"))
        detail = f"{date_text}: {company}; seller: {seller}; shares: {shares}; stake: {percentage}"
        return f"{detail} [{event['source_id']}]"

    def _table_cell(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "\\|")

    lines = [
        "## Scope and method",
        f"The requested window contains eight completed Indian fiscal quarters, {quarter_labels[0]} through {quarter_labels[-1]} ({start_date:%d %b %Y} to {end_date:%d %b %Y}).",
        "This report counts direct NSE/BSE publisher-page passages that identify a promoter or promoter-group seller and a completed transfer or sale of equity shares. An SPA or proposed offer is not counted as a completed sale; an inter-se transfer is excluded.",
        "The retrieved filing set is a research sample, not a complete issuer-by-issuer registry. A quarter with no verified transaction date is marked unknown; that does not mean there were zero sales.",
        "",
        "## Quarter-by-quarter coverage",
        "| Fiscal quarter | Verified completed promoter sales dated in quarter | Other sale-related records found in quarter | Coverage result | Direct pages reviewed in quarter |",
        "| --- | --- | --- | --- | --- |",
    ]
    method_ids = [exchange_ids.get("nse"), exchange_ids.get("bse")]
    method_refs = _refs([source_id for source_id in method_ids if source_id])
    for label in quarter_labels:
        dated_rows = [event for event in dated_completed if event.get("quarter") == label]
        disclosure_rows = [
            event for event in events
            if event.get("disclosure_date")
            and next((q for q, start, end in quarters if start <= event["disclosure_date"] <= end), None) == label
            and event not in dated_rows
        ]
        if dated_rows:
            dated_text = "; ".join(
                f"Verified completed promoter sale: {event['company']} — {event['shares']} shares on {event['event_date']:%d %b %Y} [{event['source_id']}]"
                for event in dated_rows
            )
            coverage = f"{counts[label]} verified completed sale transaction(s) dated to this quarter; this is not a count of distinct promoters or full exits."
            refs = _refs([event["source_id"] for event in dated_rows + disclosure_rows] + quarter_page_ids[label])
        else:
            dated_text = "No quarter-dated completed promoter sale was isolated from the retrieved pages."
            coverage = "The retrieved sample does not resolve quarter activity; it is unknown, not zero."
            refs = _refs([event["source_id"] for event in disclosure_rows] + quarter_page_ids[label])
        other_text = "; ".join(
            f"{event['status'].replace('_', ' ')} disclosure dated {event['disclosure_date']:%d %b %Y} [{event['source_id']}]"
            for event in disclosure_rows
        ) or "No other quarter-dated sale-related record resolved in this sample."
        lines.append(f"| {label} | {dated_text} | {other_text} | {coverage} | {refs or 'No direct exchange citation available'} |")

    lines.extend([
        "",
            "Quarter references identify direct exchange pages dated in that quarter or transaction-specific filings. They show the retrieved sample, not full market coverage.",
        "",
        "## Verified completed sale transactions with an exact event date",
        "| Event date | Quarter | Issuer | Promoter / seller | Shares reported | Stake reported | Full-exit status | Filing-stated reason | Evidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    if dated_completed:
        for event in dated_completed:
            reason = event.get("reason") or "The fetched filing passage does not state a reason."
            lines.append(
                f"| {event['event_date']:%d %b %Y} | {event['quarter']} | {_table_cell(event['company'])} | {_table_cell(event['seller'])} | "
                f"{_table_cell(event['shares'])} | {_table_cell(event['percentage'])} | {_table_cell(event['exit_size'])} | {_table_cell(reason)} | [{event['source_id']}] |"
            )
    else:
        lines.append("| No event date verified | — | — | — | — | — | Not established | No completed sale is counted without a dated direct filing passage. | — |")

    if completed_date_unknown:
        lines.extend([
            "",
            "### Completed-sale disclosures whose transaction date is not established",
            "These fetched filings report a completed transfer or sale, but the extracted passage does not establish the underlying transaction date. They are not counted as quarter-dated transactions or full exits.",
        ])
        lines.extend(f"- {_display_event(event)}. Reason: {event.get('reason') or 'not stated in the fetched passage.'}" for event in completed_date_unknown)

    if status_unclear:
        lines.extend([
            "",
            "### Sale-related disclosures with completion or event-date gaps",
            "These records mention a promoter-linked sale or disposal but the fetched passage does not establish a completed, quarter-dated transaction. They are excluded from the completed-sale count.",
        ])
        lines.extend(f"- {_display_event(event)}. Status: completion or transaction date not established. [{event['source_id']}]" for event in status_unclear)

    if proposed:
        lines.extend([
            "",
            "### Proposed offers and sales — not completed exits",
        ])
        lines.extend(
            f"- {event.get('disclosure_date').strftime('%d %b %Y') if event.get('disclosure_date') else 'Disclosure date not established'}: "
            f"{event['company']}; {event['shares']} shares; {event['percentage']}; reason: {event['reason']} [{event['source_id']}]."
            for event in proposed
        )
    if agreement_only:
        lines.extend(["", "### Agreements without verified completion"])
        lines.extend(f"- {_display_event(event)}. The fetched passage records an agreement, not a verified completed exit." for event in agreement_only)
    if inter_se:
        lines.extend(["", "### Inter-se transfers excluded from promoter exits"])
        lines.extend(f"- {_display_event(event)}. The filing describes an inter-se transfer within the promoter group." for event in inter_se)

    count = len(dated_completed)
    quantity_count = sum(1 for event in dated_completed if event.get("shares") != "Not stated in extracted transaction passage")
    percentage_count = sum(1 for event in dated_completed if event.get("percentage") != "Not stated in extracted transaction passage")
    reason_count = sum(1 for event in dated_completed if not str(event.get("reason") or "").casefold().startswith("the fetched transaction passage does not state"))
    lines.extend([
        "",
        "## What the retrieved data can support",
        f"The fetched source set verifies {count} completed promoter sale transaction record(s) with an exact event date inside the requested window. This is not a count of distinct promoters, a market-wide total, or an exhaustive denominator.",
        f"Among those {count} dated records, {quantity_count} report a share count and {percentage_count} state a stake percentage in the extracted transaction passage. These ratios describe disclosure detail in the retrieved set only.",
        f"A filing-stated reason was isolated for {reason_count} of the dated completed records. Where no reason is stated, the report does not infer motive from market behavior or company context.",
        f"A full promoter exit is established for {sum(1 for event in dated_completed if event.get('exit_size') == 'Full exit stated in the filing')} dated record(s) only where the direct filing explicitly states full disposal. The current retrieved pages do not support a reliable national proportion of promoters who sell, or a complete count of all exits.",
    ])
    if method_refs:
        lines.extend(["", f"Exchange pages in the retrieved sample: NSE { _refs([exchange_ids.get('nse')]) }, BSE { _refs([exchange_ids.get('bse')]) }."])

    meta = {
        "applicable": True,
        "quarterCount": len(quarters),
        "datedCompletedCount": count,
        "completedDateUnknownCount": len(completed_date_unknown),
        "statusUnclearCount": len(status_unclear),
        "proposedCount": len(proposed),
        "agreementOnlyCount": len(agreement_only),
        "interSeCount": len(inter_se),
        "countsByQuarter": counts,
        "exchangeMethodSources": dict(exchange_ids),
        "retrievedSourceIds": sorted(source_by_id),
    }
    return "\n".join(lines).strip() + "\n", meta


def render_promoter_extraction_failure_report(
    question: str,
    publisher_sources: list[dict],
    *,
    as_of_date: date | None = None,
) -> tuple[str, dict]:
    """Fail closed with a quarter grid if safe event extraction raises."""
    quarters = requested_indian_fy_quarters(question, as_of_date)
    if not quarters:
        return "", {"applicable": False, "extractionFailed": True}

    method_ids: dict[str, str] = {}
    quarter_ids: dict[str, list[str]] = {label: [] for label, _start, _end in quarters}
    for row in publisher_sources or []:
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("id") or "").strip().upper()
        url = row.get("pageFetchFinalUrl") or row.get("pageFetchUrl") or row.get("url") or ""
        body = _direct_page_text(row)
        if not re.fullmatch(r"S\d+", source_id) or not body or not _is_official_exchange_url(url):
            continue
        host = (urlparse(str(url)).hostname or "").casefold().removeprefix("www.")
        exchange = "nse" if host == "nseindia.com" or host.endswith(".nseindia.com") else "bse"
        method_ids.setdefault(exchange, source_id)
        filing_day = _filing_date(row, body)
        quarter = next((label for label, start, end in quarters if filing_day and start <= filing_day <= end), None)
        if quarter:
            quarter_ids[quarter].append(source_id)

    def refs(values) -> str:
        return " ".join(f"[{value}]" for value in dict.fromkeys(
            str(value).strip().upper() for value in values
            if re.fullmatch(r"S\d+", str(value).strip().upper())
        ))

    start_date, end_date = quarters[0][1], quarters[-1][2]
    labels = [label for label, _start, _end in quarters]
    lines = [
        "## Scope and status",
        f"The requested window covers {len(quarters)} completed Indian fiscal quarters, {labels[0]} through {labels[-1]} ({start_date:%d %b %Y} to {end_date:%d %b %Y}).",
        "The transaction-extraction step did not produce a reliable ledger. No sale count, issuer, seller, share quantity, stated reason, or full-exit classification is asserted.",
        "The retrieved exchange pages are a sample and do not establish a complete issuer-by-issuer registry. Quarter activity remains unknown, not zero.",
        "",
        "## Quarter-by-quarter coverage",
        "| Fiscal quarter | Extracted completed sale records | Coverage result | Direct pages dated in quarter |",
        "| --- | --- | --- | --- |",
    ]
    for label, _start, _end in quarters:
        lines.append(
            f"| {label} | No safe transaction record extracted. | Activity is unknown, not zero. | {refs(quarter_ids[label]) or 'No quarter-dated direct page available'} |"
        )
    if method_ids:
        lines.extend(["", f"Direct exchange pages in the retrieved sample: NSE {refs([method_ids.get('nse')])}; BSE {refs([method_ids.get('bse')])}."])
    lines.extend([
        "",
        "## Data required to complete the requested analysis",
        "The report cannot state a proportion of promoters who sell or enumerate all exits without a reliably extracted and complete exchange disclosure universe. This draft does not treat missing records as zero activity.",
    ])
    return "\n".join(lines).strip() + "\n", {
        "applicable": True,
        "extractionFailed": True,
        "quarterCount": len(quarters),
        "exchangeMethodSources": method_ids,
        "quarterSourceIds": {label: sorted(set(values)) for label, values in quarter_ids.items()},
    }
