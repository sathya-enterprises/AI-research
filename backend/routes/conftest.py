"""Keep request-local report evidence isolated across direct endpoint tests."""
from __future__ import annotations

import pytest

import routes.report as report


@pytest.fixture(autouse=True)
def no_real_browser_fetch(monkeypatch):
    """Endpoint tests mock page fetches; never launch a real browser from them."""
    monkeypatch.setenv("GG_BROWSER_FALLBACK", "0")


@pytest.fixture(autouse=True)
def isolate_report_evidence_contextvars():
    """Restore report-local chart evidence after each test.

    FastAPI requests normally receive their own async context. Unit tests often
    invoke report helpers directly in one shared context, so a test that builds
    an evidence gallery must not affect a later provenance assertion.
    """
    variables_and_defaults = (
        (report._CHART_EVIDENCE_LINES, {}),
        (report._PUBLISHER_TABLE_CHART_EVIDENCE_IDS, set()),
        (report._PUBLISHER_GALLERY_CHART_EVIDENCE_IDS, set()),
        (report._SEARCH_GALLERY_CHART_EVIDENCE_IDS, set()),
        (report._PUBLISHER_GALLERY_CHART_EVIDENCE_LINES, {}),
        (report._SEARCH_GALLERY_CHART_EVIDENCE_LINES, {}),
    )
    tokens = [(variable, variable.set(default)) for variable, default in variables_and_defaults]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
