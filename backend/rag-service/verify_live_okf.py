"""Exercise real OKF retrieval through the running local RAG service.

Output is deliberately limited to booleans/counts; document text, names,
session identifiers, and credentials are never printed.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
import json
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
import okf_store  # noqa: E402 - environment must load first


async def verify() -> dict:
    base_url = "http://127.0.0.1:" + str(okf_store.os.environ.get("RAG_PORT", "7860"))
    sessions = await okf_store.list_session_ids()
    selected = None
    private_docs = []
    for session_id in sessions:
        if okf_store._private_storage_configured():
            private_docs = await okf_store._read_documents_from_bucket(
                session_id, okf_store._PRIVATE_BUCKET, okf_store._SERVICE_ROLE_KEY
            )
        if private_docs:
            selected = session_id
            break

    result = {
        "serviceReachable": False,
        "privateResourceFound": bool(selected and private_docs),
        "restoreStatus": "not_run",
        "retrieved": 0,
        "provenanceMatch": False,
        "sourceHashMatch": False,
        "chunkIdPresent": False,
        "rerankerLoaded": False,
        "rerankerRan": False,
    }
    if not selected:
        result["status"] = "no_private_okf_resource_found"
        return result

    docs = [item for item in private_docs if len(item.get("text", "")) >= 120]
    if not docs:
        result["status"] = "private_okf_resource_has_no_retrievable_text"
        return result

    tokenized = [re.findall(r"[A-Za-z0-9][A-Za-z0-9%.,/-]*", doc["text"]) for doc in docs]
    document_frequency = Counter()
    for tokens in tokenized:
        document_frequency.update({token.casefold() for token in tokens})
    stopwords = {"this", "that", "with", "from", "were", "have", "into", "their", "which", "there", "about", "would", "could", "should", "these", "those", "than", "then", "when", "where", "what", "will", "been", "being", "also", "more", "most", "such", "some", "only", "over", "under", "after", "before"}
    candidates = []
    total_docs = max(1, len(docs))
    for doc, tokens in zip(docs, tokenized):
        for offset in range(max(1, len(tokens) - 7)):
            window = tokens[offset:offset + 8]
            informative = [w.casefold() for w in window if len(w) >= 4 and w.casefold() not in stopwords]
            if len(informative) < 4:
                continue
            rarity = sum((total_docs + 1) / (document_frequency[word] + 1) for word in informative)
            candidates.append((rarity, doc, " ".join(window)))
    candidates.sort(key=lambda item: item[0], reverse=True)
    candidates = candidates[:5]
    if not candidates:
        result["status"] = "source_text_insufficient_for_query"
        return result

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10)) as client:
        try:
            health_response = await client.get(f"{base_url}/health")
            result["serviceReachable"] = health_response.is_success
            if not health_response.is_success:
                result["status"] = f"health_http_{health_response.status_code}"
                return result
            health = health_response.json()
            result["rerankerLoaded"] = bool((health.get("reranker") or {}).get("enabled"))
            data = None
            matching = []
            expected_hash = ""
            for _rarity, doc, query in candidates:
                response = await client.post(
                    f"{base_url}/query",
                    json={"session_id": selected, "question": query, "top_k": 30, "min_score": 0.0},
                )
                if not response.is_success:
                    result["status"] = f"query_http_{response.status_code}"
                    return result
                data = response.json()
                evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
                expected_id = str(doc.get("id") or "")
                expected_hash = str((doc.get("metadata") or {}).get("source_sha256") or "")
                if not expected_hash:
                    # Older OKF frontmatter may not store an upload checksum;
                    # the live index hashes its extracted text deterministically.
                    expected_hash = hashlib.sha256(str(doc.get("text") or "").encode("utf-8")).hexdigest()
                matching = [item for item in evidence if str(item.get("documentId") or "") == expected_id]
                if matching:
                    break
            if data is None:
                result["status"] = "no_query_response"
                return result
            result.update(
                restoreStatus=str(data.get("restore_status") or "unknown"),
                retrieved=int(data.get("retrieved") or 0),
                provenanceMatch=bool(matching),
                sourceHashMatch=bool(expected_hash and any(item.get("sourceSha256") == expected_hash for item in matching)),
                chunkIdPresent=any(bool(item.get("chunkId")) for item in matching),
                rerankerRan=bool(data.get("reranker_ran")),
            )
            result["status"] = "pass" if all((result["serviceReachable"], result["privateResourceFound"], result["retrieved"] > 0, result["provenanceMatch"], result["sourceHashMatch"], result["chunkIdPresent"])) else "evidence_check_failed"
        except Exception as exc:
            result["status"] = f"request_failed:{type(exc).__name__}"
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), sort_keys=True))
