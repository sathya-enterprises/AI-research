"""Phase 0: only the detailed and in_depth report tiers exist, and PDF is the only output."""
import asyncio
import json

import pytest

import routes.report as R
from utils.report_tiers import (
    REMOVED_BRIEF_TIER_ERROR,
    REPORT_TIER_SPECS,
    VALID_REPORT_TIERS,
    get_report_tier_spec,
    report_tier_error,
    report_tier_for_page_count,
)


def _post(body):
    from starlette.requests import Request
    raw = json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)


def test_only_detailed_and_in_depth_tiers_exist():
    assert VALID_REPORT_TIERS == ("detailed", "in_depth")
    assert set(REPORT_TIER_SPECS) == {"detailed", "in_depth"}
    # Unknown values still resolve to the detailed spec for internal callers.
    assert get_report_tier_spec("brief").tier.value == "detailed"


@pytest.mark.parametrize("tier,expected", [
    ("detailed", None),
    ("in_depth", None),
    ("brief", REMOVED_BRIEF_TIER_ERROR),
    ("Brief", REMOVED_BRIEF_TIER_ERROR),
    ("", 'Invalid report tier. Use "detailed" or "in_depth".'),
    ("standard", 'Invalid report tier. Use "detailed" or "in_depth".'),
])
def test_report_tier_error_messages(tier, expected):
    assert report_tier_error(tier) == expected


@pytest.mark.parametrize("pages,tier", [(6, "detailed"), (10, "detailed"), (30, "detailed"), (69, "detailed"), (70, "in_depth"), (100, "in_depth")])
def test_explicit_page_requests_map_to_remaining_tiers(pages, tier):
    assert report_tier_for_page_count(pages) == tier


def test_start_rejects_brief_with_400(monkeypatch):
    submitted = []
    monkeypatch.setattr(R, "_submit_report_job", lambda job_id, body: submitted.append(job_id))
    monkeypatch.setattr(R, "_REPORT_JOBS", {})
    response = asyncio.run(R.start_report_job(_post({"reportTier": "brief", "question": "Indian banking sector"})))
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == '"brief" was removed; use "detailed" or "in_depth"'
    assert submitted == []


def test_start_maps_short_explicit_page_request_to_detailed(monkeypatch):
    submitted = []
    monkeypatch.setattr(R, "_submit_report_job", lambda job_id, body: submitted.append((job_id, body)))
    monkeypatch.setattr(R, "_REPORT_JOBS", {})
    response = asyncio.run(R.start_report_job(_post({"reportTier": "brief", "question": "Write an 8-page report on Indian banks"})))
    assert response.status_code == 202
    assert submitted and submitted[0][1]["reportTier"] == "detailed"
    assert submitted[0][1]["requestedPageCount"] == 8


def test_synchronous_report_endpoint_rejects_brief_with_400():
    response = asyncio.run(R.generate_report(_post({"reportTier": "brief", "question": "Indian banking sector"})))
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == REMOVED_BRIEF_TIER_ERROR


def test_only_pdf_report_output_routes_are_registered():
    import main
    paths = {getattr(route, "path", "") for route in main.app.routes}
    assert "/api/chat/report/pdf" in paths
    assert "/api/chat/report/email" in paths
    for removed in ("/api/chat/report/html", "/api/chat/report/docx", "/api/chat/report/okf"):
        assert removed not in paths
    # The HTML builder remains as the internal PDF rendering step.
    from routes.html_report import build_html_report  # noqa: F401
    import routes.html_report as html_report
    assert not hasattr(html_report, "router")
