"""Repetition control (Stage I, FIX 6).

  * ``data_takeaway(spec)``: a chart's KEY TAKEAWAY computed from its plotted values (highest/lowest,
    wins vs the benchmark, first/last), never the body sentence that interprets the figure; it is
    dropped when it still reads like a sentence of the body (``echoes_body``). The CONTEXT box
    ("Supports: <the same sentence>") is not produced any more.
  * ``dedupe_tables(report, charts)``: each dataset appears once - a later table whose numbers are
    mostly (>= 80 %) those of an earlier table, or whose numbers are all plotted by one chart, is
    removed with its caption and Source line.
  * ``cap_headline_stats(report, cap=2)``: a precise statistic (a decimal percentage or a currency
    amount) is stated at most ``cap`` times in the prose; a further mention in parentheses is
    removed, and a further sentence whose only statistics are such repeats is removed.
  * ``repetition_metrics(report, charts)``: max repeats of a headline number, duplicate sentences
    across figure blocks, duplicate tables (eval and the publication report).
"""
from __future__ import annotations

import logging
import re
from collections import Counter

log = logging.getLogger("repetition")

_NUM_RE = re.compile(r"[+\-−]?\d+(?:,\d{3})*(?:\.\d+)?")
_STAT_RE = re.compile(r"(?<![\w.])(?:[+\-−]?\d+\.\d+%|(?:₹|Rs\.?\s?|\$|US\$)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakh crore|crore|trillion|billion|million|bn|cr))?)")
_CITE_RE = re.compile(r"\s*\[\s*S\d+(?:\.\d+)?(?:\s*[,;]\s*S\d+(?:\.\d+)?)*\s*\]")
_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}")


def _f(raw: str) -> float | None:
    try:
        return round(float(raw.replace("−", "-").replace(",", "")), 2)
    except ValueError:
        return None


# ── chart takeaways ──────────────────────────────────────────────────────────────────────────────
def _points(series: dict) -> list[tuple[str, float, str]]:
    out = []
    for p in series.get("data") or series.get("points") or []:
        if isinstance(p, dict) and isinstance(p.get("value"), (int, float)):
            out.append((str(p.get("label") or ""), float(p["value"]), str(p.get("evidenceValue") or "")))
    return out


def _fmt(value: float, raw: str, unit: str) -> str:
    if raw and re.search(r"\d", raw):
        return raw.strip()
    text = f"{value:+.2f}" if unit == "%" else f"{value:,.2f}".rstrip("0").rstrip(".")
    return text + ("%" if unit == "%" else "")


def data_takeaway(spec: dict) -> str:
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    unit = str(spec.get("unit") or ("%" if "%" in str(spec.get("yLabel") or "") else ""))
    kind = str(spec.get("type") or "")
    if len(series) == 2 and kind in {"grouped_bar", "line", "bar"}:
        a, b = _points(series[0]), _points(series[1])
        if len(a) >= 3 and len(a) == len(b):
            wins = sum(1 for (_, x, _), (_, y, _) in zip(a, b) if x > y)
            gaps = [(la, x - y) for (la, x, _), (_, y, _) in zip(a, b)]
            widest = max(gaps, key=lambda g: abs(g[1]))
            return (f"{series[0].get('name')} beat {series[1].get('name')} in {wins} of {len(a)} periods; "
                    f"the widest gap was {widest[1]:+.2f} pp in {widest[0]}.")
    if len(series) == 1:
        pts = _points(series[0])
        if len(pts) >= 3:
            if kind in {"line", "area", "spline", "step_line"}:
                first, last = pts[0], pts[-1]
                peak = max(pts, key=lambda p: p[1])
                return (f"{_fmt(first[1], first[2], unit)} in {first[0]}, {_fmt(last[1], last[2], unit)} in {last[0]}; "
                        f"peak {_fmt(peak[1], peak[2], unit)} in {peak[0]}.")
            hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
            spread = hi[1] - lo[1]
            gap = f"; spread {spread:.2f} pp" if unit == "%" else ""
            return f"Highest: {hi[0]} ({_fmt(hi[1], hi[2], unit)}); lowest: {lo[0]} ({_fmt(lo[1], lo[2], unit)}){gap}."
    return ""


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+|\d+(?:\.\d+)?", _CITE_RE.sub("", str(text or "")).casefold()))


def echoes_body(sentence: str, body: str, threshold: float = 0.6) -> bool:
    mine = _tokens(sentence)
    if len(mine) < 4:
        return False
    for s in re.split(r"(?<=[.!?])\s+", str(body or "")):
        other = _tokens(s)
        if other and len(mine & other) / len(mine | other) >= threshold:
            return True
    return False


# ── tables ───────────────────────────────────────────────────────────────────────────────────────
def _tables(lines: list[str]) -> list[dict]:
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and _SEP_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            body = "\n".join(lines[i + 2:j])
            values = [v for v in (_f(n) for n in _NUM_RE.findall(_CITE_RE.sub("", body))) if v is not None]
            start = i
            k = i - 1
            while k >= 0 and not lines[k].strip():
                k -= 1
            if k >= 0 and re.match(r"^\s*(?:\*\*Table\b|#{3,4}\s)", lines[k]):
                start = k
            end = j
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k < len(lines) and re.match(r"^\s*\*?Source:", lines[k]):
                end = k + 1
            out.append({"start": start, "end": end, "header": lines[i], "values": values,
                        "caption": lines[start] if start != i else ""})
            i = j
        else:
            i += 1
    return out


def _chart_values(charts: list) -> list[set[float]]:
    out = []
    for c in charts or []:
        vals = {round(p[1], 2) for s in (c.get("series") or []) if isinstance(s, dict) for p in _points(s)}
        if vals:
            out.append(vals)
    return out


def dedupe_tables(report: str, charts: list | None = None, *, min_values: int = 4, overlap: float = 0.8) -> tuple[str, list[dict]]:
    lines = str(report or "").split("\n")
    tables = _tables(lines)
    chart_sets = _chart_values(charts or [])
    drop: list[dict] = []
    kept: list[Counter] = []
    for t in tables:
        values = t["values"]
        bag = Counter(abs(v) for v in values)
        reason = ""
        if len(values) >= min_values:
            for earlier in kept:
                common = sum((bag & earlier).values())
                if common / min(sum(bag.values()), sum(earlier.values())) >= overlap:
                    reason = "same data as an earlier table"
                    break
            if not reason:
                distinct = {round(v, 2) for v in values}
                if any(len(distinct) >= min_values and distinct <= cs for cs in chart_sets):
                    reason = "every value is already plotted in a figure"
        if reason:
            drop.append({"table": (t["caption"] or t["header"]).strip()[:120], "reason": reason, "start": t["start"], "end": t["end"]})
        elif len(values) >= min_values:
            kept.append(bag)
    if not drop:
        return report, []
    remove = set()
    for d in drop:
        remove.update(range(d["start"], d["end"]))
    log.info("Repetition: removed %d duplicate table(s)", len(drop))
    text = "\n".join(line for n, line in enumerate(lines) if n not in remove)
    return re.sub(r"\n{3,}", "\n\n", text), [{k: d[k] for k in ("table", "reason")} for d in drop]


# ── headline statistics ──────────────────────────────────────────────────────────────────────────
def _is_prose(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith(("|", ">", "#", "**Table", "Source:", "*Source:", "[CHART_", "<"))


def _norm_stat(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace("−", "-")).strip().casefold()


def cap_headline_stats(report: str, cap: int = 2) -> tuple[str, list[dict]]:
    lines = str(report or "").split("\n")
    seen: Counter = Counter()
    actions: list[dict] = []
    for n, line in enumerate(lines):
        if not _is_prose(line):
            continue
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z\[(*\"'])", line)
        out_sentences = []
        for sentence in sentences:
            stats = [_norm_stat(m.group(0)) for m in _STAT_RE.finditer(sentence)]
            over = [s for s in stats if seen[s] >= cap]
            new = sentence
            if over:
                # 1) a parenthetical that only restates the number goes
                for stat in set(over):
                    pattern = re.compile(r"\s*\((?:[^()]{0,30}?)" + re.escape(stat).replace(r"\ ", r"\s*") + r"(?:[^()]{0,20}?)\)", re.I)
                    new, k = pattern.subn("", new)
                    if k:
                        actions.append({"stat": stat, "action": "removed a repeated parenthetical"})
                remaining = [_norm_stat(m.group(0)) for m in _STAT_RE.finditer(new)]
                # 2) a sentence whose statistics are all over-cap repeats goes
                if remaining and all(seen[s] >= cap for s in remaining) and not re.search(r"\b(?:Figure|Table)\s+\d", new):
                    actions.append({"stat": ", ".join(sorted(set(remaining))), "action": "removed a repeated sentence",
                                    "sentence": sentence.strip()[:200]})
                    new = ""
            for s in [_norm_stat(m.group(0)) for m in _STAT_RE.finditer(new)]:
                seen[s] += 1
            if new.strip():
                out_sentences.append(new)
        bullet = re.match(r"^\s*(?:[-*]|\d+[.)])\s+", line)
        joined = " ".join(out_sentences)
        if not joined.strip() or (bullet and not joined.strip()[len(bullet.group(0).strip()):].strip()):
            lines[n] = ""
        else:
            lines[n] = joined
    if actions:
        log.info("Repetition: headline-statistic cap applied %d time(s)", len(actions))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)), actions


# ── metrics ──────────────────────────────────────────────────────────────────────────────────────
def repetition_metrics(report: str, charts: list | None = None) -> dict:
    lines = str(report or "").split("\n")
    counts = Counter(_norm_stat(m.group(0)) for line in lines if _is_prose(line) for m in _STAT_RE.finditer(line))
    top = counts.most_common(1)[0] if counts else ("", 0)
    blocks = []
    for c in charts or []:
        for key in ("takeaway", "rationale", "caption"):
            text = str(c.get(key) or "").strip()
            if text:
                blocks.append(re.sub(r"\W+", " ", _CITE_RE.sub("", text)).strip().casefold())
    dup_blocks = sum(n - 1 for n in Counter(blocks).values() if n > 1)
    body = "\n".join(line for line in lines if _is_prose(line))
    echo = sum(1 for c in charts or [] if str(c.get("takeaway") or "").strip() and echoes_body(str(c.get("takeaway")), body))
    tables = _tables(lines)
    dup_tables = 0
    kept: list[Counter] = []
    for t in tables:
        bag = Counter(abs(v) for v in t["values"])
        if len(t["values"]) >= 4:
            if any(sum((bag & e).values()) / min(sum(bag.values()), sum(e.values())) >= 0.8 for e in kept):
                dup_tables += 1
            else:
                kept.append(bag)
    return {"max_headline_repeats": top[1], "max_headline_stat": top[0], "stats_over_cap": sum(1 for v in counts.values() if v > 2),
            "duplicate_figure_block_sentences": dup_blocks, "figure_blocks_echoing_body": echo,
            "duplicate_tables": dup_tables, "tables": len(tables)}
