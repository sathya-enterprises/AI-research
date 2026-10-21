"""When a Groq-compatible model rejects JSON mode, retry once without the hint."""
import asyncio
import json
from types import SimpleNamespace

from utils import intent


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeAsyncClient:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, *, headers, json):
        self.__class__.requests.append({"url": url, "headers": headers, "json": json})
        return self.__class__.responses.pop(0)


def test_intent_resolver_retries_groq_without_json_mode_after_validation_400(monkeypatch):
    FakeAsyncClient.requests = []
    FakeAsyncClient.responses = [
        FakeResponse(400, {"error": {"message": "Failed to validate JSON. Please adjust your prompt."}}),
        FakeResponse(200, {"choices": [{"message": {"content": json.dumps({
            "resolved_topic": "Electronic City software companies",
            "is_followup": False,
            "intent_label": "source discovery",
            "evidence_needed": ["general_knowledge"],
            "wants_report": False,
            "notes": "",
        })}}]}),
    ]
    monkeypatch.setattr(intent, "get_groq_keys", lambda: ["gsk_test_key_not_real"])
    monkeypatch.setattr(intent, "is_rate_limited", lambda _key: False)
    monkeypatch.setattr(intent, "round_robin", lambda pool: list(pool))
    monkeypatch.setattr(intent.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(intent.resolve_request_intent("Search about software companies in Electronic City"))

    assert result.source == "llm"
    assert result.resolved_topic == "Electronic City software companies"
    assert len(FakeAsyncClient.requests) == 2
    assert FakeAsyncClient.requests[0]["json"].get("response_format") == {"type": "json_object"}
    assert "response_format" not in FakeAsyncClient.requests[1]["json"]
