"""Safe diagnostics for failed RAG persistence requests."""
import httpx

from utils.rag_client import _safe_index_http_error, filter_rag_report_evidence_to_attachments


def test_index_error_explains_missing_server_key_without_echoing_service_body():
    response = httpx.Response(500, json={
        "detail": "RAG pgvector storage is unavailable: pgvector needs a server-only "
                 "SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY in backend/rag-service/.env."
    })

    message = _safe_index_http_error(response)

    assert message == "RAG pgvector persistence is not configured with a server-only Supabase key."


def test_index_error_does_not_echo_unknown_database_response_details_or_credentials():
    response = httpx.Response(500, json={
        "detail": "https://private-project.supabase.co/rest/v1/rag_chunks?apikey=not-for-logs"
    })

    message = _safe_index_http_error(response)

    assert message == "RAG service returned HTTP 500. Check RAG service health diagnostics."
    assert "private-project" not in message
    assert "not-for-logs" not in message


def test_attachment_report_filters_a_mixed_stale_rag_session_to_the_current_file():
    rows = [
        {"sourceName": r"C:\\old\\PMSPRFFEB2026.xlsx", "documentId": "current", "text": "current rows"},
        {"sourceName": "SHANKESH_Shankesh_Jewellers_Ltd.xlsx", "documentId": "old-1", "text": "old rows"},
        {"sourceName": "SUNSHINE_Sunshine_Pictures_Ltd.xlsx", "documentId": "old-2", "text": "old rows"},
    ]

    kept_rows, kept_files, meta = filter_rag_report_evidence_to_attachments(
        rows,
        ["PMSPRFFEB2026.xlsx", "SHANKESH_Shankesh_Jewellers_Ltd.xlsx", "SUNSHINE_Sunshine_Pictures_Ltd.xlsx"],
        {"PMSPRFFEB2026.xlsx"},
    )

    assert [row["documentId"] for row in kept_rows] == ["current"]
    assert kept_files == ["PMSPRFFEB2026.xlsx"]
    assert meta == {"requestedFiles": 1, "matchedFiles": 1, "discardedRows": 2, "discardedFiles": 2}


def test_attachment_report_uses_exact_basenames_and_rejects_similarly_named_files():
    rows = [
        {"sourceName": "PMSPRFFEB2026-copy.xlsx", "text": "not this upload"},
        {"sourceName": "PMSPRFFEB2026.xlsx", "text": "this upload"},
        {"metadata": {"filename": "PMSPRFFEB2026.xlsx"}, "text": "metadata filename"},
    ]

    kept_rows, kept_files, meta = filter_rag_report_evidence_to_attachments(
        rows, [], [r"C:\\Users\\Joan\\Downloads\\PMSPRFFEB2026.xlsx"],
    )

    assert [row["text"] for row in kept_rows] == ["this upload", "metadata filename"]
    assert kept_files == ["PMSPRFFEB2026.xlsx"]
    assert meta["discardedRows"] == 1


def test_attachment_filename_matching_preserves_punctuation_that_distinguishes_files():
    rows = [
        {"sourceName": "ab.xlsx", "text": "different file"},
        {"sourceName": "a-b.xlsx", "text": "the attachment"},
    ]

    kept_rows, _, _ = filter_rag_report_evidence_to_attachments(rows, [], ["a-b.xlsx"])

    assert [row["text"] for row in kept_rows] == ["the attachment"]


def test_attachment_report_rejects_rag_evidence_when_current_filename_is_unknown():
    rows = [{"sourceName": "old.xlsx", "text": "stale"}]

    kept_rows, kept_files, meta = filter_rag_report_evidence_to_attachments(rows, ["old.xlsx"], [])

    assert kept_rows == []
    assert kept_files == []
    assert meta["requestedFiles"] == 0
    assert meta["discardedRows"] == 1
