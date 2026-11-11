"""
POST /api/rag/index   — Index uploaded file text in the RAG service
POST /api/rag/query   — Query RAG for grounded context
DELETE /api/rag/session/{session_id} — Clean up session
GET  /api/rag/health  — Health check
"""
import asyncio
import base64
import hashlib
import io
import logging
import re
import shutil
import subprocess
from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse

from utils.rag_client import rag_index, rag_query, rag_delete_session, rag_health_details

router = APIRouter()
log = logging.getLogger("rag_route")

_MAX_BINARY_FILE_BYTES = 40 * 1024 * 1024
_MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _decode_attachment_data(raw: object, max_bytes: int) -> bytes:
    value = str(raw or "").strip()
    if value.startswith("data:") and "," in value:
        value = value.split(",", 1)[1]
    if not value:
        raise ValueError("Attachment data is empty")
    try:
        data = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise ValueError("Attachment data is not valid base64") from exc
    if not data:
        raise ValueError("Attachment data is empty")
    if len(data) > max_bytes:
        raise ValueError(f"Attachment exceeds the {max_bytes // (1024 * 1024)} MB extraction limit")
    return data


def _extract_docx_text(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:
        raise ValueError("DOCX extraction is not installed on the backend") from exc
    document = Document(io.BytesIO(data))
    parts: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)
    for table_index, table in enumerate(document.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", cell.text).strip() for cell in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            parts.append(f"[Table {table_index}]\n" + "\n".join(rows))
    for section_index, section in enumerate(document.sections, start=1):
        for area_name, area in (("Header", section.header), ("Footer", section.footer)):
            area_text = " ".join(p.text.strip() for p in area.paragraphs if p.text.strip())
            if area_text:
                parts.append(f"[{area_name} {section_index}] {area_text}")
    return "\n\n".join(parts).strip()


def _extract_legacy_doc_text(data: bytes) -> str:
    antiword = shutil.which("antiword")
    if not antiword:
        raise ValueError("Legacy .doc extraction is unavailable; convert the file to .docx or install antiword")
    try:
        result = subprocess.run(
            [antiword, "-"], input=data, capture_output=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Legacy .doc extraction timed out") from exc
    if result.returncode != 0:
        raise ValueError("Legacy .doc could not be decoded")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _attachment_document(name: str, text: str, file_type: str, source_type: str = "file") -> dict:
    text = str(text or "").strip()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return {
        "id": digest[:32], "name": name, "text": text, "source_type": source_type,
        "file_type": file_type, "metadata": {"source_sha256": digest},
    }


@router.post("/index")
async def index_documents(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "Request body must be a JSON object"}, status_code=400)
    session_id = str(body.get("session_id") or "").strip()
    documents = body.get("documents", [])
    images = body.get("images", [])
    binary_files = body.get("binaryFiles", [])

    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
    if not isinstance(documents, list) or not isinstance(images, list) or not isinstance(binary_files, list):
        return JSONResponse({"error": "documents, images, and binaryFiles must be lists"}, status_code=400)
    if not documents and not images and not binary_files:
        return JSONResponse({"error": "No attachment content was supplied"}, status_code=400)

    normalized_documents = []
    extraction_errors = []
    for document in documents:
        if not isinstance(document, dict):
            extraction_errors.append({"name": "Unknown attachment", "error": "Invalid text document"})
            continue
        text = str(document.get("text") or "").strip()
        name = str(document.get("name") or "Attached document")[:240]
        if text:
            normalized_documents.append({
                **document,
                "id": str(document.get("id") or hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]),
                "name": name,
                "text": text,
            })

    extracted_documents = []
    for binary_file in binary_files:
        if not isinstance(binary_file, dict):
            extraction_errors.append({"name": "Unknown attachment", "error": "Invalid binary document"})
            continue
        name = str(binary_file.get("name") or "Attached document")[:240]
        extension = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
        try:
            raw = _decode_attachment_data(binary_file.get("data"), _MAX_BINARY_FILE_BYTES)
            if extension == "docx" or "wordprocessingml.document" in str(binary_file.get("mimeType") or ""):
                text = await asyncio.to_thread(_extract_docx_text, raw)
            elif extension == "doc" or str(binary_file.get("mimeType") or "") == "application/msword":
                text = await asyncio.to_thread(_extract_legacy_doc_text, raw)
            else:
                raise ValueError("This binary attachment format has no text extractor")
            if len(text) < 20:
                raise ValueError("No readable text was found in the document")
            file_type = str(binary_file.get("mimeType") or extension)
            document = _attachment_document(name, text, file_type)
            normalized_documents.append(document)
            extracted_documents.append({"name": name, "text": text, "file_type": file_type})
        except Exception as exc:
            extraction_errors.append({"name": name, "error": str(exc)[:240]})

    # OCR image attachments and rendered, text-empty PDF pages before indexing.
    # PDF page image names are grouped back into one source document.
    image_groups: dict[str, list[dict]] = {}
    for image in images:
        if not isinstance(image, dict):
            extraction_errors.append({"name": "Unknown image", "error": "Invalid image payload"})
            continue
        name = str(image.get("documentName") or image.get("name") or "Attached image")[:240]
        group_name = re.sub(r"\s+page\s+\d+$", "", name, flags=re.I).strip() or name
        image_groups.setdefault(group_name, []).append(image)

    if image_groups:
        try:
            from routes.report import extract_data_from_images
        except Exception as exc:
            log.warning("RAG attachment OCR unavailable: %s", type(exc).__name__)
            extract_data_from_images = None
        for name, group in image_groups.items():
            if extract_data_from_images is None:
                extraction_errors.append({"name": name, "error": "Image OCR is unavailable on the backend"})
                continue
            valid_images = []
            for image in group[:8]:
                try:
                    data = _decode_attachment_data(image.get("data"), _MAX_IMAGE_BYTES)
                    mime_type = str(image.get("mimeType") or "image/jpeg").strip().lower()
                    if not mime_type.startswith("image/"):
                        raise ValueError("Image MIME type is invalid")
                    valid_images.append({
                        "name": str(image.get("name") or name)[:240],
                        "mimeType": mime_type,
                        "data": base64.b64encode(data).decode("ascii"),
                    })
                except Exception as exc:
                    extraction_errors.append({"name": name, "error": str(exc)[:240]})
            if not valid_images:
                continue
            try:
                text = await extract_data_from_images(
                    str(body.get("question") or "Extract all text, tables, labels, and numeric data for document indexing."),
                    valid_images,
                )
            except Exception as exc:
                text = ""
                log.warning("RAG attachment OCR failed (%s)", type(exc).__name__)
            if len(str(text or "").strip()) < 20:
                extraction_errors.append({"name": name, "error": "No readable text was extracted from this image"})
                continue
            indexed_text = f"[File: {name}]\n[OCR extracted from attached image/page]\n{text.strip()}"
            mime_type = valid_images[0].get("mimeType") or "image"
            normalized_documents.append(_attachment_document(name, indexed_text, mime_type, "image_ocr"))
            extracted_documents.append({"name": name, "text": indexed_text, "file_type": mime_type})

    if not normalized_documents:
        return JSONResponse({
            "error": "No readable attachment text was available to index",
            "index_status": "no_readable_text",
            "extracted_documents": extracted_documents,
            "extraction_errors": extraction_errors,
        }, status_code=422)

    log.info("RAG /index: session=%s docs=%d extracted_images=%d extraction_errors=%d",
             session_id[:8], len(normalized_documents), len(extracted_documents), len(extraction_errors))
    result = await rag_index(session_id, normalized_documents)
    if not isinstance(result, dict):
        result = {"error": "RAG service returned an invalid response"}
    diagnostics = result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {}
    document_statuses = diagnostics.get("documents") if isinstance(diagnostics.get("documents"), list) else []
    persisted = result.get("vector_persistence") if isinstance(result.get("vector_persistence"), dict) else {}
    if not persisted:
        available = bool(result.get("chunks_added") or result.get("total_chunks"))
        persisted = {
            "store": result.get("vector_store") or "supabase_pgvector",
            "status": "persisted" if available and not result.get("error") else "no_data",
            "chunksAdded": int(result.get("chunks_added") or 0),
            "totalChunksInSession": int(result.get("total_chunks") or 0),
        }
    succeeded = not result.get("error") and persisted.get("status") in {"persisted", "unchanged"}
    result.update({
        "index_status": "indexed" if succeeded else "failed",
        "vector_persistence": persisted,
        "extracted_documents": extracted_documents,
        "extraction_errors": extraction_errors,
    })
    if document_statuses:
        result["indexed_documents"] = document_statuses
    status_code = 200 if succeeded else (502 if result.get("error") else 422)
    return JSONResponse(result, status_code=status_code)


@router.post("/query")
async def query_rag(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    session_id = body.get("session_id", "").strip()
    question   = body.get("question", "").strip()
    top_k      = int(body.get("top_k", 8))
    min_score  = float(body.get("min_score", 0.15))
    is_general = bool(body.get("is_general", False))

    if not session_id or not question:
        return JSONResponse({"error": "session_id and question required"}, status_code=400)

    result = await rag_query(session_id, question, top_k, min_score, is_general)
    return JSONResponse(result)


@router.delete("/session/{session_id}")
async def delete_rag_session(session_id: str):
    await rag_delete_session(session_id)
    return JSONResponse({"success": True, "session_id": session_id})


@router.get("/health")
async def check_rag_health():
    result = await rag_health_details()
    if result.get("reachable") and result.get("ready", True):
        result["rag_service"] = "ok"
    elif result.get("reachable"):
        result["rag_service"] = "degraded"
    else:
        result["rag_service"] = "unreachable"
    return JSONResponse(result, status_code=200 if result["rag_service"] == "ok" else 503)
