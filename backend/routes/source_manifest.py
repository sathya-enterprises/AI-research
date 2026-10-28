"""Canonical report-source registry helpers.

A registry's S# IDs are immutable once assigned. The report writer, audit,
HTML preview, PDF export, and appendix must all resolve the same IDs; renderers
must never re-number an already canonical registry after citations exist.
"""
from __future__ import annotations

import re
import logging
from urllib.parse import urlparse, urlunparse

from utils.source_identity import source_identity_key

log = logging.getLogger("source_manifest")

_REGISTRY_MARKER = "_ggSourceRegistryVersion"
_REGISTRY_VERSION = 1
_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_ID_RE = re.compile(r"^S\d+$", re.IGNORECASE)


def _clean_text(value: object, limit: int = 300) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _safe_http_url(value: object) -> str:
    """Keep an absolute clickable HTTP(S) URL and remove its fragment only."""
    raw = _clean_text(value, 2_000)
    if not _HTTP_URL_RE.match(raw):
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.netloc:
            return ""
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.params, parsed.query, ""))
    except Exception:
        return ""


def _publisher_for(url: str, fallback: str) -> str:
    if url:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    return _clean_text(fallback, 80)


def _domain_for(url: str, fallback: str = "") -> str:
    if url:
        host = urlparse(url).hostname or ""
        return host[4:] if host.lower().startswith("www.") else host.lower()
    return _clean_text(fallback, 160).lower()


def _is_canonical_registry(sources: object) -> bool:
    if not isinstance(sources, list) or not sources:
        return False
    ids = []
    for source in sources:
        if not isinstance(source, dict) or source.get(_REGISTRY_MARKER) != _REGISTRY_VERSION:
            return False
        source_id = str(source.get("id") or "").strip().upper()
        if not _ID_RE.fullmatch(source_id):
            return False
        ids.append(source_id)
    return len(ids) == len(set(ids))


def _display_entry(entry: dict, source_id: str) -> dict:
    title = _clean_text(entry.get("title") or entry.get("name"), 300)
    url = _safe_http_url(entry.get("url") or entry.get("sourceUrl") or entry.get("source_url"))
    kind = _clean_text(entry.get("kind") or entry.get("sourceType") or "Web source", 80)
    publisher = _publisher_for(url, entry.get("publisher") or kind)
    domain = _domain_for(url, entry.get("domain") or entry.get("host") or "")
    publication_date = _clean_text(
        entry.get("publication_date") or entry.get("publicationDate") or
        entry.get("published_at") or entry.get("publishedAt") or entry.get("date"), 80,
    )
    publisher_page_title = _clean_text(entry.get("publisher_page_title") or entry.get("publisherPageTitle") or "", 300)
    publisher_page_url = _safe_http_url(entry.get("publisher_page_url") or entry.get("publisherPageUrl") or "")
    publication_date_source = _clean_text(entry.get("publication_date_source") or entry.get("publicationDateSource") or "", 120)
    publisher_page_modified_at = _clean_text(entry.get("publisher_page_modified_at") or entry.get("publisherPageModifiedAt") or "", 120)
    source_type = _clean_text(entry.get("source_type") or entry.get("sourceType") or kind, 80)
    if not title:
        title = publisher or "Untitled source"
    result = {
        "id": source_id,
        "title": title,
        "url": url,
        "publisher": publisher,
        "domain": domain,
        "publication_date": publication_date,
        "publisher_page_title": publisher_page_title,
        "publisher_page_url": publisher_page_url,
        "publication_date_source": publication_date_source,
        "publisher_page_modified_at": publisher_page_modified_at,
        "source_type": source_type,
        "kind": kind,
        "source_status": _clean_text(entry.get("source_status") or entry.get("sourceStatus") or "resolved", 40).casefold(),
        "status_reason": _clean_text(entry.get("status_reason") or entry.get("statusReason") or "", 500),
        # Keep retrieval/evidence diagnostics with the same canonical source ID
        # all the way into Show Report and PDF. Without these fields a source that
        # returned HTTP 403 looked identical to a fully fetched publisher page.
        "evidence_origin": _clean_text(entry.get("evidence_origin") or entry.get("evidenceExcerptOrigin") or entry.get("evidenceOrigin") or "", 80).casefold(),
        "page_fetch_status": _clean_text(entry.get("page_fetch_status") or entry.get("pageFetchStatus") or "unknown", 80).casefold(),
        "page_fetch_http_status": entry.get("page_fetch_http_status", entry.get("pageFetchHttpStatus")),
        "evidence_available": bool(entry.get("evidence_available", entry.get("evidenceAvailable", False))),
        "claim_evidence_matched": bool(entry.get("claim_evidence_matched", entry.get("claimEvidenceMatched", False))),
        "unmatched_numeric_claims_count": int(entry.get("unmatched_numeric_claims_count", len(entry.get("unmatchedNumericClaims") or [])) or 0),
        _REGISTRY_MARKER: _REGISTRY_VERSION,
    }
    # Exact quoted source lines behind evidence-gallery chart points; kept so
    # export-time chart audits can re-verify the same lines.
    chart_lines = str(entry.get("chartEvidenceExcerpt") or "").strip()
    if chart_lines:
        result["chartEvidenceExcerpt"] = chart_lines[:16000]
    # Keep the chart-verification evidence the report's final gate used. The
    # PDF/HTML export re-runs the same gate on this manifest; dropping these
    # fields made every chart that passed at report time fail at download
    # (preview showed 3 charts, the PDF 0).
    for key, limit in (("publisherChartEvidenceExcerpt", 16000), ("searchChartEvidenceExcerpt", 16000),
                       ("publisherEvidenceExcerpt", 5200), ("evidenceExcerpt", 2400)):
        value = str(entry.get(key) or "").strip()
        if value:
            result[key] = value[:limit]
    for key in ("publisher_gallery_chart_evidence_verified", "search_extract_chart_evidence_verified",
                "publisher_table_chart_evidence_verified"):
        if entry.get(key) is True or str(entry.get(key)).strip().casefold() == "true":
            result[key] = True
    origin = _clean_text(entry.get("evidence_excerpt_origin") or entry.get("evidenceExcerptOrigin") or "", 80).casefold()
    if origin:
        result["evidence_excerpt_origin"] = origin
    return result


def _next_id(result: list[dict]) -> int:
    maximum = 0
    for item in result:
        match = re.fullmatch(r"S(\d+)", str(item.get("id") or ""), re.IGNORECASE)
        if match:
            maximum = max(maximum, int(match.group(1)))
    return maximum + 1


_CITATION_GROUP_RE = re.compile(r"(?<![A-Za-z0-9])[\[(]\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*[\])]", re.IGNORECASE)


def extract_source_citation_ids(text: object) -> list[str]:
    """Extract only properly bracketed source citations, including grouped IDs.

    Searching for bare ``S1`` tokens throughout prose can confuse a source ID
    with unrelated labels such as a sensor/model name. Citation groups like
    ``[S1, S7]`` are kept together semantically while returning stable IDs.
    """
    found: list[str] = []
    for match in _CITATION_GROUP_RE.finditer(str(text or "")):
        group = match.group(1) or ""
        found.extend(re.findall(r"\bS\d+\b", group, flags=re.IGNORECASE))
    result: list[str] = []
    seen: set[str] = set()
    for source_id in found:
        source_id = source_id.upper()
        if source_id not in seen:
            seen.add(source_id)
            result.append(source_id)
    return result


def ensure_cited_source_records(report_text: object, sources: object) -> list[dict]:
    """Keep citation IDs visible and auditable even when their source record is lost.

    Missing sources are NOT fabricated. A placeholder with ``source_status='unresolved'``
    is inserted at the original S# so browser/PDF appendices can explain exactly why
    a citation cannot be opened. Publication QA must still treat that placeholder as
    a blocking provenance defect; this helper prevents a silent broken reference.
    """
    manifest = normalise_source_manifest(sources)
    cited_ids = set(extract_source_citation_ids(report_text))
    # A prior audit pass may have inserted unresolved placeholders. If a bounded
    # repair removes the corresponding citation, remove its now-unused placeholder
    # as well; otherwise one repaired claim would leave a permanent blocker in the
    # next audit even though the report no longer cites that source ID.
    manifest = [
        item for item in manifest
        if not (
            isinstance(item, dict)
            and str(item.get("source_status") or item.get("sourceStatus") or "").casefold() == "unresolved"
            and str(item.get("id") or "").strip().upper() not in cited_ids
        )
    ]
    existing = {str(item.get("id") or "").upper() for item in manifest if isinstance(item, dict)}
    missing = [sid for sid in extract_source_citation_ids(report_text) if sid not in existing]
    for source_id in missing:
        manifest.append({
            "id": source_id,
            "title": f"Source record missing for [{source_id}]",
            "url": "",
            "publisher": "",
            "domain": "",
            "publication_date": "",
            "source_type": "Unresolved citation",
            "kind": "Missing source record",
            "source_status": "unresolved",
            "status_reason": "The report cites this ID, but the corresponding retrieved source record was unavailable at publication time. The claim is not verified.",
            _REGISTRY_MARKER: _REGISTRY_VERSION,
        })
    if missing:
        # Preserve the original S# values but keep the appendix in a predictable
        # order so readers can find the gap adjacent to its neighboring IDs.
        manifest.sort(key=lambda item: (
            int(re.fullmatch(r"S(\d+)", str(item.get("id") or "S0"), re.I).group(1))
            if re.fullmatch(r"S(\d+)", str(item.get("id") or ""), re.I) else 10**9,
            str(item.get("title") or "").casefold(),
        ))
        log.info("Source registry: preserved %d unresolved citation placeholder(s): %s", len(missing), missing)
    return manifest



def source_provenance_note(source: object) -> str:
    """Explain what retrieval actually established for a displayed citation.

    A link's presence is not proof that the page was fetched or that it supports
    a numeric claim. The note is intentionally conservative and never says a
    claim is factually verified merely because an excerpt exists.
    """
    if not isinstance(source, dict):
        return "Source evidence status unavailable; verify the claim against the publisher."
    status = str(source.get("page_fetch_status") or source.get("pageFetchStatus") or "unknown").casefold()
    origin = str(source.get("evidence_origin") or source.get("evidenceOrigin") or source.get("evidenceExcerptOrigin") or "").casefold()
    available = bool(source.get("evidence_available", source.get("evidenceAvailable", False)))
    matched = source.get("claim_evidence_matched", source.get("claimEvidenceMatched"))
    missing_count = source.get("unmatched_numeric_claims_count")
    if missing_count is None:
        missing_count = len(source.get("unmatchedNumericClaims") or [])
    try:
        missing_count = int(missing_count or 0)
    except (TypeError, ValueError):
        missing_count = 0

    if str(source.get("source_status") or source.get("sourceStatus") or "").casefold() == "unresolved":
        return "Source record missing; this citation is not verified."
    if origin == "uploaded_workbook" and available and str(source.get("chartEvidenceExcerpt") or "").strip():
        return "Evidence is derived from the user-uploaded workbook and is traceable to worksheet rows; it is not independently publisher-verified."
    if origin == "okf_retrieval" and available:
        if matched is True and missing_count == 0:
            return "Retrieved from the saved OKF document; the cited claim aligns with the available excerpt."
        return "Retrieved from the saved OKF document; review the cited claim against this excerpt and its document provenance."
    group_coverage = source.get("citation_group_coverage")
    if status == "ok" and available:
        if matched is True and missing_count == 0:
            return "Publisher page text retrieved; cited claim aligns with the available excerpt (not an independent guarantee of factual accuracy)."
        if group_coverage is True:
            return "Publisher page text retrieved; this source did not independently match every number, but at least one source cited with the same claim did. Review this source's own excerpt separately."
        return "Publisher page text retrieved, but the cited numeric claim was not fully matched to the available excerpt. Review before relying on it."
    if status.startswith("http_"):
        code = status.split("_", 1)[1]
        basis = "search-index excerpt only" if "search_extract" in origin else "no usable page text"
        group_note = " Another source cited with the same claim matched the number, but this URL itself remains unverified." if group_coverage is True else ""
        return f"Direct publisher fetch returned HTTP {code}; evidence basis: {basis}. Confirm cited figures at the source.{group_note}"
    if status == "rejected_by_relevance_gate":
        chars = source.get("page_fetch_candidate_chars") or source.get("pageFetchCandidateChars") or "an unknown amount of"
        return f"Publisher returned page text ({chars} characters), but the relevance check rejected it; only the search-index excerpt was used. Inspect the extracted page text and relevance threshold before relying on this citation."
    if status in {"timeout", "network_error", "extract_error", "empty_extraction", "empty_pdf_text", "empty_response", "unavailable_or_not_relevant", "skipped_by_policy"}:
        basis = "search-index excerpt only" if "search_extract" in origin else ("some source text is available" if available else "no usable source text retrieved")
        return f"Publisher page could not be fully extracted ({status.replace('_', ' ')}); evidence basis: {basis}. Confirm cited figures at the source."
    if "search_extract_only" in origin or origin == "stored_search_extract":
        return "Search-index excerpt only; direct publisher-page text was not verified. Confirm cited figures at the source."
    if not available:
        return "No usable source text was retrieved for claim checking; the URL/title alone does not verify the claim."
    return "Source is listed, but claim-specific evidence alignment was not recorded. Review before relying on precise figures."

def normalise_source_manifest(sources: object, source_documents: object = None) -> list[dict]:
    """Build a manifest, preserving IDs if input is already canonical.

    Canonical entries are copied with their exact IDs, even when IDs are not
    contiguous (for example a Brief appendix displays only cited sources).
    Fresh retrieval inputs without the registry marker are deduplicated first,
    then numbered from S1. This prevents source IDs from shifting between writer
    prompts, audit payloads, HTML, PDF, and a later export.
    """
    raw_sources = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    result: list[dict] = []
    seen: set[str] = set()
    canonical_input = _is_canonical_registry(raw_sources)

    if canonical_input:
        # Canonical IDs are references used by already-written prose. Preserve
        # the entries verbatim by ID (including gaps) even if the visible list
        # is a cited-only appendix subset.
        for entry in raw_sources:
            result.append(_display_entry(entry, str(entry["id"]).strip().upper()))
            identity = source_identity_key(entry)
            if identity:
                seen.add(identity)
    else:
        def add(entry: object) -> None:
            if not isinstance(entry, dict):
                return
            current = dict(entry)
            if str(current.get("url") or "").lower().startswith("internal://"):
                current["url"] = ""
                current["kind"] = current.get("kind") or "Verified data"
            identity = source_identity_key(current)
            if identity and identity in seen:
                return
            if identity:
                seen.add(identity)
            result.append(_display_entry(current, f"S{len(result) + 1}"))

        for entry in raw_sources:
            add(entry)

    # User-provided documents and external additions are appended after web
    # sources. If the primary list was canonical, preserve its IDs and allocate
    # additions strictly after the current maximum; never compact gaps.
    documents = source_documents if isinstance(source_documents, list) else []
    next_num = _next_id(result)
    for document in documents:
        if not isinstance(document, dict):
            continue
        name = _clean_text(document.get("name") or document.get("title"), 300)
        if not name:
            continue
        entry = {
            "title": name, "kind": "Uploaded document", "publisher": "User-provided", "url": "",
        }
        # Retain bounded evidence excerpts for uploaded workbooks. This is a
        # user-supplied evidence source, not a publisher-page verification; the
        # chart audit handles that provenance explicitly. Do not copy extracted
        # file text wholesale into the public source registry.
        for key in (
            "chartEvidenceExcerpt", "evidence_origin", "evidenceOrigin",
            "page_fetch_status", "pageFetchStatus", "evidence_available",
            "evidenceAvailable", "source_type", "sourceType",
        ):
            if document.get(key) is not None:
                entry[key] = document.get(key)
        identity = source_identity_key(entry)
        # A canonical manifest is immutable. Appended records may be deduped
        # against it, but uploaded-workbook evidence metadata still needs to be
        # attached when the same file already entered through RAG retrieval.
        if identity and identity in seen:
            if str(entry.get("evidence_origin") or entry.get("evidenceOrigin") or "").casefold() == "uploaded_workbook":
                evidence_keys = (
                    "chartEvidenceExcerpt", "evidence_origin", "evidenceOrigin",
                    "page_fetch_status", "pageFetchStatus", "evidence_available",
                    "evidenceAvailable", "source_type", "sourceType",
                )
                for current in result:
                    if source_identity_key(current) == identity:
                        for key in evidence_keys:
                            if entry.get(key) is not None:
                                current[key] = entry.get(key)
                        break
            continue
        if identity:
            seen.add(identity)
        result.append(_display_entry(entry, f"S{next_num}"))
        next_num += 1
    return result


def build_source_registry(
    sources: object,
    source_documents: object = None,
    additional_sources: object = None,
) -> list[dict]:
    """Return canonical sources plus optional extra display-attribution records.

    Once sources are canonical, retain every original S# and append genuinely
    new entries after the highest ID. Never pass a canonical manifest through
    a fresh sequential numbering operation.
    """
    raw_sources = sources if isinstance(sources, list) else ([sources] if isinstance(sources, dict) else [])
    if _is_canonical_registry(raw_sources):
        manifest = normalise_source_manifest(raw_sources, source_documents)
        seen = {source_identity_key(item) for item in manifest if source_identity_key(item)}
        additions = additional_sources if isinstance(additional_sources, list) else ([additional_sources] if isinstance(additional_sources, dict) else [])
        next_num = _next_id(manifest)
        # Normalize addition records without treating their temporary S# IDs as
        # authoritative, then append after the primary registry. The existing
        # canonical IDs remain stable even when some are not displayed.
        normalized_additions = normalise_source_manifest(additions)
        for entry in normalized_additions:
            identity = source_identity_key(entry)
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            manifest.append(_display_entry(entry, f"S{next_num}"))
            next_num += 1
        return manifest

    combined: list[object] = list(raw_sources)
    if isinstance(additional_sources, list):
        combined.extend(additional_sources)
    elif isinstance(additional_sources, dict):
        combined.append(additional_sources)
    return normalise_source_manifest(combined, source_documents)
