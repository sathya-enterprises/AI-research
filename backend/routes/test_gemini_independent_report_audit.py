import json
import pytest

from routes import report as report_mod


@pytest.mark.asyncio
async def test_gemini_audit_returns_unavailable_without_key(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: [])
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "unavailable"
    assert result["reason"] == "no_gemini_api_key"
    assert result["blocking"] is False


@pytest.mark.asyncio
async def test_gemini_audit_parses_structured_review(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["gemini-test-model"])

    class FakeResponse:
        status_code = 200
        is_success = True
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "status": "FAIL", "summary": "Duplicate content found", "blocking": True,
                "critical_issues": ["Repeated table"],
                "findings": [{"severity": "critical", "category": "duplication", "location": "Page 4", "issue": "Repeated table", "recommended_fix": "Remove duplicate"}],
                "checks": {"duplication": "fail"}
            })}]}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "FAIL"
    assert result["blocking"] is True
    assert result["findings"][0]["issue"] == "Repeated table"
    assert result["provider"] == "Gemini API"


@pytest.mark.asyncio
async def test_gemini_503_falls_back_to_compact_groq_audit(monkeypatch):
    monkeypatch.delenv("GG_GROQ_AUDIT_SAFE_CHARS", raising=False)
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["gemini-audit-key"])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-audit-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["gemini-audit-model"])
    monkeypatch.setenv("GG_GROQ_AUDIT_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(report_mod, "_is_rest_api_key", lambda _key: True)
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda _identifier: False)
    monkeypatch.setattr(report_mod, "iter_reserved_groq_keys", lambda keys, max_keys=1: iter(keys))
    calls = []
    groq_attempts = 0
    sources = [{"id": f"S{index}", "title": f"Source {index}", "snippet": f"Extracted value {index}"}
               for index in range(1, 31)]
    sources[-1].update({
        "url": "https://example.org/capacity.csv",
        "page_fetch_status": "ok", "evidence_excerpt_origin": "publisher_page",
        "publisher_table_chart_evidence_verified": True,
        "chartEvidenceExcerpt": "Year | Capacity (GW)\n2015 | 6\n2016 | 8\n2017 | 10\n2024 | 94",
        "_citedClaimGroups": [{
            "groupId": "late-source-claim", "sourceIds": ["S30"],
            "claim": "Capacity was 94 GW and the service lasted 12 years",
            "claimTokens": ["capacity_gw:94", "duration_years:12"],
            "supportByToken": {},
            "unsupportedTokens": ["capacity_gw:94"], "supportingSourceIds": [],
            "groupEvidenceMatched": False,
        }],
    })

    class FakeResponse:
        def __init__(self, status, payload=None):
            self.status_code = status
            self.is_success = 200 <= status < 300
            self.headers = {}
            self.payload = payload or {}

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            nonlocal groq_attempts
            calls.append(url)
            if "generativelanguage.googleapis.com" in url:
                return FakeResponse(503)
            groq_attempts += 1
            assert kwargs["json"]["response_format"] == {"type": "json_object"}
            assert kwargs["json"]["reasoning_effort"] == "low"
            assert kwargs["json"]["max_tokens"] == 1800
            user_content = kwargs["json"]["messages"][1]["content"]
            assert len(user_content) <= 10500
            payload = json.loads(user_content)
            assert payload["requiredOutput"]["status"] == "PASS|WARNING|FAIL"
            assert "ANY ONE cited source" in payload["task"]
            assert "Ignore leading #/Rank ordinals" in payload["task"]
            assert "keyed by citation ID" in payload["task"]
            assert "Never compare different types" in payload["task"]
            assert "supportByToken and source-level match flags are heuristic" in payload["task"]
            assert payload["report"]["charts"] == [["S30"]]
            assert list(payload["report"]["sources"]) == ["S1", "S30"]
            assert payload["report"]["sourceCoverage"]["allReferencedIdsIncluded"] is True
            late_source = payload["report"]["sources"]["S30"]
            assert late_source["b"] == "p"
            assert late_source["k"] == "table"
            assert "2024 | 94" in late_source["e"]
            assert payload["report"]["numericClaimEvidence"][0][2] == "capacity_gw:94;duration_years:12"
            review = {"status": "WARNING", "summary": "One source needs review", "blocking": False,
                      "findings": [{"severity": "medium", "category": "source", "issue": "Check one citation"}],
                      "checks": {"source_mapping": "warning"}}
            return FakeResponse(200, {"choices": [{"message": {"content": json.dumps(review)}}]})

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Solar capacity report", report_text="# Solar capacity\n2024 was 91 GW [S1]. Later, measured capacity was 94 GW [S30].",
        report_tier="detailed", charts=[{"title": "Capacity chart", "type": "line", "sourceIds": ["S30"]}], images=[], sources=sources,
    )

    assert result["status"] == "WARNING"
    assert result["provider"] == "Groq API"
    assert result["fallbackFrom"] == "Gemini API"
    assert result["geminiUnavailableReason"] == "http_503"
    assert len(calls) == 2
    assert groq_attempts == 1


@pytest.mark.asyncio
async def test_groq_fallback_does_not_trust_misattributed_token_support(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["gemini-audit-key"])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-audit-key"])
    monkeypatch.setattr(report_mod, "_is_rest_api_key", lambda _key: True)
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda _identifier: False)
    monkeypatch.setattr(report_mod, "iter_reserved_groq_keys", lambda keys, max_keys=1: iter(keys))
    monkeypatch.setenv("GG_GROQ_AUDIT_MODEL", "openai/gpt-oss-20b")
    group = {
        "groupId": "renewable-share-claim", "sourceIds": ["S1", "S15"],
        "claim": "China's renewable share rose from 23.97% in 2015 to 33.70% in 2024",
        "claimTokens": ["percent:23.97", "percent:33.7"],
        # Deliberately wrong: S1's fetched table is the evidence for both values.
        "supportByToken": {"percent:23.97": ["S15"], "percent:33.7": []},
    }
    sources = [
        {
            "id": "S1", "title": "Our World in Data: Share of electricity from renewables (%), 2015-2024",
            "url": "https://example.org/renewables-share", "page_fetch_status": "ok",
            "evidence_excerpt_origin": "publisher_page", "publisher_table_chart_evidence_verified": True,
            "chartEvidenceExcerpt": "Year | Brazil | China | India | Nigeria\n2015 | 74.21 | 23.97 | 14.63 | 19.51\n2024 | 87.33 | 33.70 | 19.81 | 25.00",
            "_citedClaimGroups": [group],
        },
        {
            "id": "S15", "title": "Major Countries and Regions", "url": "https://example.org/china-generation",
            "page_fetch_status": "ok", "evidence_excerpt_origin": "publisher_page",
            "evidenceExcerpt": "China wind and solar generation reached 2,310 TWh in 2025.",
            "_citedClaimGroups": [group],
        },
    ]
    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({
                "status": "PASS", "summary": "The cited table supports both shares.",
                "findings": [], "checks": {}, "blocking": False,
            })}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, _url, **kwargs):
            captured["payload"] = json.loads(kwargs["json"]["messages"][1]["content"])
            return FakeResponse()

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Renewable electricity shares", question="Compare China and Brazil",
        report_text="China's renewable share rose from 23.97% in 2015 to 33.70% in 2024 [S1, S15].",
        report_tier="detailed", charts=[], images=[], sources=sources, _force_groq_fallback=True,
    )

    payload = captured["payload"]
    assert result["status"] == "PASS"
    assert payload["report"]["sources"]["S1"]["t"] == "Share of electricity from renewables (%)"
    assert "2015 | 74.21 | 23.97" in payload["report"]["sources"]["S1"]["e"]
    assert payload["report"]["numericClaimEvidence"] == [[
        "S1,S15", "China's renewable share rose from", "percent:23.97;percent:33.7",
    ]]


@pytest.mark.asyncio
async def test_groq_json_validation_error_retries_without_constrained_output(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["gemini-audit-key"])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-audit-key"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["gemini-audit-model"])
    monkeypatch.setenv("GG_GROQ_AUDIT_MODEL", "test-json-compatible-model")
    monkeypatch.setattr(report_mod, "_is_rest_api_key", lambda _key: True)
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda _identifier: False)
    monkeypatch.setattr(report_mod, "iter_reserved_groq_keys", lambda keys, max_keys=1: iter(keys))
    groq_attempts = 0

    class FakeResponse:
        def __init__(self, status, payload=None):
            self.status_code = status
            self.is_success = 200 <= status < 300
            self.headers = {}
            self.payload = payload or {}

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            nonlocal groq_attempts
            if "generativelanguage.googleapis.com" in url:
                return FakeResponse(503)
            groq_attempts += 1
            if groq_attempts == 1:
                assert kwargs["json"].get("response_format") == {"type": "json_object"}
                return FakeResponse(400, {"error": {"code": "json_validate_failed"}})
            assert "response_format" not in kwargs["json"]
            return FakeResponse(200, {"choices": [{"message": {
                "content": '{"status":"PASS","summary":"Reviewed","findings":[],"blocking":false}'
            }}]})

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="A concise report [S1].",
        report_tier="detailed", charts=[], images=[], sources=[{"id": "S1", "title": "Evidence"}],
    )

    assert result["status"] == "PASS"
    assert result["provider"] == "Groq API"
    assert groq_attempts == 2


@pytest.mark.asyncio
async def test_groq_exhausted_reasoning_budget_is_reported_as_truncated(monkeypatch):
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: [])
    monkeypatch.setattr(report_mod, "get_groq_keys", lambda: ["groq-audit-key"])
    monkeypatch.setenv("GG_GROQ_AUDIT_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setattr(report_mod, "iter_reserved_groq_keys", lambda keys, max_keys=1: iter(keys))
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda _identifier: False)

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {}
        def json(self):
            return {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": "internal reasoning"}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="A short report.",
        report_tier="detailed", charts=[], images=[], sources=[], _force_groq_fallback=True,
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "response_truncated"


@pytest.mark.asyncio
async def test_gemini_audit_retries_rate_limit_and_falls_back(monkeypatch):
    cooled = set()
    monkeypatch.setattr(report_mod, "get_gemini_keys", lambda: ["test-key-a", "test-key-b"])
    monkeypatch.setattr(report_mod, "GEMINI_MODELS", ["model-a", "model-b"])
    monkeypatch.setattr(report_mod, "mark_rate_limited", lambda key, _cooldown_ms: cooled.add(key))
    monkeypatch.setattr(report_mod, "is_rate_limited", lambda key: key in cooled)
    calls = []
    class FakeResponse:
        def __init__(self, status): self.status_code = status; self.is_success = status == 200
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({"status":"PASS","summary":"No critical defects","blocking":False,"findings":[],"checks":{}})}]}}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            calls.append((url, kwargs["headers"]["x-goog-api-key"]))
            return FakeResponse(429 if len(calls) == 1 else 200)
    monkeypatch.setattr(report_mod.httpx, "AsyncClient", FakeClient)
    result = await report_mod._gemini_independent_report_audit(
        title="Test", question="Test report", report_text="# Test", report_tier="detailed",
        charts=[], images=[], sources=[])
    assert result["status"] == "PASS"
    assert len(calls) == 2
    assert calls[0][1] != calls[1][1]


@pytest.mark.asyncio
async def test_audit_wrapper_runs_groq_after_gemini_deadline(monkeypatch):
    calls = []

    async def fake_audit(**kwargs):
        force_groq = bool(kwargs.get("_force_groq_fallback"))
        calls.append(force_groq)
        if not force_groq:
            import asyncio
            await asyncio.sleep(0.05)
            return {"status": "unavailable", "reason": "provider_unavailable", "blocking": False, "findings": []}
        return {"status": "PASS", "summary": "Groq completed the independent review", "blocking": False,
                "findings": [], "provider": "Groq API", "fallbackFrom": "Gemini API"}

    monkeypatch.setattr(report_mod, "_gemini_independent_report_audit", fake_audit)
    audit, _deterministic = await report_mod._run_independent_report_audit(
        title="Test", question="Test report", report_text="A short report.", report_tier="detailed",
        charts=[], images=[], sources=[], timeout_seconds=0.001,
    )

    assert calls == [False, True]
    assert audit["status"] == "PASS"
    assert audit["provider"] == "Groq API"


def test_independent_audit_includes_late_cited_sources_and_separates_evidence_provenance():
    sources = [{"id": f"S{index}", "title": f"Uncited source {index}", "snippet": "not cited"}
               for index in range(1, 60)]
    sources[0].update({"title": "Uncited early record"})
    sources[58].update({
        "title": "Uploaded data.xlsx",
        "source_type": "Uploaded workbook",
        "evidenceExcerpt": "Workbook value 91.2",
        "chartEvidenceExcerpt": "Metric | Value\nCapacity | 91.2",
        "_citedClaimGroups": [{
            "groupId": "claim-1", "sourceIds": ["S59"], "claim": "Capacity was 91.2",
            "claimTokens": ["91.2"], "supportByToken": {"91.2": ["S59"]},
            "unsupportedTokens": [], "supportingSourceIds": ["S59"], "groupEvidenceMatched": True,
        }],
    })
    sources[1].update({
        "id": "S2", "title": "Fetched table", "url": "https://example.org/table",
        "page_fetch_status": "ok", "evidence_excerpt_origin": "publisher_page",
        "publisher_table_chart_evidence_verified": True,
        "evidenceExcerpt": "Short prose excerpt",
        "chartEvidenceExcerpt": "Year | Capacity\n2024 | 91.2",
    })
    sources[2].update({
        "id": "S3", "title": "Search-only data", "url": "https://example.org/search",
        "page_fetch_status": "http_403", "evidence_excerpt_origin": "search_extract_only",
        "search_extract_chart_evidence_verified": True,
        "chartEvidenceExcerpt": "Search value | 999",
        "evidenceExcerpt": "Search result says 999",
    })
    sources[3].update({
        "id": "S4", "title": "Mixed publisher and search text", "url": "https://example.org/mixed",
        "page_fetch_status": "ok", "evidence_excerpt_origin": "publisher_page_and_search_extract",
        "evidenceExcerpt": "Mixed search excerpt mentioning 777",
    })

    source_meta, coverage = report_mod._independent_audit_source_metadata(
        "The workbook reports 91.2 [S59]. See chart [S2].", [], sources,
    )

    assert [row["id"] for row in source_meta] == ["S59", "S2"]
    assert coverage["allReferencedIdsIncluded"] is True
    assert coverage["uncitedSourceRecordsOmitted"] == 57
    workbook = source_meta[0]
    assert workbook["evidenceBasis"] == "uploaded_file"
    assert workbook["uploadedFileEvidenceExcerpt"] == "Metric | Value\nCapacity | 91.2"
    assert workbook["citationClaimGroups"][0]["supportByToken"] == {"91.2": ["S59"]}
    fetched_table = source_meta[1]
    assert fetched_table["verifiedStructuredDataKind"] == "verified_publisher_table_rows"
    assert fetched_table["verifiedStructuredDataExcerpt"] == "Year | Capacity\n2024 | 91.2"

    search_only, _coverage = report_mod._independent_audit_source_metadata(
        "Search data [S3].", [], sources,
    )
    assert search_only[0]["evidenceBasis"] == "search_excerpt_only"
    assert search_only[0]["verifiedStructuredDataExcerpt"] == ""
    assert search_only[0]["searchOnlyChartExcerpt"] == "Search value | 999"
    mixed, _coverage = report_mod._independent_audit_source_metadata(
        "Mixed evidence [S4].", [], sources,
    )
    assert mixed[0]["evidenceBasis"] == "search_excerpt_only"
    assert mixed[0]["verifiedPublisherEvidenceExcerpt"] == ""


def test_independent_audit_includes_chart_source_ids_without_inline_report_citations():
    sources = [{"id": "S7", "title": "Chart evidence", "snippet": "series values"}]
    source_meta, coverage = report_mod._independent_audit_source_metadata(
        "A short report with no inline source citation.",
        [{"title": "Generated from source", "sourceIds": ["S7"]}],
        sources,
    )

    assert [row["id"] for row in source_meta] == ["S7"]
    assert coverage["citedIds"] == ["S7"]
    assert coverage["selectionBasis"] == "report_and_chart_citations"


def test_audit_excerpt_selects_cited_value_rows_even_when_token_map_has_no_match():
    table = "Year | Capacity (GW)\n" + "\n".join(
        f"{year} | {year % 9 + 1}" for year in range(1900, 2024)
    ) + "\n2024 | 94"
    groups = [{"sourceIds": ["S1"], "claimTokens": ["capacity_gw:94"], "supportByToken": {}}]

    excerpt = report_mod._audit_excerpt_for_claims(table, "S1", groups, 300)

    assert excerpt == "Year | Capacity (GW)\n2024 | 94"


def test_independent_audit_keeps_all_numeric_claim_groups_for_a_source():
    groups = [{
        "groupId": f"claim-{index}", "sourceIds": ["S1"],
        "claim": f"The measured value was {index} units.",
        "claimTokens": [f"scalar:{index}"], "supportByToken": {},
    } for index in range(1, 18)]
    source_meta, _coverage = report_mod._independent_audit_source_metadata(
        "Measured values are reported here [S1].",
        [],
        [{"id": "S1", "title": "Publisher source", "snippet": "Observed data", "_citedClaimGroups": groups}],
    )

    assert len(source_meta[0]["citationClaimGroups"]) == 17


def test_audit_excerpt_deduplicates_rows_and_never_slices_a_matching_row():
    table = "Year | Value\n2024 | 94\n2024 | 94\n" + "x" * 80
    groups = [{"sourceIds": ["S1"], "claimTokens": ["scalar:94"], "supportByToken": {}}]

    excerpt = report_mod._audit_excerpt_for_claims(table, "S1", groups, 20)

    assert excerpt == "Year | Value"


def test_audit_excerpt_keeps_claim_values_from_long_single_line_source_text():
    source_text = (
        "Solar output in 2025 (1,175 TWh) was more than four times as large as five years ago. "
        "China wind and solar generation in 2025 reached 2,310 TWh. "
        "China fossil generation declined by 56 TWh (-0.9%) in 2025."
    )
    groups = [{
        "sourceIds": ["S15"],
        "claimTokens": ["energy_twh:1175", "energy_twh:2310", "energy_twh:56"],
    }]

    excerpt = report_mod._audit_excerpt_for_claims(source_text, "S15", groups, 220)

    assert len(excerpt) <= 220
    assert "1,175 TWh" in excerpt
    assert "2,310 TWh" in excerpt
    assert "56 TWh" in excerpt


def test_audit_excerpt_reads_decimal_growth_multipliers():
    source_text = "TOP countries in 2024: India 135.37 TWh (6.60%; 23.29x growth since 2014)."
    groups = [{"sourceIds": ["S31"], "claimTokens": ["energy_twh:135.37", "scalar:23.29"]}]

    excerpt = report_mod._audit_excerpt_for_claims(source_text, "S31", groups, 220)

    assert "India 135.37 TWh" in excerpt
    assert "23.29x growth" in excerpt
