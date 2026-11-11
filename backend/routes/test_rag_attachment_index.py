"""Regression coverage for attachment extraction before vector indexing."""
import base64
import json
from io import BytesIO

import pytest
from starlette.requests import Request

import routes.rag as rag_route


def _request(body: dict) -> Request:
    raw = json.dumps(body).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/index", "headers": []}, receive)


def _index_result(documents):
    return {
        "chunks_added": len(documents),
        "total_chunks": len(documents),
        "vector_store": "supabase_pgvector",
        "vector_persistence": {
            "store": "supabase_pgvector", "status": "persisted",
            "documentsAttempted": len(documents), "documentsAvailable": len(documents),
        },
        "diagnostics": {
            "documents": [{"name": item["name"], "status": "indexed"} for item in documents],
        },
    }


@pytest.mark.asyncio
async def test_docx_is_extracted_and_confirmed_in_pgvector(monkeypatch):
    from docx import Document

    content = BytesIO()
    document = Document()
    document.add_paragraph("Quarterly sales report with important data.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Region"
    table.cell(0, 1).text = "Revenue"
    table.cell(1, 0).text = "North"
    table.cell(1, 1).text = "125"
    document.save(content)
    indexed = {}

    async def fake_index(session_id, documents):
        indexed["session_id"] = session_id
        indexed["documents"] = documents
        return _index_result(documents)

    monkeypatch.setattr(rag_route, "rag_index", fake_index)
    response = await rag_route.index_documents(_request({
        "session_id": "test-session",
        "binaryFiles": [{
            "name": "sales.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "data": base64.b64encode(content.getvalue()).decode("ascii"),
        }],
    }))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["index_status"] == "indexed"
    assert payload["vector_persistence"]["store"] == "supabase_pgvector"
    assert "Quarterly sales report" in indexed["documents"][0]["text"]
    assert "North | 125" in indexed["documents"][0]["text"]
    assert payload["extracted_documents"][0]["name"] == "sales.docx"


@pytest.mark.asyncio
async def test_image_ocr_text_is_indexed_and_returned_for_report_context(monkeypatch):
    import routes.report as report_route

    async def fake_ocr(question, images):
        assert len(images) == 1
        return "Revenue by region\nRegion,Revenue\nNorth,125\nSouth,98"

    async def fake_index(session_id, documents):
        return _index_result(documents)

    monkeypatch.setattr(report_route, "extract_data_from_images", fake_ocr)
    monkeypatch.setattr(rag_route, "rag_index", fake_index)
    response = await rag_route.index_documents(_request({
        "session_id": "test-session",
        "images": [{
            "name": "revenue.png", "documentName": "revenue.png",
            "mimeType": "image/png", "data": base64.b64encode(b"image bytes").decode("ascii"),
        }],
    }))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["index_status"] == "indexed"
    assert "North,125" in payload["extracted_documents"][0]["text"]
    assert payload["indexed_documents"][0]["status"] == "indexed"


@pytest.mark.asyncio
async def test_empty_or_unreadable_attachments_do_not_report_success(monkeypatch):
    called = False

    async def fake_index(session_id, documents):
        nonlocal called
        called = True
        return _index_result(documents)

    monkeypatch.setattr(rag_route, "rag_index", fake_index)
    response = await rag_route.index_documents(_request({
        "session_id": "test-session",
        "documents": [{"name": "empty.csv", "text": ""}],
    }))
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload["index_status"] == "no_readable_text"
    assert not called


@pytest.mark.asyncio
async def test_vector_store_failure_is_returned_as_failed_index(monkeypatch):
    async def failed_index(session_id, documents):
        return {"error": "database unavailable", "chunks_added": 0, "vector_store": "supabase_pgvector"}

    monkeypatch.setattr(rag_route, "rag_index", failed_index)
    response = await rag_route.index_documents(_request({
        "session_id": "test-session",
        "documents": [{"name": "source.csv", "text": "header,value\nNorth,125"}],
    }))
    payload = json.loads(response.body)

    assert response.status_code == 502
    assert payload["index_status"] == "failed"
    assert payload["vector_persistence"]["status"] == "no_data"


@pytest.mark.asyncio
async def test_health_reports_degraded_when_rag_is_reachable_but_pgvector_is_not_ready(monkeypatch):
    async def unavailable_vector_store():
        return {
            "reachable": True, "ready": False, "status": "degraded",
            "vector_storage": {"ready": False, "reason": "missing_server_key"},
        }

    monkeypatch.setattr(rag_route, "rag_health_details", unavailable_vector_store)
    response = await rag_route.check_rag_health()
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["reachable"] is True
    assert payload["rag_service"] == "degraded"
