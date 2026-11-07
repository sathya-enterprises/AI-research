"""One definition of "which sources does this chart cite", shared by every stage.

Chart specs reach the pipeline from several producers (the report writer, the
evidence gallery, the sector-return builders, the table promoter, action edits)
and each stores its provenance slightly differently: ``sourceIds``,
``source_ids``, ``sources``, ``source``, per-point ``evidenceSource`` and
``derivedFrom[].sourceId``. The audit, the grounding step and the exporters each
read a different subset, so a chart could pass one stage and fail the next with
"source IDs do not resolve" even though nothing was actually wrong with the data.

This module makes the three operations explicit and identical everywhere:

* :func:`chart_source_ids`      - every canonical ID a chart references.
* :func:`reconcile_chart_source_ids` - make the chart's declared IDs agree with
  (a) the canonical manifest and (b) the sources that actually contain each
  quoted point; never invents a source and never changes a plotted value.
* :func:`allocate_new_source_ids` - IDs for newly fetched sources that are
  strictly after the current maximum, so existing citations never shift.
"""
from __future__ import annotations

import copy
import re
from typing import Iterable

_SID_RE = re.compile(r"\bS(\d+)\b", re.IGNORECASE)
_PROVENANCE_KEYS = ("sourceIds", "source_ids", "sources", "source")


def normalize_sid(value: object) -> str:
    """'s12', ' [S12] ' -> 'S12'; anything that is not a source ID -> ''."""
    match = _SID_RE.search(str(value or ""))
    return f"S{int(match.group(1))}" if match else ""


def sid_number(value: object) -> int:
    sid = normalize_sid(value)
    return int(sid[1:]) if sid else 0


def sort_sids(ids: Iterable[str]) -> list[str]:
    return sorted({normalize_sid(x) for x in ids if normalize_sid(x)}, key=sid_number)


def manifest_ids(manifest: object) -> set[str]:
    """Canonical IDs of a manifest, excluding unresolved placeholders."""
    out: set[str] = set()
    for entry in manifest if isinstance(manifest, list) else []:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("source_status") or entry.get("sourceStatus") or "").casefold()
        sid = normalize_sid(entry.get("id"))
        if sid and status != "unresolved":
            out.add(sid)
    return out


def next_source_number(manifest: object) -> int:
    """First S-number that cannot collide with any existing ID (gaps are never reused)."""
    highest = 0
    for entry in manifest if isinstance(manifest, list) else []:
        if isinstance(entry, dict):
            highest = max(highest, sid_number(entry.get("id")))
    return highest + 1


def _declared_ids(chart: dict) -> list[str]:
    found: list[str] = []
    for key in _PROVENANCE_KEYS:
        raw = chart.get(key)
        if raw is None:
            continue
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
        for value in values:
            found.extend(f"S{int(n)}" for n in _SID_RE.findall(str(value)))
    return found


def _points(chart: dict) -> Iterable[dict]:
    for series in chart.get("series") or []:
        if not isinstance(series, dict):
            continue
        data = series.get("data") if isinstance(series.get("data"), list) else series.get("values")
        for point in data if isinstance(data, list) else []:
            if isinstance(point, dict):
                yield point


def chart_source_ids(chart: object) -> list[str]:
    """Every source ID a chart references anywhere, sorted, de-duplicated."""
    if not isinstance(chart, dict):
        return []
    ids = list(_declared_ids(chart))
    for point in _points(chart):
        ids.append(normalize_sid(point.get("evidenceSource")))
    for record in chart.get("derivedFrom") or []:
        if isinstance(record, dict):
            ids.append(normalize_sid(record.get("sourceId")))
    return sort_sids(ids)


def _norm_quote(text: object) -> str:
    return re.sub(r"[\s|*_`]+", " ", str(text or "")).strip().casefold()


def _find_quote_source(quote: str, texts: dict[str, str], valid: set[str]) -> str:
    """The single manifest source whose text contains this exact quote, else ''."""
    needle = _norm_quote(quote)
    if len(needle) < 12:
        return ""
    hits = [sid for sid, text in texts.items() if sid in valid and needle in _norm_quote(text)]
    return hits[0] if len(hits) == 1 else ""


def reconcile_chart_source_ids(
    charts: list | None,
    manifest: list | None,
    source_texts: dict[str, str] | None = None,
) -> tuple[list, dict]:
    """Make each chart's provenance consistent with the canonical manifest.

    Rules, in order, per chart:

    1. Per-point ``evidenceSource`` that is not in the manifest is re-pointed to
       the one manifest source whose text contains the point's exact
       ``evidenceQuote``; if none/ambiguous the quote fields are dropped so the
       grounding step can try again instead of the audit failing the point.
    2. Chart-level IDs that are not in the manifest are removed.
    3. The chart-level list is the union of the valid declared IDs and the valid
       IDs actually used by quoted points, written under ``sourceIds`` AND
       ``source`` so every reader (audit, exporters, gallery) sees the same set.
       A declared ID that no quoted point uses is dropped only when EVERY numeric
       point is quoted; while any point is unquoted the declared IDs are what
       the audit checks it against, so they stay.

    Plotted values, labels and quotes are never modified.
    """
    valid = manifest_ids(manifest)
    texts = {normalize_sid(k): str(v or "") for k, v in (source_texts or {}).items() if normalize_sid(k)}
    meta = {"chartsTouched": 0, "pointsRepointed": 0, "pointsUngrounded": 0, "idsRemoved": 0, "idsAdded": 0}
    out: list = []
    for chart in charts or []:
        if not isinstance(chart, dict) or not valid:
            out.append(chart)
            continue
        before = chart_source_ids(chart)
        spec = copy.deepcopy(chart)
        changed = False
        used: set[str] = set()
        has_quoted_points = False
        has_unquoted_points = False
        for point in _points(spec):
            sid = normalize_sid(point.get("evidenceSource"))
            if not sid and not point.get("evidenceQuote"):
                if not point.get("derived") and any(isinstance(point.get(k), (int, float)) for k in ("value", "y", "count", "amount")):
                    has_unquoted_points = True
                continue
            has_quoted_points = True
            if sid in valid:
                used.add(sid)
                continue
            replacement = _find_quote_source(point.get("evidenceQuote") or "", texts, valid)
            if replacement:
                point["evidenceSource"] = replacement
                used.add(replacement)
                meta["pointsRepointed"] += 1
            else:
                for key in ("evidenceSource", "evidenceQuote", "evidenceValue"):
                    point.pop(key, None)
                meta["pointsUngrounded"] += 1
            changed = True
        declared = [sid for sid in _declared_ids(spec) if sid in valid]
        removed = [sid for sid in _declared_ids(spec) if sid not in valid]
        # Unquoted points are verified against the chart-level IDs, so those IDs
        # may only be narrowed to the quoted ones when every point is quoted.
        final = sort_sids(used if (used and has_quoted_points and not has_unquoted_points) else (set(declared) | used))
        if removed:
            meta["idsRemoved"] += len(set(removed))
        for derived in spec.get("derivedFrom") or []:
            if isinstance(derived, dict) and normalize_sid(derived.get("sourceId")) and normalize_sid(derived.get("sourceId")) not in valid:
                derived.pop("sourceId", None)
                changed = True
        if final:
            if (spec.get("sourceIds") != final) or (spec.get("source") != final):
                changed = True
            spec["sourceIds"] = final
            spec["source"] = list(final)
            for legacy in ("source_ids", "sources"):
                if legacy in spec:
                    spec.pop(legacy)
                    changed = True
        else:
            if any(spec.get(k) for k in _PROVENANCE_KEYS):
                changed = True
            for key in _PROVENANCE_KEYS:
                spec.pop(key, None)
        meta["idsAdded"] += len(set(final) - set(before))
        if changed or removed:
            meta["chartsTouched"] += 1
        out.append(spec if (changed or removed) else chart)
    return out, meta


def unresolved_chart_references(charts: list | None, manifest: list | None) -> list[dict]:
    """Diagnostics: chart IDs that do not resolve to a real manifest source."""
    valid = manifest_ids(manifest)
    rows: list[dict] = []
    for index, chart in enumerate(charts or [], 1):
        missing = [sid for sid in chart_source_ids(chart) if sid not in valid]
        if missing:
            rows.append({"chartIndex": index, "title": str((chart or {}).get("title") or "")[:120], "missing": missing})
    return rows
