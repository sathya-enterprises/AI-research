"""Safe diagnostics for failed RAG persistence requests."""
import httpx

from utils.rag_client import _safe_index_http_error


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
