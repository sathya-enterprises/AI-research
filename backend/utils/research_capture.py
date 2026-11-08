"""Opt-in local capture of web-search and source-evidence provenance.

Enable only for a diagnostic run by setting GG_RESEARCH_CAPTURE=1. Captures are
written directly under backend/diagnostics and never include API keys
or request authorization headers. They can contain the user query, public search
extracts, fetched page text, and generated report; do not enable for confidential
queries or uploaded private documents.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

log = logging.getLogger("research_capture")

_CURRENT_CAPTURE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "gg_research_capture", default=None
)

_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|(?:^|[_-])keys?(?:$|[_-])|key[_-]?suffix|credential[_-]?fingerprint|"
    r"access[_-]?token|authorization|password|secret|client[_-]?secret|bearer)",
    re.I,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"^(?:(?:x[-_]?(?:goog|amz|ms)[-_])?(?:key|api[-_]?key|credential|security[-_]?token|"
    r"token|access[-_]?token|auth|authorization|secret|client[-_]?secret|password|credentials?|signature|sig|bearer)|"
    r"awsaccesskeyid|googleaccessid|jwt)$",
    re.I,
)
_INLINE_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:(?:x[-_]?(?:goog|amz|ms)[-_])?(?:key|api[-_]?key|credential|security[-_]?token|"
    r"token|access[-_]?token|auth|authorization|secret|client[-_]?secret|password|credentials?|signature|sig|bearer)|"
    r"awsaccesskeyid|googleaccessid|jwt)=)[^&#\s\"']+"
)
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:x[-_]?(?:goog|amz|ms)[-_](?:api[-_]?key|security[-_]?token)|"
    r"api[_-]?key|access[_-]?token|token|secret|password|authorization)\b\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^,\s&]+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|_)(?:API[_-]?KEYS?|KEYS?|ACCESS[_-]?TOKEN|AUTH[_-]?TOKEN|TOKEN|SECRET|PASSWORD|CREDENTIALS?)(?:_?\d+)?$"
    r"|(?:^|_)(?:DATABASE|DB|SUPABASE)_?(?:URL|URI)$|(?:^|_)DSN$"
)


def capture_enabled() -> bool:
    return str(os.getenv("GG_RESEARCH_CAPTURE", "")).strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value[:2000]
        query = [(k, "REDACTED" if _SENSITIVE_QUERY_RE.match(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        # User-info is not needed for a public citation and can contain a
        # password or embedded bearer credential.
        netloc = parts.netloc.rsplit("@", 1)[-1]
        safe = urlunsplit((parts.scheme, netloc, parts.path, urlencode(query, doseq=True), ""))
        return _redact_text(safe)[:2000]
    except Exception:
        return str(value)[:2000]


def _configured_secret_values() -> list[str]:
    values = set()
    for name, raw_value in os.environ.items():
        if not _SENSITIVE_ENV_NAME_RE.search(name):
            continue
        for candidate in re.split(r"[,;\r\n]+", str(raw_value or "")):
            value = candidate.strip().strip("\"'").strip()
            if len(value) >= 6:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def _redact_text(value: str) -> str:
    safe = _INLINE_SECRET_QUERY_RE.sub(r"\1REDACTED", str(value or ""))
    safe = _BEARER_TOKEN_RE.sub("Bearer REDACTED", safe)
    safe = _CREDENTIAL_ASSIGNMENT_RE.sub(r"\1REDACTED", safe)
    for secret in _configured_secret_values():
        safe = safe.replace(secret, "[redacted]")
    return safe


def _safe(value: Any, field_name: str = "", depth: int = 0) -> Any:
    if depth > 12:
        return "[depth-limited]"
    if _SENSITIVE_KEY_RE.search(field_name):
        return "[redacted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        name = field_name.lower()
        if name.endswith(("url", "uri")) or name in {"requested_url", "final_url"}:
            return _redact_url(value)
        if name in {"llmprompt", "llmresponse", "llmsystem"}:
            # Full writer prompts/responses so later stages can be replayed
            # offline (scripts/eval_report.py). Still secret-redacted.
            try:
                cap = max(10_000, int(os.getenv("GG_RESEARCH_CAPTURE_LLM_CHARS", "400000")))
            except ValueError:
                cap = 400_000
            clipped = value[:cap] + (f"\n[CAPTURE TRUNCATED; original chars={len(value)}]" if len(value) > cap else "")
            return _redact_text(clipped)
        if name in {"pagecontent", "fullcontent", "full_content", "raw_content", "content", "evidence", "evidenceexcerpt", "snippet", "report", "finalreport", "writerprompt"}:
            cap = 9000 if name in {"pagecontent", "fullcontent", "full_content", "raw_content", "content", "evidence", "evidenceexcerpt"} else 15000
            clipped = value[:cap] + (f"\n[CAPTURE TRUNCATED; original chars={len(value)}]" if len(value) > cap else "")
            return _redact_text(clipped)
        clipped = value[:4000] + (f"…[truncated original chars={len(value)}]" if len(value) > 4000 else "")
        return _redact_text(clipped)
    if isinstance(value, dict):
        result = {}
        source_kind = " ".join(str(value.get(k) or "") for k in ("source_type", "sourceType", "kind", "type")).casefold()
        source_url = str(value.get("url") or "").casefold()
        # Fetched public market datasets (internal://verified-*: index closes, calculated sector
        # returns) are not private: keeping them lets offline replays check the numbers they back.
        private_source = any(term in source_kind for term in ("uploaded document", "user upload", "private document", "attached file")) or (
            source_url.startswith("internal://") and not source_url.startswith("internal://verified-"))
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text) or key_text.lower() in {"headers", "request_headers", "authorization_header"}:
                continue
            if private_source and key_text.lower() in {"pagecontent", "fullcontent", "full_content", "snippet", "evidence", "evidenceexcerpt", "text", "extractedtext"}:
                result[key_text] = "[omitted from diagnostic capture: uploaded/private source text]"
                continue
            result[key_text] = _safe(item, key_text, depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        # Stage lists must be complete enough for offline replays
        # (scripts/eval_report.py); an in-depth run has 100+ candidate sources.
        try:
            max_items = max(10, int(os.getenv("GG_RESEARCH_CAPTURE_MAX_ITEMS", "250")))
        except ValueError:
            max_items = 250
        out = [_safe(item, field_name, depth + 1) for item in items[:max_items]]
        if len(items) > max_items:
            out.append(f"[LIST TRUNCATED; original items={len(items)}]")
        return out
    return str(value)[:1000]


def _path_for(ctx: dict) -> Path:
    override = os.getenv("GG_RESEARCH_CAPTURE_DIR", "").strip()
    # Put the capture directly in backend/diagnostics so it is discoverable
    # when developers open that folder. Earlier versions nested files one level
    # deeper under diagnostics/research-captures, which made captures appear
    # missing when only the diagnostics root was inspected.
    root = Path(override).expanduser() if override else Path(__file__).resolve().parents[1] / "diagnostics"
    # Always resolve to an absolute path so logs tell the developer exactly where
    # the file was written, regardless of which working directory launched uvicorn.
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"research_capture_{ctx['captureId']}.json"


def _flush(ctx: dict) -> None:
    try:
        path = _path_for(ctx)
        output = {k: v for k, v in ctx.items() if not k.startswith("_")}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_safe(output), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        ctx["_writtenPath"] = str(path)
    except Exception as exc:
        # Capture errors must not break report generation, but must be visible.
        # Previously this branch silently swallowed PermissionError, invalid
        # overrides and disk-write failures, leaving users unable to find a file.
        ctx["_lastWriteError"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        log.exception("Research capture write failed (capture_id=%s)", ctx.get("captureId", "unknown"))
        return


def _finalize_if_request_exits_early(ctx: dict, task) -> None:
    """Ensure a capture doesn't stay `in_progress` when a request exits early."""
    if ctx.get("finishedAt"):
        return
    try:
        cancelled = bool(task.cancelled()) if task is not None else False
        error = None if cancelled or task is None else task.exception()
    except Exception as exc:
        cancelled, error = False, exc
    status = "failed" if error is not None or cancelled else "incomplete"
    ctx["status"] = status
    ctx["finishedAt"] = _now()
    ctx.setdefault("stages", {})["request_exit"] = {
        "capturedAt": _now(),
        "data": {
            "reason": "request task exited before normal capture finalization",
            "status": status,
            "cancelled": cancelled,
            "exceptionType": type(error).__name__ if error is not None else None,
        },
    }
    _flush(ctx)
    log.warning("Research capture auto-finalized after early request exit; status=%s path=%s",
                status, ctx.get("_writtenPath") or current_research_capture_path() or "<write failed; inspect earlier error>")


def begin_research_capture(question: str, tier: str):
    """Start an opt-in capture for the current async request; return ContextVar token."""
    if not capture_enabled():
        log.info("Research capture disabled; set GG_RESEARCH_CAPTURE=1 in the backend environment and restart to write diagnostics JSON")
        return None
    capture_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    ctx = {
        "schemaVersion": 1,
        "captureId": capture_id,
        "status": "in_progress",
        "startedAt": _now(),
        "reportTier": str(tier or "")[:40],
        "question": str(question or "")[:1200],
        "searchCalls": [],
        "rawExaResponses": [],
        "llmCalls": [],
        "stages": {},
    }
    token = _CURRENT_CAPTURE.set(ctx)
    # Keep the token inside the request-scoped state so early return/failure
    # finalizers can restore the ContextVar even when callers omit token.
    ctx["_contextToken"] = token
    _flush(ctx)
    # This is a final safety net for direct `/api/chat/report` calls and early
    # returns not executed through the async report-job wrapper. Normal finalizers
    # set finishedAt first, so this callback is then a no-op.
    try:
        import asyncio
        task = asyncio.current_task()
        if task is not None:
            task.add_done_callback(lambda completed_task, capture=ctx: _finalize_if_request_exits_early(capture, completed_task))
    except RuntimeError:
        # Unit tests or synchronous contexts can initiate a capture without an
        # active event loop; callers can still finalize it explicitly.
        pass
    return token


def record_exa_raw_response(query: str, params: dict, results: list | None, *, transport: str = "exa-rest", key_suffix: str = "", request_id: str = "") -> None:
    """Store raw Exa result text fields before app-side normalization/filtering."""
    ctx = _CURRENT_CAPTURE.get()
    if ctx is None:
        return
    rows = []
    for row in (results or [])[:40]:
        if not isinstance(row, dict):
            continue
        highlights = row.get("highlights") or []
        if isinstance(highlights, str):
            highlights = [highlights]
        chunks = row.get("chunks") or []
        rows.append({
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "score": row.get("score"),
            "publishedDate": row.get("publishedDate") or row.get("published_date"),
            "author": row.get("author", ""),
            "snippet": row.get("snippet", ""),
            "summary": row.get("summary", ""),
            "text": row.get("text", ""),
            "fullContent": row.get("fullContent", ""),
            "highlights": [str(x)[:2000] for x in highlights[:6]],
            "chunks": [
                {"content": c.get("content", ""), "source": c.get("source", "")}
                for c in chunks[:4] if isinstance(c, dict)
            ],
        })
    ctx["rawExaResponses"].append({
        "capturedAt": _now(), "transport": transport, "query": str(query or "")[:400],
        "requestId": str(request_id or "")[:100],
        # Keep the legacy key_suffix argument for callers that still pass it,
        # but never persist credential fingerprints in diagnostic captures.
        "requestParameters": params,
        "resultCount": len(results or []), "results": rows,
    })


def record_search_call(queries: list[str], result_lists: list[list[dict]], *, max_results: int | None = None) -> None:
    """Record the per-query normalized results returned by Exa to the app."""
    ctx = _CURRENT_CAPTURE.get()
    if ctx is None:
        return
    per_query = []
    for idx, query in enumerate(queries or []):
        results = result_lists[idx] if idx < len(result_lists) else []
        per_query.append({
            "query": str(query or "")[:400],
            "resultCount": len(results or []),
            "results": [r for r in (results or [])[:40] if isinstance(r, dict)],
        })
    ctx["searchCalls"].append({
        "capturedAt": _now(), "provider": "exa", "maxResultsPerQuery": max_results,
        "queryCount": len(queries or []), "perQuery": per_query,
    })
    _flush(ctx)


def record_llm_call(*, purpose: str, section_id: str = "", model: str = "", system: str = "", prompt: str = "",
                    response: str = "", ok: bool = True, error: str = "", tokens_in: int | None = None,
                    tokens_out: int | None = None, elapsed_ms: int = 0, attempt: int = 1, params: dict | None = None) -> None:
    """Record one LLM call (full prompt/response, redacted) for offline replay."""
    ctx = _CURRENT_CAPTURE.get()
    if ctx is None:
        return
    calls = ctx.setdefault("llmCalls", [])
    calls.append({
        "seq": len(calls) + 1, "capturedAt": _now(), "purpose": str(purpose)[:60], "sectionId": str(section_id)[:80],
        "model": str(model)[:80], "ok": bool(ok), "error": str(error)[:300], "attempt": int(attempt),
        "tokensIn": tokens_in, "tokensOut": tokens_out, "elapsedMs": int(elapsed_ms), "params": params or {},
        "llmSystem": system, "llmPrompt": prompt, "llmResponse": response,
    })
    _flush(ctx)


def record_research_stage(name: str, payload: Any) -> None:
    """Write a named snapshot of the pipeline stage; overwrites prior snapshot of same name."""
    ctx = _CURRENT_CAPTURE.get()
    if ctx is None:
        return
    ctx["stages"][str(name)[:120]] = {"capturedAt": _now(), "data": payload}
    _flush(ctx)


def finish_research_capture(final_payload: dict | None = None, *, status: str = "completed", token=None) -> str | None:
    """Save final report/audit metadata and restore the caller's ContextVar."""
    ctx = _CURRENT_CAPTURE.get()
    path = None
    if ctx is not None:
        if final_payload is not None:
            ctx["stages"]["final_output"] = {"capturedAt": _now(), "data": final_payload}
        ctx["status"] = str(status or "completed")
        ctx["finishedAt"] = _now()
        _flush(ctx)
        path = ctx.get("_writtenPath")
    active_token = token or (ctx.get("_contextToken") if ctx is not None else None)
    if active_token is not None:
        try:
            _CURRENT_CAPTURE.reset(active_token)
        except Exception as exc:
            # Context tokens can only be reset in the context that created them;
            # keep the report alive but surface a diagnostic rather than hiding it.
            log.debug("Research capture context reset skipped: %s", type(exc).__name__)
    return path


def current_capture_active() -> bool:
    return _CURRENT_CAPTURE.get() is not None


def current_research_capture_path() -> str | None:
    """Return the absolute JSON path for the current capture, if enabled."""
    ctx = _CURRENT_CAPTURE.get()
    if not isinstance(ctx, dict):
        return None
    written = ctx.get("_writtenPath")
    if written:
        return str(written)
    try:
        return str(_path_for(ctx))
    except Exception:
        return None
