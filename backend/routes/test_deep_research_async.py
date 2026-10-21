"""Regression tests for the long-running Deep Research job transport."""
import asyncio
import json
import pytest

from fastapi.responses import JSONResponse

import routes.report as report


class _Request:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def test_only_validated_gemini_models_are_enabled():
    assert report.GEMINI_MODELS == [
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
    ]


@pytest.mark.parametrize("tier", ["brief", "detailed", "in_depth"])
def test_each_report_tier_starts_and_can_be_polled(monkeypatch, tier):
    async def fake_generate(request):
        await asyncio.sleep(0.001)
        return JSONResponse({"report": "completed", "title": "Deep Test"})

    async def run():
        monkeypatch.setattr(report, "generate_report", fake_generate)
        report._REPORT_JOBS.clear()
        started = await report.start_report_job(_Request({"reportTier": tier, "question": "test"}))
        assert started.status_code == 202
        start_payload = json.loads(started.body)
        assert start_payload["reportTier"] == tier
        job_id = start_payload["jobId"]
        for _ in range(20):
            await asyncio.sleep(0.005)
            status = await report.report_job_status(job_id)
            payload = json.loads(status.body)
            if payload["status"] == "done":
                assert payload["result"]["report"] == "completed"
                return
        raise AssertionError(f"{tier} report job did not complete")

    asyncio.run(run())
