"""Regression test: API keys in query-string URLs must never hit logs unredacted."""
import ast
import logging
import re
from pathlib import Path


def test_redaction_pattern_covers_serpapi_api_key_query_parameter():
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    pattern_node = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_KEY_PARAM_RE" for target in node.targets)
    )
    pattern_text = ast.literal_eval(pattern_node.value.args[0])
    pattern = re.compile(pattern_text, re.IGNORECASE)
    request = 'https://serpapi.com/search.json?engine=google&api_key=SECRET_VALUE&num=18'
    redacted = pattern.sub(r"\1***REDACTED***", request)
    assert "SECRET_VALUE" not in redacted
    assert "api_key=***REDACTED***" in redacted
    gemini_request = 'https://example.org/generate?x-goog-api-key=GEMINI_SECRET&access_token=ACCESS_SECRET'
    gemini_redacted = pattern.sub(r"\1***REDACTED***", gemini_request)
    assert "GEMINI_SECRET" not in gemini_redacted
    assert "ACCESS_SECRET" not in gemini_redacted


def test_backend_log_filter_redacts_configured_secret_and_authorization(monkeypatch):
    import main

    secret = "synthetic-test-provider-secret-123"
    monkeypatch.setenv("TEST_PROVIDER_API_KEY", secret)
    log_filter = main._RedactApiKeysFilter()
    record = logging.LogRecord(
        "provider", logging.ERROR, __file__, 1,
        "request failed api_key=%s Authorization: Bearer %s",
        (secret, secret), None,
    )

    assert log_filter.filter(record)
    rendered = record.getMessage()
    assert secret not in rendered
    assert rendered.count("***REDACTED***") >= 2

    url_record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        "request URL %s", ("https://example.org/generate?x-goog-api-key=URL_SECRET",), None,
    )
    assert log_filter.filter(url_record)
    assert "URL_SECRET" not in url_record.getMessage()


def test_provider_calls_never_put_gemini_keys_in_request_urls():
    backend = Path(__file__).resolve().parents[1]
    unsafe_url = re.compile(
        r"generativelanguage\.googleapis\.com[^\n\"']*\?[^\n\"']*\bkey=\{[^}]+\}",
        re.IGNORECASE,
    )
    offenders = []
    for path in backend.rglob("*.py"):
        if path.name.startswith("test_") or "__pycache__" in path.parts:
            continue
        if unsafe_url.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(backend)))
    assert offenders == []


def test_backend_does_not_log_api_key_fragments():
    backend = Path(__file__).resolve().parents[1]
    exposed_fragment = re.compile(r"\b(?:key|api[_-]?key|[a-z]+_key)\s*\[\s*-\s*4\s*:\s*\]", re.IGNORECASE)
    offenders = []
    for path in backend.rglob("*.py"):
        if path.name.startswith("test_") or "__pycache__" in path.parts:
            continue
        if exposed_fragment.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(backend)))
    assert offenders == []
