"""Regression tests for robust, asynchronous and multi-target Action execution."""
import asyncio
import json
from pathlib import Path

import pytest

import routes.report as report_module


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def test_auto_detect_finds_multiple_exact_report_headings_without_internal_target_syntax():
    report_text = """# Renewable energy outlook

## 3. Investment, Costs & Project Economics
Investments [S1].

## 4. Policy, Regulation & Grid Infrastructure
Policy [S2].

## 5. Opportunities and Risks
Risks [S3].
"""
    prompt = (
        "Update the existing report's **Investment, Costs & Project Economics** and "
        "**Policy, Regulation & Grid Infrastructure** sections. Improve costs and grid policy."
    )
    assert report_module._action_extract_targets(prompt) == ([], False)
    assert report_module._action_extract_heading_mentions(prompt, report_text) == [
        "3. Investment, Costs & Project Economics",
        "4. Policy, Regulation & Grid Infrastructure",
    ]


def test_natural_two_heading_action_is_orchestrated_atomically(monkeypatch):
    original = """# Renewable energy outlook

## 3. Investment, Costs & Project Economics
Investment analysis [S1].

## 4. Policy, Regulation & Grid Infrastructure
Policy analysis [S2].

## 5. Opportunities and Risks
This section must remain byte-for-byte unchanged [S3].
"""
    calls = []

    async def fake_single(body):
        instruction = body["editInstruction"]
        first_line = instruction.splitlines()[0]
        target = "Investment, Costs & Project Economics" if first_line.endswith("Investment, Costs & Project Economics\"") else "Policy, Regulation & Grid Infrastructure"
        calls.append(target)
        old = body["report"]
        if target.startswith("Investment"):
            new = old.replace("Investment analysis [S1].", "Investment analysis updated with a verified cost comparison [S1].")
        else:
            new = old.replace("Policy analysis [S2].", "Policy analysis updated with a grid implementation matrix [S2].")
        return report_module.JSONResponse({
            "report": new,
            "charts": body.get("charts", []),
            "images": body.get("images", []),
            "sources": body.get("sources", []),
            "presentation": body.get("presentation"),
            "editedSection": target,
            "chartUpdated": False,
        })

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)
    request = FakeRequest({
        "report": original,
        "title": "Renewable energy outlook",
        "editInstruction": (
            "Update the existing report's **Investment, Costs & Project Economics** and "
            "**Policy, Regulation & Grid Infrastructure** sections. Improve costs and grid policy."
        ),
        "charts": [], "images": [], "sources": [], "presentation": None,
    })
    response = asyncio.run(report_module.edit_report_section(request))
    assert response.status_code == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert len(calls) == 2
    assert "Investment analysis updated" in payload["report"]
    assert "Policy analysis updated" in payload["report"]
    assert "This section must remain byte-for-byte unchanged [S3]." in payload["report"]
    assert payload["multiSectionEdit"] is True


def test_action_start_returns_quickly_and_queues_job(monkeypatch):
    submitted = []
    monkeypatch.setattr(report_module, "_submit_action_job", lambda job_id, body: submitted.append((job_id, body)))
    response = asyncio.run(report_module.start_action_edit_job(FakeRequest({
        "report": "## Target\nExisting content [S1].",
        "editInstruction": "Update Target.",
    })))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 202
    assert payload["status"] == "queued"
    assert payload["jobId"]
    assert len(submitted) == 1
    assert submitted[0][0] == payload["jobId"]
    report_module._ACTION_JOBS.pop(payload["jobId"], None)


def test_action_start_rejects_duplicate_running_job_with_clear_message(monkeypatch):
    report_module._ACTION_JOBS.clear()
    report_module._ACTION_JOBS["existing"] = {"status": "running", "createdAt": 0}
    try:
        response = asyncio.run(report_module.start_action_edit_job(FakeRequest({
            "report": "## Target\nExisting content [S1].",
            "editInstruction": "Update Target.",
        })))
        payload = json.loads(response.body.decode("utf-8"))
        assert response.status_code == 429
        assert "still running" in payload["error"].lower()
        assert payload["preserved"] is True
    finally:
        report_module._ACTION_JOBS.clear()


def test_action_job_status_returns_result_only_after_complete_commit():
    report_module._ACTION_JOBS["test-done"] = {
        "status": "done",
        "result": {"report": "## Target\nUpdated [S1].", "editedSection": "Target"},
    }
    try:
        response = asyncio.run(report_module.action_edit_job_status("test-done"))
        payload = json.loads(response.body.decode("utf-8"))
        assert response.status_code == 200
        assert payload["status"] == "done"
        assert payload["result"]["report"].endswith("Updated [S1].")
        assert payload["editedSection"] == "Target"
    finally:
        report_module._ACTION_JOBS.pop("test-done", None)


def test_action_provider_pool_does_not_busy_spin_when_all_keys_are_rate_limited(monkeypatch):
    monkeypatch.setattr(report_module, "get_groq_keys", lambda: [])
    monkeypatch.setattr(report_module, "get_gemini_keys", lambda: ["test-key"])
    monkeypatch.setattr(report_module, "GEMINI_MODELS", ["test-model"])
    monkeypatch.setattr(report_module, "_is_rest_api_key", lambda key: True)
    monkeypatch.setattr(report_module, "is_rate_limited", lambda value: True)
    result = asyncio.run(asyncio.wait_for(report_module._call_llm_plain("system", "user"), timeout=0.5))
    assert result == ""


def test_frontend_uses_background_action_jobs_and_status_polling():
    root = Path(__file__).parents[1] / "frontend"
    component = (root / "src/components/GrowthGradualChat.tsx").read_text(encoding="utf-8")
    start_route = root / "src/app/api/chat/report/edit/start/route.ts"
    status_route = root / "src/app/api/chat/report/edit/status/[job_id]/route.ts"
    assert "'/api/chat/report/edit/start'" in component
    assert "`/api/chat/report/edit/status/" in component
    assert "20 * 60_000" in component
    assert start_route.exists() and status_route.exists()
    assert "/api/chat/report/edit/start" in start_route.read_text(encoding="utf-8")
    assert "/api/chat/report/edit/status/" in status_route.read_text(encoding="utf-8")


def test_synchronous_compatibility_route_converts_unexpected_exception_to_preserved_error(monkeypatch):
    async def explode(_request):
        raise RuntimeError("provider adapter exception")

    monkeypatch.setattr(report_module, "_edit_report_section_impl", explode)
    response = asyncio.run(report_module.edit_report_section(FakeRequest({"report": "unchanged"})))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 500
    assert payload["preserved"] is True
    assert "not committed" in payload["error"].lower()


def test_multi_section_failure_preserves_provider_error_and_commits_no_partial_edit(monkeypatch):
    original = "## Alpha\nAlpha [S1].\n\n## Beta\nBeta [S2].\n"
    calls = 0

    async def fake_single(body):
        nonlocal calls
        calls += 1
        if calls == 1:
            return report_module.JSONResponse({
                "report": body["report"].replace("Alpha [S1].", "Alpha updated [S1]."),
                "charts": [], "images": [], "sources": [], "presentation": None,
                "editedSection": "Alpha",
            })
        return report_module.JSONResponse({
            "error": "The edit provider pool did not return a usable rewrite after retrying available models.",
            "details": ["No provider returned a non-empty section rewrite."],
            "editedSection": "Beta", "preserved": True,
        }, status_code=503)

    monkeypatch.setattr(report_module, "_edit_report_section_single_body", fake_single)
    response = asyncio.run(report_module.edit_report_section(FakeRequest({
        "report": original,
        "editInstruction": 'Target section: "Alpha"\nTarget section: "Beta"\nInstruction: update both sections.',
        "charts": [], "images": [], "sources": [], "presentation": None,
    })))
    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 503
    assert "provider pool did not return" in payload["error"].lower()
    assert "no multi-section changes were committed" in payload["error"].lower()
    assert payload["preserved"] is True
    assert payload["failedSection"] == "Beta"
