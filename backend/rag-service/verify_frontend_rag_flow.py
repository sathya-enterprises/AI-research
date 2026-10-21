"""Exercise frontend-proxy indexing, private OKF persistence, and RAG query.

Requires the local Next.js proxy (3000), backend (8765), and RAG service
(7860). Uses a unique synthetic document and deletes only the object keys
created in that unique diagnostic session. Output contains status and counts,
never document content or credentials.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
import okf_store  # noqa: E402 - environment must load first
from okf_storage_ops import _walk_objects  # noqa: E402


async def verify() -> dict:
    session_id = f"finbot-rag-diagnostic-{uuid.uuid4().hex}"
    document_id = f"diagnostic-doc-{uuid.uuid4().hex}"
    text = (
        "Diagnostic knowledge record for Growth Gradual private OKF acceptance. "
        "The test record states Aurora test facility output was 17.25 gigawatt-hours "
        "in fiscal year 2025, measured as net output in India. This is synthetic "
        "diagnostic content and must never be represented as a real research fact. "
        "Unique retrieval phrase: indigo kestrel quartz calibration."
    )
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    result = {
        "frontendProxy": False,
        "indexStatus": "not_run",
        "persistenceStatus": "not_run",
        "privateResourceReadable": False,
        "queryProvenanceMatch": False,
        "sourceHashMatch": False,
        "chunkIdPresent": False,
        "rerankerRan": False,
        "diagnosticObjectsRemoved": False,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=90, write=90, pool=10)) as client:
        try:
            response = await client.post(
                "http://127.0.0.1:3000/api/rag/index",
                json={
                    "session_id": session_id,
                    "documents": [{
                        "id": document_id,
                        "name": "Temporary private RAG diagnostic",
                        "text": text,
                        "source_type": "uploaded_document",
                        "file_type": "txt",
                        "metadata": {"diagnostic": True},
                    }],
                },
            )
            result["frontendProxy"] = True
            result["indexStatus"] = f"http_{response.status_code}"
            if response.is_success:
                payload = response.json()
                result["indexStatus"] = "pass" if int(payload.get("chunks_added") or 0) > 0 else "no_chunks_added"
                result["persistenceStatus"] = str((payload.get("persistence") or {}).get("status") or "missing")
            else:
                return result

            query = await client.post(
                "http://127.0.0.1:8765/api/rag/query",
                json={
                    "session_id": session_id,
                    "question": "What output and unique retrieval phrase are in the temporary Aurora diagnostic record?",
                    "top_k": 8,
                    "min_score": 0.0,
                },
            )
            if query.is_success:
                payload = query.json()
                evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
                matching = [item for item in evidence if str(item.get("documentId") or "") == document_id]
                result["queryProvenanceMatch"] = bool(matching)
                result["sourceHashMatch"] = any(item.get("sourceSha256") == expected_hash for item in matching)
                result["chunkIdPresent"] = any(bool(item.get("chunkId")) for item in matching)
                result["rerankerRan"] = bool(payload.get("reranker_ran"))

            private_docs = await okf_store._read_documents_from_bucket(
                session_id, okf_store._PRIVATE_BUCKET, okf_store._SERVICE_ROLE_KEY
            )
            result["privateResourceReadable"] = any(
                item.get("id") == document_id
                and hashlib.sha256(str(item.get("text") or "").encode("utf-8")).hexdigest() == expected_hash
                for item in private_docs
            )
        except Exception as exc:
            result["errorType"] = type(exc).__name__
        finally:
            if okf_store._private_storage_configured():
                try:
                    objects = await _walk_objects(
                        client, okf_store._PRIVATE_BUCKET, okf_store._SERVICE_ROLE_KEY,
                        f"paperly-okf/{session_id}",
                    )
                    allowed_prefix = f"paperly-okf/{session_id}/"
                    object_paths = [path for path, _metadata in objects if path.startswith(allowed_prefix)]
                    if len(object_paths) == len(objects) and object_paths:
                        deleted = await client.request(
                            "DELETE",
                            f"{okf_store._SUPABASE_URL}/storage/v1/object/{okf_store._PRIVATE_BUCKET}",
                            headers=okf_store._headers(okf_store._SERVICE_ROLE_KEY),
                            json={"prefixes": object_paths},
                        )
                        remaining = await _walk_objects(
                            client, okf_store._PRIVATE_BUCKET, okf_store._SERVICE_ROLE_KEY,
                            f"paperly-okf/{session_id}",
                        )
                        result["diagnosticObjectsRemoved"] = deleted.is_success and not remaining
                    else:
                        result["diagnosticObjectsRemoved"] = not object_paths and not objects
                except Exception as exc:
                    result["cleanupErrorType"] = type(exc).__name__

    result["status"] = "pass" if all((
        result["frontendProxy"], result["indexStatus"] == "pass",
        result["persistenceStatus"] == "persisted", result["privateResourceReadable"],
        result["queryProvenanceMatch"], result["sourceHashMatch"],
        result["chunkIdPresent"], result["rerankerRan"], result["diagnosticObjectsRemoved"],
    )) else "failed"
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), sort_keys=True))
