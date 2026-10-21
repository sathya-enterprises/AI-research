"""Safe Supabase Storage diagnostics and copy-only legacy OKF migration.

Run this module only from the trusted RAG service environment. It never prints
credentials, object names, object contents, or raw provider response bodies.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
import okf_store  # noqa: E402 - environment must load first

log = logging.getLogger("okf-storage-ops")


def _object_url(bucket: str, path: str) -> str:
    return f"{okf_store._SUPABASE_URL}/storage/v1/object/{quote(bucket, safe='')}/{quote(path, safe='/')}"


def _list_url(bucket: str) -> str:
    return f"{okf_store._SUPABASE_URL}/storage/v1/object/list/{quote(bucket, safe='')}"


def _status_result(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "ok"
    if status_code in (401, 403):
        return "authentication_or_permission"
    if status_code == 404:
        return "bucket_or_object_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "provider_error"
    return "storage_error"


async def _list_level(client: httpx.AsyncClient, bucket: str, credential: str, prefix: str) -> List[dict]:
    found: List[dict] = []
    offset = 0
    page_size = 1000
    while True:
        response = await client.post(
            _list_url(bucket),
            headers=okf_store._headers(credential),
            json={"prefix": prefix, "limit": page_size, "offset": offset},
        )
        if not response.is_success:
            raise RuntimeError(f"Storage list failed: HTTP {response.status_code} ({_status_result(response.status_code)})")
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError("Storage list returned an invalid response")
        found.extend(item for item in data if isinstance(item, dict))
        if len(data) < page_size:
            return found
        offset += len(data)


async def _walk_objects(client: httpx.AsyncClient, bucket: str, credential: str, prefix: str) -> List[Tuple[str, dict]]:
    """Recursively list files without leaving the requested RAG namespace."""
    found: List[Tuple[str, dict]] = []
    stack = [prefix.strip("/")]
    while stack:
        current = stack.pop()
        for entry in await _list_level(client, bucket, credential, current):
            name = str(entry.get("name") or "").strip("/")
            if not name or name in {".", ".."} or "/" in name:
                continue
            path = f"{current}/{name}"
            if entry.get("id") is None:
                stack.append(path)
            else:
                found.append((path, entry))
    return sorted(found, key=lambda item: item[0])


async def probe_private_storage(*, diagnostic_write: bool = True) -> Dict[str, Any]:
    """Check configured private-bucket reachability and optionally round-trip a temp object."""
    if not okf_store._SUPABASE_URL:
        return {"reachable": False, "status": "missing_supabase_url"}
    if not okf_store._SERVICE_ROLE_KEY:
        return {"reachable": False, "status": "missing_server_credential"}
    if not okf_store._PRIVATE_BUCKET:
        return {"reachable": False, "status": "missing_private_bucket"}

    result: Dict[str, Any] = {"bucketConfigured": True, "writeTest": "not_run"}
    credential = okf_store._SERVICE_ROLE_KEY
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            entries = await _list_level(client, okf_store._PRIVATE_BUCKET, credential, "paperly-okf")
            result.update(reachable=True, listStatus="ok", rootEntryCount=len(entries))
        except RuntimeError as exc:
            result.update(reachable=False, listStatus=str(exc).split(" (")[0].split(": ")[-1])
            return result
        except Exception as exc:
            result.update(reachable=False, listStatus=f"network_error:{type(exc).__name__}")
            return result

        if not diagnostic_write:
            return result

        path = f"paperly-okf/.diagnostics/tempo-probe-{uuid.uuid4().hex}.txt"
        content = f"OKF storage diagnostic {uuid.uuid4()}\n".encode("utf-8")
        expected_hash = hashlib.sha256(content).hexdigest()
        object_url = _object_url(okf_store._PRIVATE_BUCKET, path)
        uploaded = False
        try:
            upload = await client.post(
                object_url,
                headers={**okf_store._headers(credential, "text/plain; charset=utf-8"), "x-upsert": "false"},
                content=content,
            )
            if not upload.is_success:
                result["writeTest"] = f"upload_failed:{upload.status_code}:{_status_result(upload.status_code)}"
                return result
            uploaded = True
            download = await client.get(object_url, headers=okf_store._headers(credential))
            if not download.is_success:
                result["writeTest"] = f"download_failed:{download.status_code}:{_status_result(download.status_code)}"
                return result
            result["downloadHashMatches"] = hashlib.sha256(download.content).hexdigest() == expected_hash
            result["writeTest"] = "round_trip_pass" if result["downloadHashMatches"] else "hash_mismatch"
        except Exception as exc:
            result["writeTest"] = f"request_failed:{type(exc).__name__}"
        finally:
            if uploaded:
                try:
                    delete = await client.request(
                        "DELETE",
                        f"{okf_store._SUPABASE_URL}/storage/v1/object/{quote(okf_store._PRIVATE_BUCKET, safe='')}",
                        headers=okf_store._headers(credential),
                        json={"prefixes": [path]},
                    )
                    result["temporaryObjectDeleted"] = delete.is_success
                    if not delete.is_success:
                        result["deleteStatus"] = f"{delete.status_code}:{_status_result(delete.status_code)}"
                except Exception as exc:
                    result["temporaryObjectDeleted"] = False
                    result["deleteStatus"] = f"request_failed:{type(exc).__name__}"
    return result


async def migrate_legacy_okf_to_private() -> Dict[str, Any]:
    """Copy and hash-verify only paperly-okf/ objects; never delete source data."""
    if not (okf_store._SUPABASE_URL and okf_store._SUPABASE_KEY and okf_store._PRIVATE_BUCKET and okf_store._SERVICE_ROLE_KEY):
        raise RuntimeError("Migration requires the legacy read key and private server credential")
    if okf_store._BUCKET == okf_store._PRIVATE_BUCKET:
        raise RuntimeError("Source and destination buckets must be different")

    source_credential = okf_store._SUPABASE_KEY
    target_credential = okf_store._SERVICE_ROLE_KEY
    copied = skipped = 0
    async with httpx.AsyncClient(timeout=60) as client:
        source = await _walk_objects(client, okf_store._BUCKET, source_credential, "paperly-okf")
        # Establish destination access and inventory existing files before any
        # writes. Supabase returns HTTP 400 (not 404) for some missing-object
        # GETs, so do not infer absence from a GET response.
        destination_before = await _walk_objects(
            client, okf_store._PRIVATE_BUCKET, target_credential, "paperly-okf"
        )
        destination_paths = {path for path, _ in destination_before}

        for path, metadata in source:
            source_response = await client.get(
                _object_url(okf_store._BUCKET, path),
                headers=okf_store._headers(source_credential),
            )
            if not source_response.is_success:
                raise RuntimeError(f"Source download failed: HTTP {source_response.status_code} ({_status_result(source_response.status_code)})")
            source_bytes = source_response.content
            source_hash = hashlib.sha256(source_bytes).hexdigest()
            target_url = _object_url(okf_store._PRIVATE_BUCKET, path)
            if path in destination_paths:
                existing = await client.get(target_url, headers=okf_store._headers(target_credential))
                if not existing.is_success:
                    raise RuntimeError(f"Destination download failed: HTTP {existing.status_code} ({_status_result(existing.status_code)})")
                if hashlib.sha256(existing.content).hexdigest() != source_hash:
                    raise RuntimeError("Destination contains different content; migration stopped without overwriting it")
                skipped += 1
                continue

            entry_meta = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
            content_type = str(entry_meta.get("mimetype") or "application/octet-stream")
            upload_headers = {
                **okf_store._headers(target_credential, content_type),
                "x-upsert": "false",
            }
            user_metadata = metadata.get("user_metadata") or metadata.get("userMetadata")
            if isinstance(user_metadata, dict) and user_metadata:
                upload_headers["x-metadata"] = json.dumps(user_metadata, separators=(",", ":"))
            uploaded = await client.post(target_url, headers=upload_headers, content=source_bytes)
            if not uploaded.is_success:
                raise RuntimeError(f"Destination upload failed: HTTP {uploaded.status_code} ({_status_result(uploaded.status_code)})")
            verify = await client.get(target_url, headers=okf_store._headers(target_credential))
            if not verify.is_success or hashlib.sha256(verify.content).hexdigest() != source_hash:
                raise RuntimeError("Destination verification failed; source objects were left unchanged")
            copied += 1
            destination_paths.add(path)

        destination = await _walk_objects(client, okf_store._PRIVATE_BUCKET, target_credential, "paperly-okf")
        # Exact path set comparison catches missing and unexpected files within the migrated prefix.
        source_paths = {path for path, _ in source}
        destination_paths = {path for path, _ in destination}
        if not source_paths.issubset(destination_paths):
            raise RuntimeError("Final migration verification found missing destination objects")
        for path, _ in source:
            original = await client.get(_object_url(okf_store._BUCKET, path), headers=okf_store._headers(source_credential))
            target = await client.get(_object_url(okf_store._PRIVATE_BUCKET, path), headers=okf_store._headers(target_credential))
            if not original.is_success or not target.is_success or hashlib.sha256(original.content).digest() != hashlib.sha256(target.content).digest():
                raise RuntimeError("Final source/destination content hash verification failed")
            if path not in destination_paths:
                raise RuntimeError("Final destination inventory did not include a source object")

    return {
        "sourceObjectCount": len(source),
        "destinationObjectCountUnderPrefix": len(destination),
        "copied": copied,
        "alreadyPresentAndVerified": skipped,
        "allSourceHashesVerified": True,
        "sourceObjectsDeleted": 0,
        "unrelatedBucketObjectsCopied": 0,
    }


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("probe", "migrate"))
    parser.add_argument("--no-diagnostic-write", action="store_true", help="Only check bucket listing access")
    args = parser.parse_args()
    if args.operation == "probe":
        result = await probe_private_storage(diagnostic_write=not args.no_diagnostic_write)
    else:
        result = await migrate_legacy_okf_to_private()
    print(json.dumps(result, sort_keys=True))
    if args.operation == "probe" and result.get("writeTest") not in (None, "round_trip_pass"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
