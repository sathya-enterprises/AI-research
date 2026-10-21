"""Shared publisher-page extraction for report research and source evidence.

This utility is deliberately conservative: it extracts text from the actual
HTTP response (HTML or text-based PDF), removes common page furniture, keeps
table rows as individual evidence lines, and returns empty text when a page is
blocked or cannot be parsed. It never generates or repairs source content.
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,text/plain;q=0.8,*/*;q=0.4",
    "Accept-Language": "en-IN,en;q=0.9",
}


def _normalize_lines(text: str, minimum: int = 18) -> str:
    lines: list[str] = []
    seen: set[str] = set()
    boilerplate = re.compile(
        r"^(accept all cookies|cookie policy|privacy policy|terms of use|subscribe now|"
        r"sign up for our newsletter|advertisement|related articles|read more|share this article|"
        r"skip to content|all rights reserved)$",
        re.I,
    )
    for raw_line in str(text or "").replace("\r", "\n").split("\n"):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) < minimum or boilerplate.match(line):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines).strip()


_PDF_FIN_RE = re.compile(
    r"net profit|profit after tax|\bpat\b|gross npa|gross non[- ]performing|net npa|total assets|total deposits|"
    r"\badvances\b|return on equity|\broe\b|net interest margin|\bcasa\b|capital adequacy|crore|lakh crore|"
    r"financial highlights|key financial|balance sheet",
    re.I,
)


def _extract_pdf(payload: bytes, max_chars: int) -> str:
    """Extract selectable text from a PDF; scanned image-only PDFs remain empty."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(payload), strict=False)
        output: list[str] = []
        length = 0
        # Do not process a huge PDF indefinitely. Short documents keep their early
        # pages. For long filings (annual reports, investor decks, RBI tables) the
        # first pages are cover/letters, so score up to 90 pages for financial
        # figures and keep the densest ones (in document order) within max_chars.
        page_count = len(reader.pages)
        if page_count <= 12:
            for page in reader.pages[:page_count]:
                text = page.extract_text() or ""
                text = _normalize_lines(text, minimum=5)
                if text:
                    output.append(text)
                    length += len(text)
                if length >= max_chars:
                    break
            return "\n\n".join(output)[:max_chars].strip()
        scored: list[tuple[float, int, str]] = []
        for idx, page in enumerate(reader.pages[:min(page_count, 90)]):
            text = _normalize_lines(page.extract_text() or "", minimum=5)
            if not text:
                continue
            hits = len(_PDF_FIN_RE.findall(text))
            digits = sum(ch.isdigit() for ch in text) / max(1, len(text))
            score = hits * 2.0 + digits * 40.0 + (3.0 if idx < 2 else 0.0)
            scored.append((score, idx, text))
        scored.sort(key=lambda row: -row[0])
        chosen: list[tuple[int, str]] = []
        for _score, idx, text in scored:
            if length >= max_chars:
                break
            chosen.append((idx, text))
            length += len(text)
        chosen.sort(key=lambda row: row[0])
        output = [f"[PDF page {idx + 1}]\n{text}" for idx, text in chosen]
        return "\n\n".join(output)[:max_chars].strip()
    except Exception:
        return ""


def _extract_html(raw_html: str, max_chars: int, include_script_data: bool) -> str:
    """Extract publisher text while preserving chartable tables and series data.

    Numeric tables and embedded chart-data snippets are kept in explicit blocks
    ahead of narrative text so a final character budget is less likely to clip
    the observations at the end of a publisher page.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw_html, "html.parser")
        page_title = soup.title.get_text(" ", strip=True) if soup.title else ""

        # Capture tabular observations from the complete document, not just the
        # main article container. Financial/market tables are often rendered in
        # sibling containers outside <article> or [role=main].
        table_blocks: list[str] = []
        total_table_rows = 0
        for table_index, table in enumerate(soup.find_all("table"), 1):
            rows: list[list[str]] = []
            for tr in table.find_all("tr"):
                cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                         for c in tr.find_all(["th", "td"], recursive=False)]
                cells = [cell for cell in cells if cell]
                if len(cells) >= 2:
                    rows.append(cells)
            if len(rows) < 2:
                continue
            data_rows = rows[1:]
            numeric_rows = sum(
                1 for row in data_rows
                if any(re.search(r"(?:\d[\d,.]*\s*%?|₹|\$|€|£|\b(?:GW|MW|TWh|GWh|million|billion|crore|lakh)\b)", cell, re.I)
                       for cell in row)
            )
            # Ignore tiny layout tables unless they contain at least some
            # numeric records. Keep all substantive, data-bearing tables.
            if len(rows) < 3 and numeric_rows == 0:
                continue
            if numeric_rows == 0 and len(rows) < 4:
                continue
            preceding_heading = table.find_previous(re.compile(r"^h[1-6]$"))
            heading = re.sub(r"\s+", " ", preceding_heading.get_text(" ", strip=True)).strip() if preceding_heading else ""
            table_blocks.append(f"TABLE {len(table_blocks) + 1}" + (f" — {heading[:180]}" if heading else ""))
            # Header and every row are preserved. The overall output budget, not
            # a fixed row-count ceiling, determines how many rows can be passed.
            for row in rows:
                table_blocks.append(" | ".join(row))
                total_table_rows += 1
                if total_table_rows >= 800:
                    break
            if total_table_rows >= 800:
                break

        # Find embedded chart-data arrays even when a script is larger than the
        # old 12 KB gate. Keep multiple windows around labels/categories/data so
        # the series values that follow the labels are not lost to a prefix cut.
        script_blocks: list[str] = []
        if include_script_data:
            signature = re.compile(r"\b(labels|categories|xAxis|datasets|series|values)\b\s*[\"']?\s*[:=]", re.I)
            seen_windows: set[str] = set()
            for script in soup.find_all("script"):
                value = script.string or script.get_text(" ", strip=True) or ""
                if not value or len(value) > 250_000 or not re.search(r"\d", value):
                    continue
                matches = list(signature.finditer(value))
                if not matches:
                    continue
                for match in matches:
                    left = max(0, match.start() - 350)
                    right = min(len(value), match.end() + 2200)
                    compact = re.sub(r"\s+", " ", value[left:right]).strip()
                    if len(compact) < 80 or not re.search(r"\d", compact):
                        continue
                    key = re.sub(r"\W+", " ", compact.casefold())[:500]
                    if key in seen_windows:
                        continue
                    seen_windows.add(key)
                    script_blocks.append(compact[:2400])
                    if len(script_blocks) >= 10:
                        break
                if len(script_blocks) >= 10:
                    break

        # Build narrative text separately so the structured values can be given
        # first claim on the final page-extraction budget.
        for node in soup.find_all(["script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button", "iframe", "canvas"]):
            node.decompose()
        for node in soup.find_all(True):
            marker = " ".join((
                str(node.get("id") or ""),
                " ".join(str(item) for item in (node.get("class") or [])),
                str(node.get("aria-label") or ""),
            )).casefold()
            if any(token in marker for token in (
                "cookie-banner", "cookie-consent", "breadcrumb", "navigation", "share-tools",
                "social-links", "newsletter-signup", "paywall-modal", "advertisement", "ad-container",
                "related-content", "subscribe-modal", "modal-backdrop",
            )):
                node.decompose()

        def container_blocks(node: Any) -> list[str]:
            blocks: list[str] = []
            for child in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "caption"]):
                block = child.get_text(" ", strip=True)
                block = re.sub(r"\s+", " ", block).strip()
                if len(block) >= 18:
                    blocks.append(block)
            if not blocks:
                raw = node.get_text(" ", strip=True)
                if len(raw) >= 250:
                    blocks.append(re.sub(r"\s+", " ", raw).strip())
            return list(dict.fromkeys(blocks))

        candidates: list[tuple[int, list[str]]] = []
        selectors = (
            "article", "main", '[role="main"]', '[itemprop="articleBody"]',
            ".article-body", ".article-content", ".post-content", ".entry-content",
            ".story-body", ".article__body", ".content-body",
        )
        for selector in selectors:
            for node in soup.select(selector):
                blocks = container_blocks(node)
                text_length = sum(len(x) for x in blocks)
                if text_length >= 250:
                    score = text_length + min(len(blocks), 40) * 55
                    candidates.append((score, blocks))
        if candidates:
            blocks = max(candidates, key=lambda item: item[0])[1]
        else:
            body = soup.body or soup
            blocks = container_blocks(body)
            if not blocks:
                raw = body.get_text(" ", strip=True)
                if len(raw) >= 250:
                    blocks = [re.sub(r"\s+", " ", raw).strip()]

        if page_title and blocks and page_title.casefold() not in blocks[0].casefold():
            blocks.insert(0, page_title)
        narrative_text = _normalize_lines("\n".join(blocks), minimum=10)
        table_text = "\n".join(table_blocks).strip()
        script_text = "\n".join(script_blocks).strip()

        # Allocate a dynamic budget: data-rich pages keep every possible row;
        # pages without tables/scripts automatically return more narrative text.
        table_budget = min(len(table_text), int(max_chars * 0.62))
        script_budget = min(len(script_text), int(max_chars * 0.20))
        narrative_budget = max(0, max_chars - table_budget - script_budget)

        def clip_at_line(text: str, budget: int) -> str:
            if budget <= 0 or not text:
                return ""
            if len(text) <= budget:
                return text
            cut = text[:budget]
            if "\n" in cut:
                cut = cut.rsplit("\n", 1)[0]
            return cut.rstrip()

        chunks: list[str] = []
        if table_text:
            table_chunk = clip_at_line(table_text, table_budget)
            if table_chunk:
                chunks.append("[STRUCTURED PUBLISHER TABLE DATA — original rows preserved]\n" + table_chunk)
        if script_text:
            script_chunk = clip_at_line(script_text, script_budget)
            if script_chunk:
                chunks.append("[PUBLISHER CHART / TABLE SERIES DATA — embedded script excerpts; verify labels and units]\n" + script_chunk)
        narrative_chunk = clip_at_line(narrative_text, narrative_budget)
        if narrative_chunk:
            chunks.append("[PUBLISHER PAGE TEXT]\n" + narrative_chunk)

        # The block labels consume a small amount of the source budget. Enforce
        # the limit last to keep this function's contract simple for callers.
        result = "\n\n".join(chunks).strip()
        if len(result) > max_chars:
            result = result[:max_chars].rsplit("\n", 1)[0].strip()
        return result
    except Exception as exc:
        logging.getLogger("source_extractor").debug("HTML extraction failed: %s", type(exc).__name__)
        return ""

def _extract_publisher_metadata(raw_html: str, fallback_url: str = "") -> dict[str, str]:
    """Extract dated publisher metadata from the fetched page, without inference.

    Dates are accepted only from HTML metadata/schema fields or an explicit
    ``time[datetime]`` element. The URL or a search-result timestamp is never
    converted into a publication date.
    """
    result = {
        "page_title": "", "publisher": "", "published_at": "",
        "modified_at": "", "canonical_url": "", "publication_date_source": "",
    }
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(str(raw_html or ""), "html.parser")
        if soup.title:
            result["page_title"] = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True)).strip()[:300]

        def meta_value(*names: str) -> tuple[str, str]:
            for name in names:
                node = (
                    soup.find("meta", attrs={"property": name})
                    or soup.find("meta", attrs={"name": name})
                    or soup.find("meta", attrs={"itemprop": name})
                )
                if node and str(node.get("content") or "").strip():
                    return re.sub(r"\s+", " ", str(node.get("content")).strip())[:120], name
            return "", ""

        site_name, _ = meta_value("og:site_name", "application-name", "publisher")
        published_at, published_source = meta_value(
            "article:published_time", "datePublished", "date.published", "dc.date.issued",
            "DCTERMS.issued", "pubdate", "publishdate", "date",
        )
        modified_at, _ = meta_value("article:modified_time", "dateModified", "last-modified", "dcterms.modified")
        publisher = site_name
        canonical = soup.find("link", rel=lambda value: value and "canonical" in ([x.casefold() for x in value] if isinstance(value, list) else str(value).casefold().split()))
        canonical_url = str(canonical.get("href") or "").strip() if canonical else ""

        # Prefer typed JSON-LD fields over generic meta tags when present.
        jsonld_items: list[dict] = []
        for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw = script.string or script.get_text(" ", strip=True) or ""
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            nodes = parsed if isinstance(parsed, list) else [parsed]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                graph = node.get("@graph")
                jsonld_items.extend(item for item in graph if isinstance(item, dict)) if isinstance(graph, list) else None
                jsonld_items.append(node)

        for node in jsonld_items:
            typ = node.get("@type") or []
            typ = typ if isinstance(typ, list) else [typ]
            typed_article = any(str(value).casefold() in {"article", "newsarticle", "report", "analysisnewsarticle", "blogposting"} for value in typ)
            if not result["page_title"] and typed_article:
                result["page_title"] = re.sub(r"\s+", " ", str(node.get("headline") or node.get("name") or "")).strip()[:300]
            if not result["published_at"] and str(node.get("datePublished") or "").strip():
                result["published_at"] = str(node.get("datePublished")).strip()[:120]
                result["publication_date_source"] = "jsonld:datePublished"
            if not result["modified_at"] and str(node.get("dateModified") or "").strip():
                result["modified_at"] = str(node.get("dateModified")).strip()[:120]
            pub = node.get("publisher")
            if not publisher and isinstance(pub, dict):
                publisher = str(pub.get("name") or "").strip()[:180]
            if not canonical_url and str(node.get("url") or "").startswith(("http://", "https://")):
                canonical_url = str(node.get("url")).strip()[:2048]

        if not result["published_at"]:
            time_node = soup.find("time", attrs={"datetime": True})
            if time_node and str(time_node.get("datetime") or "").strip():
                result["published_at"] = str(time_node.get("datetime")).strip()[:120]
                result["publication_date_source"] = "time:datetime"
            elif published_at:
                result["published_at"] = published_at
                result["publication_date_source"] = f"meta:{published_source}"
        elif not result["publication_date_source"]:
            result["publication_date_source"] = f"meta:{published_source}" if published_source else "publisher_html_metadata"

        result["publisher"] = publisher[:180]
        if canonical_url:
            result["canonical_url"] = canonical_url
        elif fallback_url:
            result["canonical_url"] = fallback_url[:2048]
        return result
    except Exception:
        return result


async def fetch_source_document(
    url: str,
    max_chars: int = 8000,
    *,
    include_script_data: bool = False,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Fetch and extract a source with explicit diagnostics for provenance logs.

    `status` distinguishes an HTTP denial, a network failure, an unsupported/empty
    extraction, and a successful publisher-page extraction. The caller can still
    use the provider search extract when a direct fetch is blocked, but must not
    mislabel that fallback as direct publisher evidence.
    """
    log = logger or logging.getLogger("source_extractor")
    raw_url = str(url or "").strip()
    parsed = urlparse(raw_url)
    base: dict[str, Any] = {
        "text": "", "status": "invalid_url", "http_status": None,
        "requested_url": raw_url, "final_url": raw_url, "content_type": "",
        "page_title": "", "publisher": "", "published_at": "",
        "modified_at": "", "canonical_url": "", "publication_date_source": "",
    }
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return base
    try:
        timeout = httpx.Timeout(connect=5, read=12, write=8, pool=5)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(raw_url, headers=_DEFAULT_HEADERS)
        base.update({
            "http_status": int(response.status_code),
            "final_url": str(response.url),
            "content_type": str(response.headers.get("content-type") or "").casefold(),
        })
        if not response.is_success:
            base["status"] = f"http_{response.status_code}"
            log.debug("Source fetch returned HTTP %d for %s", response.status_code, parsed.hostname)
            return base
        body = response.content
        if not body:
            base["status"] = "empty_response"
            return base
        content_type = base["content_type"]
        final_path = urlparse(str(response.url)).path.casefold()
        is_pdf = "application/pdf" in content_type or final_path.endswith(".pdf") or body[:5] == b"%PDF-"
        if is_pdf:
            extracted = _extract_pdf(body, max_chars)
            base["status"] = "ok" if extracted.strip() else "empty_pdf_text"
            base["text"] = extracted
            log.debug("Source PDF extracted chars=%d host=%s", len(extracted), parsed.hostname)
            return base
        encoding = response.encoding or "utf-8"
        try:
            raw_text = body.decode(encoding, errors="replace")
        except LookupError:
            raw_text = body.decode("utf-8", errors="replace")
        if "json" in content_type or raw_text.lstrip().startswith(("{", "[")):
            # Keep APIs/JSON data sourceable without pretending they're articles.
            extracted = re.sub(r"\s+", " ", raw_text).strip()[:max_chars]
            base["status"] = "ok" if extracted else "empty_extraction"
            base["text"] = extracted
            return base
        if "html" not in content_type and "xhtml" not in content_type and not re.search(r"<\s*(html|article|main|body)\b", raw_text[:1000], re.I):
            plain = _normalize_lines(raw_text, minimum=18)
            base["status"] = "ok" if plain else "empty_extraction"
            base["text"] = plain[:max_chars]
            return base
        base.update(_extract_publisher_metadata(raw_text, str(response.url)))
        extracted = _extract_html(raw_text, max_chars, include_script_data)
        base["status"] = "ok" if extracted else "empty_extraction"
        base["text"] = extracted
        return base
    except httpx.TimeoutException:
        base["status"] = "timeout"
        return base
    except httpx.RequestError as exc:
        base["status"] = "network_error"
        log.debug("Source fetch failed for %s: %s", parsed.hostname or "unknown", type(exc).__name__)
        return base
    except Exception as exc:
        base["status"] = "extract_error"
        log.debug("Source extraction failed for %s: %s", parsed.hostname or "unknown", type(exc).__name__)
        return base


async def fetch_source_content(
    url: str,
    max_chars: int = 8000,
    *,
    include_script_data: bool = False,
    logger: logging.Logger | None = None,
) -> str:
    """Compatibility wrapper that returns only extracted page text."""
    result = await fetch_source_document(
        url, max_chars=max_chars, include_script_data=include_script_data, logger=logger,
    )
    return str(result.get("text") or "")
