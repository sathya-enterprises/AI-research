"""Source-visual evidence discovery and relevance gating.

The extractor is deliberately independent of report presentation.  It creates a
provenance-rich inventory; composition may later choose an asset, recreate it,
or use it only as evidence.  HTML tags are discovery signals, never relevance
decisions by themselves.
"""
from __future__ import annotations

import hashlib
import re
import httpx
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

_ASSET_TYPES = {"image", "chart", "graph", "table", "figure", "diagram", "map", "infographic"}
_DECORATIVE = re.compile(r"\b(logo|icon|avatar|advert|sponsor|promotion|banner|subscribe|cookie|placeholder)\b", re.I)
_CHART = re.compile(r"\b(chart|graph|plot|trend|dashboard|visuali[sz]ation|data-viz)\b", re.I)
_MAP = re.compile(r"\b(map|geograph|location|regional|district|state-wise)\b", re.I)
_INFOGRAPHIC = re.compile(r"\b(infographic|explainer|illustration|diagram|flowchart|framework)\b", re.I)
_FACT = re.compile(r"\b(nim|npa|revenue|margin|growth|deposit|loan|credit|aov|orders?|cac|delivery|capital|market share|productivity|deal size)\b", re.I)


@dataclass
class EvidenceAsset:
    asset_id: str
    source_id: str
    source_url: str
    page_url: str
    publisher: str
    page_title: str
    asset_type: str
    asset_url: str = ""
    title: str = ""
    caption: str = ""
    alt_text: str = ""
    figure_label: str = ""
    nearby_heading: str = ""
    surrounding_text: str = ""
    raw_html_tag: str = ""
    detected_entities: list[str] = field(default_factory=list)
    detected_topics: list[str] = field(default_factory=list)
    extracted_labels: list[str] = field(default_factory=list)
    extracted_data: dict = field(default_factory=dict)
    relevance_score: float = 0.0
    relevance_reason: str = ""
    evidence_role: str = "irrelevant"
    credibility_level: str = "unknown"
    reuse_status: str = "unknown"
    license_status: str = "unknown"
    rights_notes: str = ""
    can_embed: bool = False
    can_recreate: bool = False
    can_use_as_evidence: bool = False
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def _tokens(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[a-z0-9][a-z0-9-]{2,}", (text or "").lower())))


def _publisher(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _quality(url: str, title: str) -> str:
    hay = f"{url} {title}".lower()
    if any(x in hay for x in ("rbi.org.in", "sebi.gov.in", ".gov.in", ".gov", "data.gov")):
        return "regulatory/government"
    if any(x in hay for x in ("annual report", "investor", "results", "company")):
        return "primary"
    if any(x in hay for x in ("reuters", "bloomberg", "ft.com", "economist", "mckinsey", "icra", "crisil")):
        return "recognized research" if "mckinsey" in hay or "icra" in hay or "crisil" in hay else "credible secondary"
    return "unknown"


def _score(text: str, question: str, section_titles: list[str], source_title: str) -> tuple[float, str, list[str], list[str]]:
    q = set(_tokens(question))
    hay_tokens = set(_tokens(f"{text} {source_title}"))
    entity_hits = sorted(q & hay_tokens)
    section_hits = sorted(set(_tokens(" ".join(section_titles))) & hay_tokens)
    score = min(0.98, 0.12 + len(entity_hits) * 0.08 + len(section_hits) * 0.05)
    if _FACT.search(text):
        score += 0.08
    if _DECORATIVE.search(text) and not _FACT.search(text):
        score = min(score, 0.08)
    score = round(min(score, 0.99), 2)
    if score >= 0.72:
        role = "directly relevant evidence"
        reason = f"Direct question/entity overlap ({', '.join(entity_hits[:5]) or 'context'}) with nearby source text."
    elif score >= 0.42:
        role = "supporting evidence"
        reason = f"Supports adjacent topic via {', '.join((entity_hits + section_hits)[:5]) or 'source context'}."
    elif _DECORATIVE.search(text):
        role = "decorative"
        reason = "Promotional or decorative language without analytical support."
    else:
        role = "contextual" if score >= 0.2 else "irrelevant"
        reason = "Contextual source-page visual; does not directly support a research claim."
    return score, reason, entity_hits, section_hits


def _asset_type(tag: str, attrs: dict, text: str) -> str:
    hay = " ".join([tag, attrs.get("class", ""), attrs.get("id", ""), attrs.get("aria-label", ""), text])
    if tag == "table": return "table"
    if tag == "script": return "chart"
    if tag == "svg" or tag == "canvas": return "chart" if _CHART.search(hay) else "figure"
    if _MAP.search(hay): return "map"
    if _INFOGRAPHIC.search(hay): return "infographic" if "infographic" in hay.lower() else "diagram"
    if _CHART.search(hay): return "chart"
    return "image" if tag == "img" else "figure"


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.assets: list[dict] = []
        self.heading = ""
        self.heading_depth = 0
        self.figure: dict | None = None
        self.table: dict | None = None
        self.capture: list[str] = []
        self.capture_tag = ""

    def _attrs(self, attrs): return {k: (v or "") for k, v in attrs}

    def handle_starttag(self, tag, attrs):
        a = self._attrs(attrs)
        if tag == "script":
            self.capture, self.capture_tag = [], tag
        if re.fullmatch(r"h[1-6]", tag):
            self.capture, self.capture_tag = [], tag
        if tag == "figure": self.figure = {"attrs": a, "caption": "", "text": ""}
        if tag == "table": self.table = {"attrs": a, "rows": [], "row": []}
        if tag in {"img", "svg", "canvas"}:
            item = {"tag": tag, "attrs": a, "text": "", "caption": "", "heading": self.heading, "data": {}}
            if self.figure: item["caption"] = self.figure.get("caption", "")
            self.assets.append(item)
        if tag in {"div", "section"} and (_CHART.search(" ".join(a.values())) or a.get("data-chart") or a.get("data-graph")):
            self.assets.append({"tag": "div", "attrs": a, "text": "", "caption": "", "heading": self.heading, "data": {}})
        if self.table is not None and tag in {"th", "td"}:
            self.capture, self.capture_tag = [], tag
        if tag == "figcaption": self.capture, self.capture_tag = [], tag

    def handle_endtag(self, tag):
        value = " ".join(self.capture).strip()
        if tag == "script":
            if _CHART.search(value) or (re.search(r"\b(labels|categories|xAxis)\b", value) and re.search(r"\b(data|series|values)\b", value)):
                labels = []
                match = re.search(r"(?:labels|categories)\s*[:=]\s*\[([^\]]+)\]", value, re.I)
                if match:
                    labels = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))[:50]
                self.assets.append({"tag": "script", "attrs": {}, "text": value[:4000], "caption": "", "heading": self.heading, "data": {"labels": labels, "raw": value[:2000]}})
        elif re.fullmatch(r"h[1-6]", tag):
            if value: self.heading = value[:240]
        elif tag == "figcaption" and self.figure is not None:
            self.figure["caption"] = value
        elif self.table is not None and tag in {"th", "td"}:
            self.table["row"].append(value)
        elif self.table is not None and tag == "tr":
            if self.table["row"]: self.table["rows"].append(self.table["row"])
            self.table["row"] = []
        elif tag == "table":
            self.assets.append({"tag": "table", "attrs": self.table["attrs"], "text": " ".join(" ".join(r) for r in self.table["rows"]), "caption": "", "heading": self.heading, "data": {"rows": self.table["rows"]}})
            self.table = None
        self.capture = []
        self.capture_tag = ""

    def handle_data(self, data):
        if self.capture_tag: self.capture.append(data.strip())


def extract_evidence_assets(html: str, source: dict, question: str, section_titles: list[str] | None = None) -> list[EvidenceAsset]:
    parser = _AssetParser()
    try: parser.feed(html or "")
    except Exception: pass
    source_id = str(source.get("source_id") or source.get("id") or "")
    url = str(source.get("url") or "")
    title = str(source.get("title") or "")
    sections = section_titles or []
    out: list[EvidenceAsset] = []
    for i, raw in enumerate(parser.assets, 1):
        attrs = raw.get("attrs", {})
        alt = attrs.get("alt", "")
        caption = raw.get("caption", "") or attrs.get("title", "") or attrs.get("aria-label", "")
        text = " ".join(x for x in (alt, caption, raw.get("heading", ""), raw.get("text", "")) if x).strip()
        if not text and raw.get("tag") == "img": text = attrs.get("src", "").rsplit("/", 1)[-1]
        score, reason, entities, topics = _score(text, question, sections, title)
        typ = _asset_type(raw["tag"], attrs, text)
        extracted = raw.get("data") or {}
        labels = [str(x) for row in extracted.get("rows", []) for x in row[:3]][:30]
        has_data = bool(extracted.get("rows"))
        license_text = " ".join([attrs.get("license", ""), attrs.get("data-license", ""), attrs.get("rights", "")]).strip()
        licensed = bool(license_text)
        quality = _quality(url, title)
        evidence_ok = score >= 0.42 and quality != "unknown" and not _DECORATIVE.search(text)
        asset_key = hashlib.sha1(f"{source_id}|{url}|{i}|{text}".encode()).hexdigest()[:10]
        out.append(EvidenceAsset(
            asset_id=f"V{asset_key}", source_id=source_id, source_url=url, page_url=url,
            publisher=_publisher(url), page_title=title, asset_type=typ,
            asset_url=urljoin(url, attrs.get("src", "") or attrs.get("data-src", "")),
            title=caption or raw.get("heading", ""),
            caption=caption, alt_text=alt, nearby_heading=raw.get("heading", ""), surrounding_text=raw.get("text", ""),
            raw_html_tag=raw["tag"], detected_entities=entities, detected_topics=topics, extracted_labels=labels,
            extracted_data=extracted, relevance_score=score, relevance_reason=reason, evidence_role=("directly relevant evidence" if score >= .72 else "supporting evidence" if score >= .42 else ("decorative" if _DECORATIVE.search(text) else "irrelevant")),
            credibility_level=quality, reuse_status=("licensed" if licensed else "unknown"), license_status=(license_text or "unknown"),
            rights_notes=("Explicit page license metadata found." if licensed else "No reuse permission detected; do not embed original."),
            can_embed=licensed and score >= .42, can_recreate=has_data and typ in {"table", "chart", "graph"}, can_use_as_evidence=evidence_ok,
        ))
    return out


async def discover_evidence_assets(source: dict, question: str, section_titles: list[str] | None = None) -> list[dict]:
    """Fetch one public source page and return a bounded structured asset inventory."""
    url = str(source.get("url") or "")
    if not url or url.startswith("internal://"):
        return []
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Growth Gradual evidence extractor)"})
        if not response.is_success or "html" not in response.headers.get("content-type", "text/html").lower():
            return []
        assets = extract_evidence_assets(response.text[:1_500_000], source, question, section_titles)
        return [asset.to_dict() for asset in assets[:40]]
    except Exception:
        return []


def validate_asset_uses(raw_uses: list, assets: list[dict]) -> list[dict]:
    """Keep only explicit, provenance-backed composition decisions."""
    by_id = {str(a.get("asset_id")): a for a in assets or []}
    valid_treatments = {"hero_visual", "full_width_visual", "side_visual", "inline_evidence", "supporting_visual", "comparison_visual", "appendix_visual"}
    out = []
    for raw in raw_uses or []:
        if not isinstance(raw, dict) or not raw.get("use_asset"):
            continue
        aid = str(raw.get("asset_id") or "")
        asset = by_id.get(aid)
        if not asset or not asset.get("can_use_as_evidence") or not (asset.get("can_embed") or asset.get("can_recreate")):
            continue
        treatment = str(raw.get("presentation") or "supporting_visual")
        if treatment not in valid_treatments: treatment = "supporting_visual"
        out.append({
            "asset_id": aid, "use_asset": True, "presentation": treatment,
            "caption": str(raw.get("caption") or asset.get("caption") or asset.get("title") or "Source visual evidence")[:240],
            "source_id": asset.get("source_id"), "source_url": asset.get("source_url"),
        })
    return out[:6]
