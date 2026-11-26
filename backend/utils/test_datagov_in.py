"""data.gov.in connector: configuration, selection, fetch, normalise, cache and failures (mocked HTTP only)."""
import asyncio
import json
import logging

import httpx

from utils import datagov_in as D

KEY = "TESTKEY-not-a-real-key-123456"
RESOURCES = [{"id": "aaaa1111-bbbb-2222", "title": "Installed solar capacity by year", "unit": "MW",
              "keywords": "solar capacity installed"},
             {"id": "cccc3333-dddd-4444", "title": "Rice production by state", "keywords": "rice production state"}]
PAYLOAD = {"title": "Solar capacity", "total": 4, "updated_date": "2026-03-01T00:00:00Z",
           "field": [{"id": "year", "name": "Year", "type": "keyword"},
                     {"id": "solar_mw", "name": "Solar (MW)", "type": "double"},
                     {"id": "wind_mw", "name": "Wind (MW)", "type": "double"}],
           "records": [{"year": "2021", "solar_mw": "49,342", "wind_mw": "40100"},
                       {"year": "2022", "solar_mw": "63302", "wind_mw": "41930"},
                       {"year": "2023", "solar_mw": "72018", "wind_mw": "44736"},
                       {"year": "2024", "solar_mw": "97865", "wind_mw": "48163"}]}


def _env(monkeypatch, tmp_path, key=KEY):
    if key:
        monkeypatch.setenv("DATA_GOV_IN_API_KEY", key)
    else:
        monkeypatch.delenv("DATA_GOV_IN_API_KEY", raising=False)
    monkeypatch.setenv("GG_DATAGOV_RESOURCES", json.dumps(RESOURCES))
    monkeypatch.setenv("GG_DATAGOV_CACHE_DIR", str(tmp_path))


def _run(handler, question="How has India's installed solar capacity grown?"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def go():
        try:
            return await D.datagov_sources(question, tier="detailed", client=client)
        finally:
            await client.aclose()
    return asyncio.run(go())


def test_off_without_a_key_or_resources(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, key="")
    rows, note = _run(lambda r: httpx.Response(500))
    assert rows == [] and not note["enabled"] and "DATA_GOV_IN_API_KEY" in note["reason"]
    monkeypatch.setenv("DATA_GOV_IN_API_KEY", KEY)
    monkeypatch.delenv("GG_DATAGOV_RESOURCES")
    assert D.datagov_sources is not None and _run(lambda r: httpx.Response(500))[1]["reason"] == "GG_DATAGOV_RESOURCES not set"


def test_selects_by_keywords_fetches_normalises_and_caches(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD)

    rows, note = _run(handler)
    assert note["selected"] == ["aaaa1111-bbbb-2222"] and len(rows) == 1
    req = seen[0]
    assert req.url.path == "/resource/aaaa1111-bbbb-2222" and req.url.params["format"] == "json"
    assert req.url.params["api-key"] == KEY
    row = rows[0]
    assert row["title"].startswith("data.gov.in: Installed solar capacity by year (MW)")
    assert "Year | Solar (MW) | Wind (MW)" in row["fullContent"] and "2021 | 49,342 | 40,100" in row["fullContent"]
    assert "TABLE 1 — Installed solar capacity by year (MW)" in row["fullContent"]
    assert KEY not in json.dumps(rows) and KEY not in json.dumps(note)          # the key is never stored
    rows2, _ = _run(lambda r: httpx.Response(500))                               # served from the cache
    assert rows2 and rows2[0]["fullContent"] == row["fullContent"]
    assert all(KEY not in p.read_text(encoding="utf-8") and KEY not in p.name for p in tmp_path.iterdir())


def test_failures_skip_the_resource_and_never_log_the_key(monkeypatch, tmp_path, caplog):
    _env(monkeypatch, tmp_path)
    caplog.set_level(logging.DEBUG)
    rows, note = _run(lambda r: httpx.Response(403, json={"message": "Invalid key"}))
    assert rows == [] and note["skipped"][0]["reason"] == "key rejected (HTTP 403)"

    def boom(request):
        raise httpx.ConnectError(f"failed for {request.url}")   # the URL carries the key
    rows, note = _run(boom)
    assert rows == [] and note["skipped"][0]["reason"] == "network error (ConnectError)"
    rows, note = _run(lambda r: httpx.Response(200, json={"message": "Resource not found"}))
    assert rows == [] and "no records" in note["skipped"][0]["reason"]
    assert KEY not in caplog.text


def test_a_429_is_retried_once_after_the_servers_delay(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    calls = {"n": 0}
    waits = []

    async def fake_sleep(s):
        waits.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "3"}) if calls["n"] == 1 else httpx.Response(200, json=PAYLOAD)
    rows, _ = _run(handler)
    assert rows and waits == [3.0]


def test_normalise_needs_a_label_and_numbers():
    res = D.Resource(id="x" * 10)
    assert D.normalise([{"a": "x", "b": "y"}] * 5, {}, res) is None
    table = D.normalise([{"state": s, "tonnes": str(v), "year": "2024"} for s, v in
                         [("Punjab", 12), ("Bihar", 7), ("Assam", 5)]], {}, res)
    assert table["label"] == "state" and table["columns"] == ["tonnes"]       # the year column is not a value
