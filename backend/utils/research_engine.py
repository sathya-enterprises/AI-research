"""Evidence graph + statistical research layer (V19).

Deterministic, source-grounded analysis that sits between retrieval/enrichment and
LLM synthesis. It never invents facts: every graph node/edge and numeric observation
is tied to a canonical source id. Statistical calculations only run when enough
numeric observations can be extracted from the supplied evidence.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

_MONEY_RE = re.compile(r"(?<![\w.])(?:USD|US\$|INR|Rs\.?|₹|\$|€|£)\s*[-+]?\d[\d,]*(?:\.\d+)?\s*(?:bn|billion|mn|million|m|k|crore|cr|lakh|lakhs|%)?", re.I)
_NUM_RE = re.compile(
    r"(?<![\w.])(?:USD|US\$|INR|Rs\.?|₹|\$|€|£)?\s*[-+]?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|bps|basis points?|bn|billion|mn|million|crore|cr|lakh|lakhs|m|k|"
    r"GW|MW|KW|TWh|GWh|MWh|kWh|BUs?|units?|accounts?|customers?|users?|projects?|"
    r"sites?|employees?|points?|months?|quarters?|years?|times|x)?(?![\w.])", re.I
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.'’\-]{1,40}(?:\s+[A-Z][A-Za-z0-9&.'’\-]{1,40}){0,4}\b")
_STOP_ENTITIES = {"The", "This", "That", "These", "Those", "Source", "Table", "Figure", "Note", "However", "According"}


def _clean(v: str) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _parse_num(raw: str) -> float | None:
    s = raw.lower().replace(",", "").strip()
    pct = "%" in s
    mult = 1.0
    if "billion" in s or re.search(r"\bbn\b", s): mult = 1e9
    elif "million" in s or re.search(r"\bmn\b", s): mult = 1e6
    elif "crore" in s or re.search(r"\bcr\b", s): mult = 1e7
    elif "lakh" in s: mult = 1e5
    elif re.search(r"\bm\b", s): mult = 1e6
    elif re.search(r"\bk\b", s): mult = 1e3
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m: return None
    value = float(m.group()) * mult
    return value / 100.0 if pct else value


def _source_text(src: dict[str, Any]) -> str:
    # Direct publisher-page extraction should outrank provider search snippets:
    # it commonly contains full tables and chart series that the snippet omits.
    value = str(
        src.get("pageContent") or src.get("evidenceContent") or
        src.get("fullContent") or src.get("snippet") or src.get("evidence") or ""
    )
    # Normalize spacing without flattening table rows; row boundaries improve
    # context labels and prevent adjacent table cells from becoming one metric.
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()).strip()


def build_evidence_graph(sources: list[dict[str, Any]], question: str = "") -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    source_nodes = []
    for i, src in enumerate(sources):
        sid = str(src.get("id") or src.get("source_id") or f"S{i+1}")
        if not sid.startswith("S") and src.get("canonical_id"):
            sid = str(src["canonical_id"])
        source_nodes.append(sid)
        nodes[sid] = {"id": sid, "type": "source", "label": _clean(src.get("title") or sid), "url": src.get("url"), "provenance": sid}
        text = _source_text(src)
        ents = []
        for m in _ENTITY_RE.finditer(text[:25000]):
            e = _clean(m.group())
            if e in _STOP_ENTITIES or len(e) < 3 or len(e) > 90: continue
            if e.lower() in {sid.lower(), "growth gradual"}: continue
            ents.append(e)
        # Prefer repeated entities; cap to keep graph compact.
        for entity, count in Counter(ents).most_common(25):
            key = "entity:" + entity.casefold()
            nodes.setdefault(key, {"id": key, "type": "entity", "label": entity, "provenance": []})
            if sid not in nodes[key]["provenance"]: nodes[key]["provenance"].append(sid)
            edges.append({"source": sid, "target": key, "type": "mentions", "provenance": [sid], "weight": min(count, 10)})
        # Lightweight relation extraction around explicit comparative/relational verbs.
        for pat, rel in [
            (r"\b([A-Z][\w&.'’-]{2,50})\s+(?:acquired|owns|partners with|acquired by)\s+([A-Z][\w&.'’-]{2,50})", "relationship"),
            (r"\b([A-Z][\w&.'’-]{2,50})\s+(?:leads|followed|surpassed|overtook)\s+([A-Z][\w&.'’-]{2,50})", "relative_position"),
        ]:
            for m in re.finditer(pat, text):
                a, b = _clean(m.group(1)), _clean(m.group(2))
                if a in _STOP_ENTITIES or b in _STOP_ENTITIES: continue
                ak, bk = "entity:"+a.casefold(), "entity:"+b.casefold()
                nodes.setdefault(ak, {"id": ak, "type":"entity", "label":a, "provenance":[sid]})
                nodes.setdefault(bk, {"id": bk, "type":"entity", "label":b, "provenance":[sid]})
                edges.append({"source":ak,"target":bk,"type":rel,"provenance":[sid],"evidence":m.group(0)[:240]})

    # Deduplicate exact graph edges.
    seen = set(); dedup = []
    for e in edges:
        k = (e["source"], e["target"], e["type"])
        if k not in seen:
            seen.add(k); dedup.append(e)
    return {"nodes": list(nodes.values()), "edges": dedup, "source_count": len(source_nodes), "entity_count": sum(n["type"] == "entity" for n in nodes.values())}


def _numeric_source_metadata(src: dict[str, Any], chosen_text: str) -> dict[str, Any]:
    """Describe the evidence level without upgrading snippets to publisher data."""
    page_status = str(src.get("pageFetchStatus") or src.get("page_fetch_status") or "").strip().casefold()
    origin = str(
        src.get("evidenceOrigin") or src.get("evidence_origin") or
        src.get("evidenceExcerptOrigin") or src.get("evidence_excerpt_origin") or ""
    ).strip().casefold()
    direct_fields = ("pageContent", "page_content", "publisherEvidenceExcerpt", "publisher_evidence_excerpt")
    has_direct_text = any(str(src.get(key) or "").strip() for key in direct_fields)
    publisher_verified = page_status == "ok" and has_direct_text and origin not in {"okf_retrieval", "search_extract", "search_extract_only"}
    url = str(src.get("publisherPageUrl") or src.get("publisher_page_url") or src.get("url") or src.get("source_url") or "").strip()
    try:
        from utils.source_identity import canonical_http_url, source_identity_key
        canonical_url = canonical_http_url(url)
        identity = source_identity_key({**src, "url": canonical_url or url})
    except Exception:
        canonical_url, identity = url, url
    if publisher_verified:
        origin_label = origin or "publisher_page"
    elif origin:
        origin_label = origin
    elif str(src.get("searchEvidenceExcerpt") or src.get("search_evidence_excerpt") or src.get("snippet") or "").strip():
        origin_label = "search_extract"
    else:
        origin_label = "unclassified_source_text"
    return {
        "source_url": url,
        "canonical_url": canonical_url,
        "source_identity": identity,
        "publisher_page_verified": bool(publisher_verified),
        "evidence_origin": origin_label,
        "page_fetch_status": page_status or "unknown",
        "evidence_text_kind": "publisher_page" if publisher_verified else "search_extract_or_unverified",
    }


_NOISE_NUMERIC_LINE_RE = re.compile(
    r"(?i)\b(?:file\s*size|download\s*size|document\s*size|pdf\s*size|image\s*size|"
    r"kilobytes?|megabytes?|bytes?|pixels?|resolution|dpi|page\s+\d+\s+of\s+\d+|"
    r"word\s+count|token\s+count|source\s+count|memory\s+usage)\b"
)
_METRIC_HINT_RE = re.compile(
    r"(?i)\b(?:revenue|sales|profit|income|earnings|assets?|liabilit(?:y|ies)|"
    r"credit|loans?|advances|deposits?|npa|non[- ]performing|margin|ratio|rate|"
    r"growth|decline|change|share|market|value|price|cost|volume|transactions?|"
    r"users?|customers?|accounts?|capital|yield|return|roe|roa|nim|nii|casa|"
    r"capacity|production|orders?|demand|supply|inflation|gdp|repo|interest|"
    r"employment|unemployment|population|incidence|prevalence|cases|mortality|"
    r"percentage|percent|score|index|balance|funding|expenditure|investment|"
    r"utili[sz]ation|output|imports?|exports?|cash flow|turnover|volume|"
    r"debt|deficit|surplus|reserve|reserves|disbursement|collection|"
    r"renewable|generation|consumption|tariff|subscriber|churn|latency|accuracy|"
    r"precision|recall|f1|rmse|mae|error|conversion|retention)\b"
)
_PERIOD_RE = re.compile(
    r"(?i)\b(?:FY\s*(?:19|20)?\d{2}(?:\s*[-/–]\s*(?:19|20)?\d{2})?|"
    r"Q[1-4]\s*(?:FY\s*)?(?:19|20)?\d{2}|(?:19|20)\d{2}[-/]\d{1,2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?:19|20)\d{2}|"
    r"(?:19|20)\d{2})\b"
)
_DENOMINATOR_RE = re.compile(
    r"(?i)(?:\b(?:as\s+(?:a\s+)?share\s+of|as\s+%\s+of|of\s+(?:the\s+)?total|"
    r"of\s+total|per\s+\w+(?:\s+\w+){0,2})\s+([a-z][a-z0-9 /-]{1,45})|"
    r"\b(?:gross|net)\s+npa\s+(?:as\s+)?(?:a\s+)?(?:%|ratio)\s+of\s+([a-z ]{2,35}))"
)


def _cell_location(line: str, absolute_position: int) -> tuple[list[str], int]:
    """Return pipe-delimited table cells and the cell containing a numeric match."""
    spans = list(re.finditer(r"[^|]+", line))
    cells = [re.sub(r"\s+", " ", m.group()).strip() for m in spans]
    cell_index = 0
    distance = None
    for i, m in enumerate(spans):
        d = 0 if m.start() <= absolute_position <= m.end() else min(abs(absolute_position - m.start()), abs(absolute_position - m.end()))
        if distance is None or d < distance:
            distance, cell_index = d, i
    return cells, cell_index


def extract_numeric_evidence(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract numeric observations with a metric-first evidence contract.

    Every retained row records its metric, value/unit, period, entity,
    denominator, source URL and a short exact source line. Search snippets are
    retained as explicitly unverified candidates; they are never labelled as
    verified publisher-page evidence. Incidental file/page sizes and unlabeled
    numbers are rejected before they can feed a chart or derived statistic.
    """
    rows: list[dict[str, Any]] = []
    max_per_source_chars = 60000
    max_total_rows = 1200
    for i, src in enumerate(sources or []):
        if not isinstance(src, dict):
            continue
        sid = str(src.get("id") or src.get("source_id") or f"S{i+1}")
        text = _source_text(src)[:max_per_source_chars]
        if not text:
            continue
        source_meta = _numeric_source_metadata(src, text)
        for match in _NUM_RE.finditer(text):
            raw = match.group().strip()
            value = _parse_num(raw)
            if value is None or not math.isfinite(value):
                continue
            has_explicit_unit = bool(re.search(
                r"(?:USD|US\$|INR|Rs\.?|₹|\$|€|£|%|bps|basis points?|bn|billion|mn|million|crore|cr|lakh|lakhs|GW|MW|KW|TWh|GWh|MWh|kWh|BUs?|units?|accounts?|customers?|users?|projects?|sites?|employees?|points?|months?|quarters?|years?|times|x)\s*$",
                raw, re.I,
            ))
            # A plain four-digit year labels a period; it is not a measurement.
            if re.fullmatch(r"(?:19|20)\d{2}", raw) and not has_explicit_unit:
                continue

            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            raw_line = text[line_start:line_end]
            line = raw_line.strip()
            if not line:
                continue
            leading_ws = len(raw_line) - len(raw_line.lstrip())
            rel_pos = max(0, match.start() - line_start - leading_ws)
            # Evaluate incidental-number noise against the sentence containing
            # this match, not the entire physical line: extracted snippets often
            # flatten several unrelated sentences onto one line.
            sentence_boundaries = [m.end() for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)]
            sentence_start_pos = max([0] + [pos for pos in sentence_boundaries if pos <= rel_pos])
            sentence_end_pos = min([len(line)] + [pos for pos in sentence_boundaries if pos > rel_pos])
            sentence_fragment = line[sentence_start_pos:sentence_end_pos]
            if _NOISE_NUMERIC_LINE_RE.search(sentence_fragment):
                continue

            # Context spans only the nearby sentence/line before the number;
            # the exact line below is preserved separately for verification.
            prefix = text[:match.start()]
            recent_prefix_start = max(0, len(prefix) - 1800)
            sentence_breaks = list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", prefix[recent_prefix_start:]))
            line_break = prefix.rfind("\n")
            sentence_start = (recent_prefix_start + sentence_breaks[-1].end()) if sentence_breaks else max(line_break + 1, recent_prefix_start)
            context = _clean(text[sentence_start:match.start()])[-220:]

            cells, cell_index = _cell_location(line, rel_pos)
            table_like = len(cells) >= 3 and "|" in line
            cell_text = cells[cell_index] if cell_index < len(cells) else ""
            preceding = [cell for cell in cells[:cell_index] if cell and not re.fullmatch(r"[-+\d,.%₹$€£ ]+", cell)] if table_like else []
            # Table row layouts vary: both ``metric | entity | value`` and
            # ``entity | metric | value`` occur in publisher tables. Choose the
            # strongest metric-labelled cell instead of assuming the last text
            # cell before the number is always the metric.
            text_cells = [cell for cell in preceding if cell and not re.fullmatch(r"[-+\d,.%₹$€£ ]+", cell)] if table_like else []
            hint_cells = [cell for cell in text_cells if _METRIC_HINT_RE.search(cell)]
            metric_cell = max(hint_cells, key=lambda value: len(_METRIC_HINT_RE.findall(value)), default="")
            if table_like and not metric_cell and text_cells:
                # A named row heading can itself be the metric if there is only
                # one textual cell (e.g. ``Gross NPA | 2.1%``).
                metric_cell = text_cells[0] if len(text_cells) == 1 else ""
            entity_cell = next((cell for cell in text_cells if cell != metric_cell), "")
            inferred_entity = ""
            if not table_like:
                subject_match = re.search(
                    r"(?i)^(?P<metric>.+?)\s+(?:of|for|among)\s+(?P<entity>.+?)\s+(?:was|were|is|are|stood|reached|reported|recorded|remained|rose|fell|declined|increased)\b",
                    context,
                )
                if subject_match and _METRIC_HINT_RE.search(subject_match.group("metric")):
                    candidate_entity = re.sub(r"\s+", " ", subject_match.group("entity")).strip(" .,:;|-|")
                    if len(candidate_entity) >= 4:
                        metric_source = subject_match.group("metric")
                        inferred_entity = candidate_entity[:120]
                    else:
                        metric_source = context
                else:
                    metric_source = context
            else:
                metric_source = metric_cell if metric_cell else context
            metric = _metric_context_key(metric_source)
            if not metric and _METRIC_HINT_RE.search(metric_source):
                metric = re.sub(r"\s+", " ", metric_source).strip(" :-|,.;")[-100:]
            # A number is not an observation merely because it occurs near a
            # source title. It needs an explicit metric label in its row/context.
            has_metric = bool(_METRIC_HINT_RE.search(metric_source)) or bool(table_like and metric_cell and len(text_cells) == 1)
            if not has_metric:
                continue
            # Reject boilerplate strings that happen to include a number and a
            # generic word (for example page counts, version IDs and dimensions).
            if re.search(r"(?i)\b(?:version|release|download|page|pages|figure|table|appendix|source id|document id|tracking id|isbn|issn)\b", context) and not _METRIC_HINT_RE.search(metric_source):
                continue

            # Bind each value to the nearest relevant period. For prose such as
            # ``Revenue was $100m in 2020 and $121m in 2022``, selecting the first
            # year on the whole line falsely assigned both values to 2020.
            line_periods = list(_PERIOD_RE.finditer(line))
            future_periods = [p for p in line_periods if p.start() >= rel_pos + len(raw) and p.start() - (rel_pos + len(raw)) <= 90]
            if future_periods:
                period_match = future_periods[0]
            elif line_periods:
                period_match = min(line_periods, key=lambda p: min(abs(p.start() - rel_pos), abs(p.end() - (rel_pos + len(raw)))))
            else:
                period_match = None
            if not period_match:
                window_start, window_end = max(0, match.start() - 100), min(len(text), match.end() + 100)
                window_periods = list(_PERIOD_RE.finditer(text[window_start:window_end]))
                if window_periods:
                    absolute_match_pos = match.start() - window_start
                    period_match = min(window_periods, key=lambda p: abs((p.start() + window_start) - absolute_match_pos))
            period = re.sub(r"\s+", " ", period_match.group()).strip() if period_match else ""
            year_match = _YEAR_RE.search(period)
            if not year_match:
                year_match = _YEAR_RE.search(line)
            if not year_match:
                window_start, window_end = max(0, match.start() - 45), min(len(text), match.end() + 65)
                year_match = _YEAR_RE.search(text[window_start:window_end])
            year = int(year_match.group()) if year_match else None

            lower_raw = raw.casefold()
            if "%" in lower_raw:
                unit = "percent"
                unit_label = "%"
            elif re.search(r"(?:USD|US\$|INR|Rs\.?|₹|\$|€|£)", raw, re.I):
                unit, unit_label = "absolute", "currency:" + ("INR" if re.search(r"INR|Rs\.?|₹", raw, re.I) else "USD" if re.search(r"USD|US\$|\$", raw, re.I) else "EUR" if re.search(r"€", raw) else "GBP")
            elif re.search(r"(?:bps|basis points?)", raw, re.I):
                unit, unit_label = "absolute", "basis_points"
            elif re.search(r"(?:bn|billion|mn|million|crore|cr|lakh|lakhs)\b", raw, re.I):
                unit, unit_label = "absolute", re.sub(r"\s+", " ", re.search(r"(?:bn|billion|mn|million|crore|cr|lakh|lakhs)\b", raw, re.I).group().casefold())
            elif re.search(r"(?:GW|MW|KW|TWh|GWh|MWh|kWh|BUs?|units?|accounts?|customers?|users?|projects?|sites?|employees?|points?|months?|quarters?|years?|times|x)\b", raw, re.I):
                unit, unit_label = "absolute", re.sub(r"\s+", " ", re.search(r"(?:GW|MW|KW|TWh|GWh|MWh|kWh|BUs?|units?|accounts?|customers?|users?|projects?|sites?|employees?|points?|months?|quarters?|years?|times|x)\b", raw, re.I).group().casefold())
            else:
                unit, unit_label = "raw", "raw"

            denominator = ""
            # Search immediately after the reported value first, so a subject
            # phrase like "of Public Sector Banks" is not mistaken for a ratio
            # denominator when the sentence also says "of total advances".
            denominator_context = line[rel_pos + len(raw):] + " " + metric_source
            den_match = _DENOMINATOR_RE.search(denominator_context) or _DENOMINATOR_RE.search(metric_source)
            if den_match:
                denominator = re.sub(r"\s+", " ", next((x for x in den_match.groups() if x), "")).strip(" .,:;|-")[:100]
                denominator = re.sub(
                    r"\s+(?:in|during|for|as\s+of)\s+(?:(?:19|20)\d{2}|FY\s*(?:19|20)?\d{2}(?:[-/–](?:19|20)?\d{2})?)\s*$",
                    "", denominator, flags=re.I,
                ).strip()
            elif re.search(r"(?i)\b(?:of|to)\s+(?:total\s+)?(?:assets|advances|loans|deposits|credit|liabilities|revenue|income|equity)\b", metric_source):
                denominator = re.sub(r"\s+", " ", re.search(r"(?i)\b(?:of|to)\s+(?:total\s+)?(?:assets|advances|loans|deposits|credit|liabilities|revenue|income|equity)\b", metric_source).group()).strip()
            elif "/" in metric_source:
                denominator = metric_source.split("/", 1)[1].strip(" :-|,.;")[:100]

            entity = entity_cell or inferred_entity
            # Do not promote a publisher/report title (for example, "RBI data"
            # or a source-provider headline) into the observed entity. Entity is
            # only populated from an explicit row label or a local subject phrase.
            quote = line if len(line) <= 700 else line[max(0, rel_pos - 260):rel_pos + 260]
            quote = quote.strip()[:700]
            raw_norm = round(float(value), 8)
            rows.append({
                "source_id": sid,
                "source_ids": [sid],
                "raw": raw,
                "value": value,
                "unit": unit,
                "unit_label": unit_label,
                "metric": metric or re.sub(r"\s+", " ", metric_source).strip(" :-|,.;")[:120],
                "entity": re.sub(r"\s+", " ", entity).strip()[:120],
                "period": period[:80],
                "year": year,
                "denominator": denominator,
                "context": context,
                "evidence_quote": quote,
                "evidence_url": source_meta["source_url"],
                "canonical_url": source_meta["canonical_url"],
                "source_identity": source_meta["source_identity"],
                "publisher_page_verified": source_meta["publisher_page_verified"],
                "evidence_origin": source_meta["evidence_origin"],
                "page_fetch_status": source_meta["page_fetch_status"],
                "evidence_text_kind": source_meta["evidence_text_kind"],
                "_dedup_value": raw_norm,
            })
            if len(rows) >= max_total_rows:
                break
        if len(rows) >= max_total_rows:
            break

    # Merge duplicate observations across URL aliases/copies by semantic
    # identity, not by numeric value alone or source ID alone. Different
    # periods, entities, units, denominators, or conflicting values survive.
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        metric_key = _metric_context_key(row.get("metric") or row.get("context"))
        entity_key = re.sub(r"[^a-z0-9]+", " ", str(row.get("entity") or "").casefold()).strip()
        period_key = re.sub(r"[^a-z0-9]+", " ", str(row.get("period") or row.get("year") or "").casefold()).strip()
        denominator_key = re.sub(r"[^a-z0-9]+", " ", str(row.get("denominator") or "").casefold()).strip()
        key = (metric_key, entity_key, period_key, row.get("unit_label"), denominator_key, row.get("_dedup_value"))
        if key not in merged:
            merged[key] = row
            order.append(key)
            continue
        prior = merged[key]
        source_ids = list(dict.fromkeys(list(prior.get("source_ids") or [prior.get("source_id")]) + list(row.get("source_ids") or [row.get("source_id")]) ))
        prior["source_ids"] = [sid for sid in source_ids if sid]
        # Prefer publisher-page verification and the strongest exact evidence.
        if bool(row.get("publisher_page_verified")) and not bool(prior.get("publisher_page_verified")):
            keep_source_ids = prior["source_ids"]
            merged[key] = {**row, "source_ids": keep_source_ids}
        elif len(str(row.get("evidence_quote") or "")) > len(str(prior.get("evidence_quote") or "")):
            prior["evidence_quote"] = row["evidence_quote"]
        prior = merged[key]
        prior.pop("_dedup_value", None)
    output = []
    for key in order:
        item = merged[key]
        item.pop("_dedup_value", None)
        output.append(item)
    return output

def _metric_context_key(context: object) -> str:
    """Normalize the nearby metric description without removing metric nouns.

    This favors omitting a derived statistic over mixing unrelated values. Dates,
    numeric literals, currencies and scale units are metadata; metric descriptors
    such as revenue, capacity or market size remain in the key.
    """
    text = str(context or "").casefold()
    text = re.sub(r"\bfy\s*(?:20)?\d{2}(?:\s*[-/–]\s*(?:20)?\d{2})?\b", " ", text, flags=re.I)
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"(?<![a-z])[-+]?\d[\d,]*(?:\.\d+)?%?", " ", text)
    text = re.sub(r"\b(?:usd|inr|rs|rupees?|us\$|million|billion|trillion|thousand|crore|cr|lakh|lakhs|bn|mn|k|m|b|t|gw|mw|kw|twh|gwh|mwh|kwh|%)\b", " ", text)
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "were", "was", "are", "is", "into", "over", "under",
        "during", "according", "reported", "expected", "projected", "estimated", "approximately", "about", "around",
        "nearly", "roughly", "rose", "grew", "growth", "increased", "declined", "fell", "reached", "stood", "at",
        "in", "on", "to", "of", "by", "per", "as", "than", "between", "compared", "year", "years", "annual",
    }
    tokens = [token for token in re.findall(r"[a-z]{3,}", text) if token not in stop]
    return " ".join(tokens[-5:])


def _numeric_series_identity(row: dict[str, Any]) -> tuple[str, str, str, str]:
    """Keep independently scoped entities, denominators and units in separate series."""
    metric = _metric_context_key(row.get("metric") or row.get("context"))
    entity = re.sub(r"[^a-z0-9]+", " ", str(row.get("entity") or "").casefold()).strip()
    denominator = re.sub(r"[^a-z0-9]+", " ", str(row.get("denominator") or "").casefold()).strip()
    unit = re.sub(r"[^a-z0-9%]+", " ", str(row.get("unit_label") or row.get("unit") or "").casefold()).strip()
    return metric, entity, denominator, unit


def compute_statistics(numeric: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive trends only inside a coherent, non-conflicting metric series.

    Averaging every number by year can blend revenue, profit, capacity, market
    size and other distinct metrics into a plausible-looking but meaningless
    CAGR. Group by metric context and require one unambiguous value per year.
    Duplicate-source observations may corroborate the same rounded value, but
    conflicting values for a metric/year block that series from derivation.
    """
    by_metric: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in numeric:
        if not isinstance(row, dict) or not row.get("year") or row.get("unit") != "absolute":
            continue
        try:
            year = int(row["year"])
            value = float(row["value"])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        series_identity = _numeric_series_identity(row)
        metric = " | ".join(part for part in series_identity if part)
        if not series_identity[0]:
            continue
        by_metric[metric][year].append({**row, "year": year, "value": value})

    stats: dict[str, Any] = {"methods": [], "derived": [], "yearly_observations": [], "series_skipped": []}
    for metric, year_map in sorted(by_metric.items()):
        yearly: list[tuple[int, float, list[str]]] = []
        ambiguous = False
        for year, rows in sorted(year_map.items()):
            values = [float(row["value"]) for row in rows]
            low, high = min(values), max(values)
            if high - low > max(1e-8, abs(low) * 0.002):
                ambiguous = True
                stats["series_skipped"].append({
                    "metric": metric,
                    "year": year,
                    "reason": "conflicting_values_for_same_metric_and_year",
                    "source_ids": sorted({str(row.get("source_id") or "") for row in rows if row.get("source_id")}),
                })
                continue
            value = sum(values) / len(values)
            source_ids = sorted({str(row.get("source_id") or "") for row in rows if row.get("source_id")})
            yearly.append((year, value, source_ids))
            stats["yearly_observations"].append({
                "metric": metric, "year": year, "value": round(value, 4),
                "source_ids": source_ids, "corroborating_observations": len(rows),
            })

        if ambiguous or len(yearly) < 2:
            continue
        yearly.sort(key=lambda item: item[0])
        y0, v0, s0 = yearly[0]
        y1, v1, s1 = yearly[-1]
        if v0 > 0 and v1 > 0 and y1 > y0:
            cagr = (v1 / v0) ** (1 / (y1 - y0)) - 1
            stats["derived"].append({
                "metric": "CAGR", "series": metric, "value": round(cagr * 100, 2),
                "from_year": y0, "to_year": y1, "from_value": round(v0, 4), "to_value": round(v1, 4),
                "source_ids": sorted(set(s0 + s1)), "formula": "(end/start)^(1/years)-1",
            })
            if "CAGR" not in stats["methods"]:
                stats["methods"].append("CAGR")

        if len(yearly) >= 3:
            xs = [float(year) for year, _, _ in yearly]
            ys = [value for _, value, _ in yearly]
            xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
            den = sum((x - xm) ** 2 for x in xs)
            if den:
                slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den
                intercept = ym - slope * xm
                ss_tot = sum((y - ym) ** 2 for y in ys)
                ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
                r2 = 1 - ss_res / ss_tot if ss_tot else None
                stats["derived"].append({
                    "metric": "linear_trend", "series": metric, "slope_per_year": round(slope, 4),
                    "intercept": round(intercept, 2), "r_squared": round(r2, 4) if r2 is not None else None,
                    "observations": len(yearly),
                    "source_ids": sorted({sid for _, _, ids in yearly for sid in ids}),
                })
                if "OLS linear trend" not in stats["methods"]:
                    stats["methods"].append("OLS linear trend")
    return stats


def detect_contradictions(numeric: list[dict[str, Any]], tolerance: float = 0.08) -> list[dict[str, Any]]:
    """Flag materially different estimates for the same metric AND period.

    Distinct values across different years are a time series, not a conflict.
    If a period is unknown, keep the same context group isolated from dated
    observations rather than comparing unlike horizons.
    """
    groups = defaultdict(list)
    for row in numeric:
        if row.get("unit") != "absolute":
            continue
        series_identity = _numeric_series_identity(row)
        context = " | ".join(part for part in series_identity if part)
        if len(series_identity[0]) < 3:
            continue
        period = str(row.get("period") or row.get("year") or "period-unknown")
        groups[(context, period)].append(row)
    contradictions = []
    for (context, period), rows in groups.items():
        # Repeated copies of an identical value are corroboration, not a conflict.
        unique_values = {round(float(r["value"]), 8) for r in rows if math.isfinite(float(r["value"]))}
        vals = sorted(unique_values)
        if len(vals) < 2:
            continue
        lo, hi = vals[0], vals[-1]
        if lo and (hi - lo) / abs(lo) > tolerance:
            contradictions.append({
                "context": context, "period": period,
                "values": [{"source_id": r["source_id"], "raw": r["raw"], "value": r["value"]} for r in rows],
                "note": "Conflicting numeric estimates detected for the same metric and period; preserve source definitions/scope rather than averaging.",
            })
    return contradictions[:30]

def build_research_engine_context(sources: list[dict[str, Any]], question: str = "") -> dict[str, Any]:
    graph=build_evidence_graph(sources, question)
    numeric=extract_numeric_evidence(sources)
    stats=compute_statistics(numeric)
    contradictions=detect_contradictions(numeric)
    return {"graph":graph,"numeric_evidence":numeric,"statistics":stats,"contradictions":contradictions}


def format_engine_context(
    ctx: dict[str, Any], max_chars: int = 14000, max_numeric_observations: int = 45,
) -> str:
    g = ctx.get("graph", {})
    stats = ctx.get("statistics", {})
    contradictions = ctx.get("contradictions", [])
    lines = [
        "EVIDENCE + ANALYTICS LAYER (deterministic, source-grounded; use as validation/analysis, not as a source of invented facts):",
        f"Graph: {g.get('entity_count', 0)} entities across {g.get('source_count', 0)} sources and {len(g.get('edges', []))} deduplicated relations.",
    ]
    entities = [node for node in g.get("nodes", []) if node.get("type") == "entity"]
    if entities:
        lines.append("Key graph entities: " + ", ".join(node["label"] for node in entities[:35]))
    derived = stats.get("derived", [])
    if derived:
        lines.append("Validated derived statistics:")
        for item in derived[:30]:
            lines.append("- " + str(item))
    if contradictions:
        lines.append("Potential numeric contradictions (do not average; reconcile scope/definition):")
        for item in contradictions[:10]:
            lines.append("- " + str(item))
    skipped = stats.get("series_skipped", [])
    if skipped:
        lines.append("Derived metric series omitted because same-year values conflict (do not calculate a trend until reconciled):")
        for item in skipped[:10]:
            lines.append("- " + str(item))

    numeric = [row for row in (ctx.get("numeric_evidence", []) or []) if isinstance(row, dict)]
    if numeric:
        # Round-robin by source so one very data-heavy source cannot consume the
        # entire preview and hide values from other fetched publishers.
        by_source: dict[str, list[dict[str, Any]]] = {}
        for row in numeric:
            by_source.setdefault(str(row.get("source_id") or "unknown"), []).append(row)
        selected: list[dict[str, Any]] = []
        source_ids = list(by_source)
        depth = 0
        while len(selected) < max(0, int(max_numeric_observations)):
            added_this_round = False
            for source_id in source_ids:
                values = by_source[source_id]
                if depth < len(values):
                    selected.append(values[depth])
                    added_this_round = True
                    if len(selected) >= max_numeric_observations:
                        break
            if not added_this_round:
                break
            depth += 1
        lines.append(f"Selected numeric observations with source provenance ({len(selected)} of {len(numeric)} extracted observations; source-balanced):")
        for row in selected:
            evidence_level = "publisher page verified" if row.get("publisher_page_verified") else f"unverified:{row.get('evidence_origin', 'unknown')}"
            context_bits = [f"metric={row.get('metric') or row.get('context', '')}"]
            if row.get("entity"): context_bits.append(f"entity={row['entity']}")
            if row.get("period") or row.get("year"): context_bits.append(f"period={row.get('period') or row.get('year')}")
            if row.get("denominator"): context_bits.append(f"denominator={row['denominator']}")
            if row.get("evidence_url"): context_bits.append(f"url={row['evidence_url']}")
            lines.append(f"- [{row.get('source_id', '')}] {row.get('raw', '')} | unit={row.get('unit_label') or row.get('unit', 'raw')} | {evidence_level} | " + " | ".join(context_bits) + f" | quote={str(row.get('evidence_quote') or '')[:220]}")
    text = "\n".join(lines)
    return text[:max_chars]

