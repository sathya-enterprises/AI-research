"""India Open Government Data (data.gov.in) connector - configurable, off unless configured.

Each configured data.gov.in resource relevant to the question is fetched through the platform's record API,
normalised to one table (a label column and its numeric columns), cached, and added as a source row exactly like
the World Bank / IMF datasets (utils.official_data.as_source): cited, number-checked and chartable.

Configuration (environment):

DATA_GOV_IN_API_KEY      the API key of a registered data.gov.in account. Without it the connector is off.
                         The key is sent only as the request's ``api-key`` parameter and never logged, cached or
                         stored in a source row (error messages are reduced to the exception type, because an
                         HTTP client's error text can contain the request URL).
GG_DATAGOV_RESOURCES     JSON list of resources this deployment uses:
                         [{"id": "<resource id>", "title": "Installed renewable capacity by state",
                           "keywords": "renewable solar wind capacity state", "unit": "MW",
                           "label_field": "state_ut", "value_fields": ["solar_power", "wind_power"],
                           "page_url": "https://www.data.gov.in/resource/..."}]
                         Only "id" and "keywords" are required; fields are detected when not named.
GG_DATAGOV_MAX_RESOURCES resources per report (default 2 detailed / 4 in-depth).
GG_DATAGOV_BASE_URL      API base (default https://api.data.gov.in).
GG_DATAGOV_CACHE_DIR     response cache directory (default backend/.cache/datagov); GG_DATAGOV_CACHE_HOURS (24).
GG_DATAGOV_MAX_RECORDS   records read per resource (default 500, fetched in pages of 100).

Assumed API contract (to confirm with a real key, see docs/CONFIGURATION.md): GET
{base}/resource/{id}?api-key=...&format=json&offset=N&limit=N returns JSON with "records" (a list of objects)
and, usually, "field" (a list of {"id", "name", "type"}), "title" and "total". Parsing is defensive: without
"field" the record keys are used; anything unexpected skips the resource with a note, never the report.
Keyword search of the catalogue is not used: the platform's search API could not be verified.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

log = logging.getLogger("datagov_in")


class RedactSecretParams(logging.Filter):
    """Masks key-like query parameters in log records. httpx logs every request URL at INFO
    ("HTTP Request: GET https://api.data.gov.in/resource/...?api-key=<key>&..."), which would put the key in
    the server log."""
    _RE = re.compile(r"(?i)([?&](?:api[-_]?key|key|token|access_token|apikey)=)[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = self._RE.sub(r"\1[redacted]", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


for _name in ("httpx", "httpcore"):
    if not any(isinstance(f, RedactSecretParams) for f in logging.getLogger(_name).filters):
        logging.getLogger(_name).addFilter(RedactSecretParams())

PUBLISHER = "data.gov.in"
_YEARISH = re.compile(r"^\s*(?:(?:19|20)\d{2}(?:\s*-\s*\d{2,4})?|FY\s*\d{2,4}(?:-\d{2})?)\s*$", re.I)


@dataclass
class Resource:
    id: str
    title: str = ""
    keywords: str = ""
    unit: str = ""
    label_field: str = ""
    value_fields: list[str] = field(default_factory=list)
    page_url: str = ""


def api_key() -> str:
    return os.getenv("DATA_GOV_IN_API_KEY", "").strip()


def configured_resources() -> list[Resource]:
    raw = os.getenv("GG_DATAGOV_RESOURCES", "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        log.warning("GG_DATAGOV_RESOURCES is not valid JSON; data.gov.in is off")
        return []
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and re.fullmatch(r"[A-Za-z0-9-]{8,80}", str(item.get("id") or "")):
            out.append(Resource(id=str(item["id"]), title=str(item.get("title") or ""),
                                keywords=str(item.get("keywords") or ""), unit=str(item.get("unit") or ""),
                                label_field=str(item.get("label_field") or ""),
                                value_fields=[str(v) for v in item.get("value_fields") or []],
                                page_url=str(item.get("page_url") or "")))
    return out


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{3,}", str(text or "").casefold())}


def select(question: str, resources: list[Resource], limit: int) -> list[Resource]:
    """The configured resources whose keywords the question shares (at least two keyword stems, or one when the
    resource lists a single keyword), best match first."""
    asked = _words(question)
    scored = []
    for r in resources:
        keys = _words(r.keywords or r.title)
        hits = len(asked & keys)
        if hits >= min(2, len(keys)) and hits:
            scored.append((hits, r))
    return [r for _hits, r in sorted(scored, key=lambda x: -x[0])[:limit]]


# ── cache ─────────────────────────────────────────────────────────────────────────────────────────
def _cache_dir() -> Path:
    return Path(os.getenv("GG_DATAGOV_CACHE_DIR") or Path(__file__).resolve().parent.parent / ".cache" / "datagov")


def _cache_path(resource_id: str, offset: int, limit: int) -> Path:
    digest = hashlib.sha256(f"{resource_id}|{offset}|{limit}".encode()).hexdigest()[:24]  # never the key
    return _cache_dir() / f"{digest}.json"


def _cache_get(resource_id: str, offset: int, limit: int) -> dict | None:
    path = _cache_path(resource_id, offset, limit)
    try:
        hours = float(os.getenv("GG_DATAGOV_CACHE_HOURS", "24") or 24)
        if path.exists() and time.time() - path.stat().st_mtime < hours * 3600:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return None


def _cache_put(resource_id: str, offset: int, limit: int, payload: dict) -> None:
    try:
        path = _cache_path(resource_id, offset, limit)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# ── fetch ─────────────────────────────────────────────────────────────────────────────────────────
class DataGovError(Exception):
    """A resource could not be read; ``reason`` is safe to log (it never contains the key or the URL)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


async def _page(client, resource: Resource, offset: int, limit: int) -> dict:
    cached = _cache_get(resource.id, offset, limit)
    if cached is not None:
        return cached
    base = os.getenv("GG_DATAGOV_BASE_URL", "https://api.data.gov.in").rstrip("/")
    params = {"api-key": api_key(), "format": "json", "offset": str(offset), "limit": str(limit)}
    for attempt in range(2):
        try:
            response = await client.get(f"{base}/resource/{resource.id}", params=params)
        except Exception as exc:  # the message may contain the URL with the key: log the type only
            raise DataGovError(f"network error ({type(exc).__name__})") from None
        if response.status_code == 429 and attempt == 0:
            from utils.llm_quota import parse_retry_delay
            import asyncio
            await asyncio.sleep(min(30.0, parse_retry_delay(response.headers) or 5.0))
            continue
        if response.status_code in (401, 403):
            raise DataGovError(f"key rejected (HTTP {response.status_code})")
        if response.status_code == 429:
            raise DataGovError("rate limited (HTTP 429)")
        if response.status_code != 200:
            raise DataGovError(f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise DataGovError("response is not JSON") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            message = str(payload.get("message") or payload.get("error") or "")[:80] if isinstance(payload, dict) else ""
            raise DataGovError(f"no records in the response{': ' + message if message else ''}")
        _cache_put(resource.id, offset, limit, payload)
        return payload
    raise DataGovError("rate limited (HTTP 429)")


async def fetch_records(client, resource: Resource) -> tuple[list[dict], dict]:
    """All records (up to GG_DATAGOV_MAX_RECORDS) and the first page's metadata."""
    cap = max(1, int(os.getenv("GG_DATAGOV_MAX_RECORDS", "500") or 500))
    records: list[dict] = []
    meta: dict = {}
    offset, size = 0, 100
    while len(records) < cap:
        payload = await _page(client, resource, offset, size)
        if not meta:
            meta = {k: payload.get(k) for k in ("title", "desc", "field", "total", "updated_date", "org", "sector")}
        batch = [r for r in payload["records"] if isinstance(r, dict)]
        records += batch
        total = payload.get("total")
        offset += size
        if len(batch) < size or (isinstance(total, int) and offset >= total):
            break
    return records[:cap], meta


# ── normalise ─────────────────────────────────────────────────────────────────────────────────────
def _number(value) -> float | None:
    text = str(value if value is not None else "").replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text):
        return None
    return float(text)


def normalise(records: list[dict], meta: dict, resource: Resource) -> dict | None:
    """{"label": name, "columns": [names], "rows": [[label, v1, ...]]} - one label column and the numeric columns,
    or None when the resource has no usable numeric table."""
    if not records:
        return None
    names = {}
    for f in meta.get("field") or []:
        if isinstance(f, dict) and f.get("id"):
            names[str(f["id"])] = str(f.get("name") or f["id"])
    keys = [k for k in records[0].keys()]
    numeric = [k for k in keys if sum(1 for r in records if _number(r.get(k)) is not None) >= 0.8 * len(records)]
    value_fields = [k for k in resource.value_fields if k in keys] or numeric
    label_field = resource.label_field if resource.label_field in keys else next(
        (k for k in keys if k not in numeric and any(str(r.get(k) or "").strip() for r in records)), "")
    if not label_field:
        # a year column is numeric: it is the label, not a value
        label_field = next((k for k in numeric if all(_YEARISH.match(str(r.get(k) or "")) for r in records)), "")
    year_cols = {k for k in numeric if all(_YEARISH.match(str(r.get(k) or "")) for r in records)}
    value_fields = [k for k in value_fields if k != label_field and (k not in year_cols or k in resource.value_fields)][:6]
    if not label_field or not value_fields:
        return None
    rows = []
    for r in records:
        label = re.sub(r"\s+", " ", str(r.get(label_field) or "")).strip()
        values = [_number(r.get(k)) for k in value_fields]
        if label and any(v is not None for v in values):
            rows.append([label] + values)
    if len(rows) < 3:
        return None
    if all(_YEARISH.match(r[0]) for r in rows):
        rows.sort(key=lambda r: r[0])
    return {"label": names.get(label_field, label_field), "columns": [names.get(k, k) for k in value_fields],
            "rows": rows[:60]}


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}" if float(value).is_integer() and abs(value) >= 1000 else f"{value:g}"


def as_source(resource: Resource, table: dict, meta: dict, retrieved: str) -> dict:
    title = resource.title or str(meta.get("title") or "") or f"data.gov.in resource {resource.id}"
    unit = f" ({resource.unit})" if resource.unit else ""
    header = f"{table['label']} | " + " | ".join(table["columns"])
    lines = [f"{r[0]} | " + " | ".join(_fmt(v) for v in r[1:]) for r in table["rows"]]
    url = resource.page_url or f"{os.getenv('GG_DATAGOV_BASE_URL', 'https://api.data.gov.in').rstrip('/')}/resource/{resource.id}"
    updated = str(meta.get("updated_date") or "")[:10]
    text = "\n".join([
        f"OFFICIAL DATASET — Open Government Data Platform India (data.gov.in): {title}{unit} [{resource.id}]",
        f"Retrieved from the data.gov.in API on {retrieved}" + (f"; the publisher last updated it on {updated}" if updated else "")
        + ". Empty cells are values the resource does not publish.",
        f"TABLE 1 — {title}{unit}",
        header, *lines,
    ])
    return {
        "title": f"{PUBLISHER}: {title}{unit}", "url": url, "publisher": "Open Government Data Platform India",
        "retrievedAt": retrieved, "snippet": f"{title}{unit} ({len(table['rows'])} rows, data.gov.in).",
        "fullContent": text, "pageContent": text, "pageFetchStatus": "ok", "pageFetchHttpStatus": 200,
        "evidenceOrigin": "publisher_page", "sourceType": "Official dataset", "kind": "Official dataset",
        "authorityTier": "primary_official", "reliabilityScore": 0.95,
        "reliabilityNote": "primary official (Government of India open data, retrieved via the data.gov.in API)",
        "officialDataset": {"id": f"datagov.{resource.id}", "code": resource.id, "unit": resource.unit,
                            "entities": table["columns"], "rows": len(table["rows"])},
    }


async def datagov_sources(question: str, *, tier: str, today: date | None = None, client=None) -> tuple[list[dict], dict]:
    """Source rows for the configured resources the question needs, and a capture note. Never raises."""
    note: dict = {"enabled": False, "selected": [], "fetched": [], "skipped": []}
    if not api_key():
        note["reason"] = "DATA_GOV_IN_API_KEY not set"
        return [], note
    resources = configured_resources()
    if not resources:
        note["reason"] = "GG_DATAGOV_RESOURCES not set"
        return [], note
    note["enabled"] = True
    default = "4" if tier == "in_depth" else "2"
    limit = int(os.getenv("GG_DATAGOV_MAX_RESOURCES", default) or default)
    chosen = select(question, resources, limit)
    note["selected"] = [r.id for r in chosen]
    if not chosen:
        return [], note
    retrieved = (today or date.today()).isoformat()
    rows: list[dict] = []
    own_client = client is None
    if own_client:
        import httpx
        client = httpx.AsyncClient(timeout=30, follow_redirects=True,
                                   headers={"User-Agent": "GrowthGradual-Research/1.0 (official open data)"})
    try:
        for resource in chosen:
            try:
                records, meta = await fetch_records(client, resource)
            except DataGovError as exc:
                note["skipped"].append({"id": resource.id, "reason": exc.reason})
                log.warning("data.gov.in: resource %s skipped: %s", resource.id, exc.reason)
                continue
            table = normalise(records, meta, resource)
            if table is None:
                note["skipped"].append({"id": resource.id, "reason": "no numeric table in the records"})
                continue
            rows.append(as_source(resource, table, meta, retrieved))
            note["fetched"].append({"id": resource.id, "rows": len(table["rows"]), "columns": table["columns"]})
    finally:
        if own_client:
            await client.aclose()
    log.info("data.gov.in: %d selected, %d fetched", len(chosen), len(rows))
    return rows, note
