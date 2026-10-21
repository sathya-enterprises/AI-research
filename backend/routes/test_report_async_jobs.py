import pytest
from pathlib import Path


def test_report_job_worker_contract_is_present():
    source = Path(__file__).with_name("report.py").read_text(encoding="utf-8")
    assert "async def _run_report_job_async(job_id: str, body: dict) -> None:" in source
    assert "ThreadPoolExecutor" in source
    assert "_submit_report_job(job_id, body)" in source
    assert "_REPORT_JOBS[job_id]" in source


@pytest.mark.asyncio
async def test_report_job_worker_runs_generate_report_off_main_thread(monkeypatch):
    import threading
    from fastapi.responses import JSONResponse
    import routes.report as report_mod

    main_thread = threading.get_ident()
    seen = {}

    async def fake_generate(request):
        seen["thread"] = threading.get_ident()
        return JSONResponse({"report": "x"}, status_code=200)

    monkeypatch.setattr(report_mod, "generate_report", fake_generate)
    job_id = "thread-test"
    report_mod._REPORT_JOBS[job_id] = {"status": "queued", "createdAt": 0, "reportTier": "detailed"}
    await report_mod._run_report_job(job_id, {})
    assert seen["thread"] != main_thread
    report_mod._REPORT_JOBS.pop(job_id, None)


@pytest.mark.asyncio
async def test_report_job_status_mirrors_completed_result():
    import routes.report as report_mod
    from fastapi.responses import JSONResponse

    job_id = "status-test"
    report_mod._REPORT_JOBS[job_id] = {
        "status": "done",
        "createdAt": __import__("time").time(),
        "finishedAt": __import__("time").time(),
        "reportTier": "detailed",
        "result": {"report": "finished report", "summary": "summary"},
    }
    response = await report_mod.report_job_status(job_id)
    assert isinstance(response, JSONResponse)
    import json
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["status"] == "done"
    assert payload["result"]["report"] == "finished report"
    assert payload["report"] == "finished report"
    report_mod._REPORT_JOBS.pop(job_id, None)
