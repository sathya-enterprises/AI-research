"""Regression tests for progressive long-report delivery and progress bookkeeping."""
import asyncio
import json
import time

import routes.report as report


def test_report_progress_is_monotonic_and_preview_is_bounded():
    job_id = "progress-v13-test"
    report._REPORT_JOBS[job_id] = {
        "status": "running",
        "reportTier": "detailed",
        "startedAt": time.time() - 0.2,
        "progressPercent": 10,
    }
    token = report._CURRENT_REPORT_JOB_ID.set(job_id)
    try:
        source_text = "x" * (report._REPORT_PREVIEW_MAX_CHARS + 100)
        report._report_progress("writing", "Draft prepared", 72, preview=source_text)
        report._report_progress("checking", "Checking report", 65)
        job = report._REPORT_JOBS[job_id]
        assert job["progressPercent"] == 72
        assert len(job["draftPreview"]) == report._REPORT_PREVIEW_MAX_CHARS
        assert job["draftPreviewTruncated"] is True
        assert job["draftPreviewTotalChars"] == len(source_text)
        assert job["elapsedMs"] >= 0
    finally:
        report._CURRENT_REPORT_JOB_ID.reset(token)
        report._REPORT_JOBS.pop(job_id, None)


def test_report_stream_emits_status_preview_result_without_buffering_full_draft():
    job_id = "stream-v13-test"
    preview = "# Provisional report\n" + ("Evidence and context. " * 700)
    report._REPORT_JOBS[job_id] = {
        "status": "done",
        "createdAt": time.time(),
        "startedAt": time.time() - 1,
        "finishedAt": time.time(),
        "reportTier": "detailed",
        "phase": "complete",
        "progressMessage": "Report is ready.",
        "progressPercent": 100,
        "elapsedMs": 1000,
        "draftPreview": preview,
        "draftPreviewTruncated": False,
        "draftPreviewTotalChars": len(preview),
        "result": {"report": "Final validated report", "title": "Test"},
    }

    async def run():
        response = await report.report_job_stream(job_id)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
        payload = ""
        async for chunk in response.body_iterator:
            payload += chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        assert '"type": "data-report-status"' in payload
        assert '"type": "data-report-preview"' in payload
        assert '"type": "data-report-result"' in payload
        assert "[DONE]" in payload
        preview_events = [
            json.loads(frame.split("data: ", 1)[1])
            for frame in payload.split("\n\n")
            if frame.startswith("data: ") and '"type": "data-report-preview"' in frame
        ]
        assert len(preview_events) >= 2
        assert "delta" in preview_events[0]["data"]
        assert preview_events[-1]["data"]["sentChars"] == len(preview)

    try:
        asyncio.run(run())
    finally:
        report._REPORT_JOBS.pop(job_id, None)
