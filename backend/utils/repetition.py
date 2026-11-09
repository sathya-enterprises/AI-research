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
_STAT_RE = re.compile(r"(?<![\w.])(?:[+\-−]?\d+\.\d+%|(?:₹|Rs\.?\s?|\$|US\$)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakh crore|crore|trillion|billion|million|bn|cr))?"
                      r"|\d{1,3}(?:,\d{2,3})+(?:\.\d+)?\s?(?:lakh crore|crore|Cr)\b)")
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


TOTAL_RE = re.compile(r"(?i)^\s*(?:grand\s+)?total\b|^\s*(?:all|overall|aggregate)\s*$")


def data_takeaway(spec: dict) -> str:
    """A takeaway from the plotted values; '' when the data cannot support one (all values equal,
    fewer than 3 distinct values)."""
    series = [s for s in (spec.get("series") or []) if isinstance(s, dict)]
    unit = str(spec.get("unit") or ("%" if "%" in str(spec.get("yLabel") or "") else ""))
    kind = str(spec.get("type") or "")
    if len(series) == 2 and kind in {"grouped_bar", "line", "bar"}:
        a, b = _points(series[0]), _points(series[1])
        if len(a) >= 3 and len(a) == len(b):
            wins = sum(1 for (_, x, _), (_, y, _) in zip(a, b) if x > y)
            gaps = [(la, x - y) for (la, x, _), (_, y, _) in zip(a, b)]
            widest = max(gaps, key=lambda g: abs(g[1]))
            unit_gap = "pp" if unit == "%" else ""
            return (f"{series[0].get('name')} beat {series[1].get('name')} in {wins} of {len(a)} periods; "
                    f"the widest gap was {widest[1]:+.2f} {unit_gap} in {widest[0]}.").replace("  ", " ")
    if len(series) == 1:
        pts = [p for p in _points(series[0]) if not TOTAL_RE.search(p[0])]  # a Total is not a component
        if len({round(p[1], 4) for p in pts}) < 3:
            return ""
        if kind in {"line", "area", "spline", "step_line"}:
            first, last = pts[0], pts[-1]
            peak = max(pts, key=lambda p: p[1])
            return (f"{_fmt(first[1], first[2], unit)} in {first[0]}, {_fmt(last[1], last[2], unit)} in {last[0]}; "
                    f"peak {_fmt(peak[1], peak[2], unit)} in {peak[0]}.")
        hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
        gap = f"; spread {hi[1] - lo[1]:.2f} pp" if unit == "%" else ""
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
    text = re.sub(r"\s+", " ", raw.replace("−", "-")).strip().casefold()
    text = re.sub(r"^(?:rs\.?\s?|₹)\s?", "₹", text)
    text = re.sub(r"\bcr$", "crore", text)
    if re.match(r"^\d", text) and "crore" in text:
        text = "₹" + text  # "11,065 crore" and "₹11,065 crore" are the same statistic
    return text


_ENTITY_RE = re.compile(r"\b(?:Nifty\s+)?[A-Z][A-Za-z&]+(?:\s+[A-Z][A-Za-z&]+){0,3}\b")
_STOP_ENTITIES = {"The", "This", "These", "That", "While", "However", "In", "As", "At", "By", "Over", "With", "From", "During",
                  "Despite", "Although", "Looking", "Meanwhile", "First", "Second", "Third", "Fourth", "Fifth", "Sixth"}


def _sections(lines: list[str]) -> list[tuple[int, int, str]]:
    """(start, end, title) of each H2 section; the text before the first H2 is section 0."""
    starts = [(i, line.lstrip("# ").strip()) for i, line in enumerate(lines) if re.match(r"^##\s+\S", line)]
    out = []
    if not starts or starts[0][0] > 0:
        out.append((0, starts[0][0] if starts else len(lines), ""))
    for k, (i, title) in enumerate(starts):
        out.append((i, starts[k + 1][0] if k + 1 < len(starts) else len(lines), title))
    return out


def _entities(sentence: str) -> set[str]:
    return {m.group(0) for m in _ENTITY_RE.finditer(_CITE_RE.sub("", sentence)) if m.group(0) not in _STOP_ENTITIES}


_GENERIC = {"growth", "return", "returns", "gain", "gains", "rate", "high", "level", "index", "sector", "sectors", "quarter",
            "year", "percent", "recorded", "reached", "posted", "approximately", "about", "around", "with", "from", "during",
            "while", "which", "this", "that", "their", "remained", "stood", "hitting", "rising", "increase", "nifty", "same",
            "window", "period", "against", "compared", "over", "into", "after", "before", "since", "than", "also", "only"}


_ALIASES = {"telecommunication": "telecom", "information technology": "it", "pharmaceutical": "pharma",
            "automobile": "auto", "metals & mining": "metal", "real estate": "realty", "psu banks": "psu bank",
            "financial service": "financial services", "fast moving consumer good": "fmcg"}


def _context(sentence: str, start: int, end: int) -> set[str]:
    before = re.findall(r"[a-z]{4,}", sentence[:start].casefold())[-5:]
    after = re.findall(r"[a-z]{4,}", sentence[end:].casefold())[:3]
    words = {w for w in before + after if w not in _GENERIC}
    # the entity named right before the number ("Nifty 50 declined 5.2%", "NIFTY FMCG (-26.33%)")
    lo = max(0, start - 80)
    near = []
    for m in re.finditer(r"\b[A-Z][A-Za-z&]+(?:\s+(?:[A-Z][A-Za-z&]+|\d{2,3}\b))*", sentence[lo:start]):
        name = m.group(0)
        at_sentence_start = lo + m.start() == 0 or sentence[:lo + m.start()].rstrip().endswith((".", "*"))
        if name in _STOP_ENTITIES or (at_sentence_start and " " not in name) or re.fullmatch(r"(?:YoY|QTD|FY\d+|Q[1-4])", name):
            continue
        near.append(name)
    if near:
        name = re.sub(r"s$", "", re.sub(r"\s+", " ", near[-1]).casefold())
        name = _ALIASES.get(re.sub(r"^nifty ", "", name), re.sub(r"^nifty ", "", name))
        words.add("@" + name)
    return words


class _Facts:
    """Mentions of one statistic are the same fact when their context words overlap."""

    def __init__(self):
        self.clusters: dict[str, list[dict]] = {}

    def key(self, stat: str, context: set[str]) -> tuple[str, int]:
        """Same fact: shared context words, and not two different entities named right before the number."""
        clusters = self.clusters.setdefault(stat, [])
        ents = {w for w in context if w.startswith("@")}
        words = context - ents
        for i, c in enumerate(clusters):
            if ents and c["ents"]:
                if ents & c["ents"]:  # the same number for the same entity: the same fact
                    c["words"] |= words
                    return stat, i
                continue
            if c["words"] & words or (not words and not c["words"]):
                c["words"] |= words
                c["ents"] |= ents
                return stat, i
        clusters.append({"words": set(words), "ents": set(ents)})
        return stat, len(clusters) - 1


def _sentences(line: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+(?=[A-Z\[(*\"'])", line)


def _owns(title: str, sentence: str, pos: int | None = None) -> bool:
    """A section whose title names an entity of the sentence (a deep-dive) keeps that entity's numbers.
    The entity is the whole title phrase ("Capital Goods", not "Capital"), next to the number."""
    head = re.split(r"[:\u2014]|\s-\s", str(title or ""), maxsplit=1)[0]
    phrases = [re.sub(r"(?i)\b(?:the|sector|deep[- ]dive|nifty)\b", "", ph).strip()
               for ph in re.split(r"\s+and\s+|,\s*|\s*&\s*|\s*/\s*", head)]
    phrases = [ph for ph in phrases if len(ph) >= 2]
    window = sentence if pos is None else sentence[max(0, pos - 60):pos + 25]
    return any(re.search(rf"\b{re.escape(ph)}\b", window, flags=re.I if len(ph) > 3 else 0) for ph in phrases)


def cap_headline_stats(report: str, cap: int = 2, *, exec_extra: int = 1, max_section_cut: float = 0.1,
                       restatement: float = 0.45) -> tuple[str, list[dict]]:
    """At most ``cap`` mentions of the same precise statistic (same number, same metric context) in the
    body prose; the executive summary may add ``exec_extra``. Over the cap, in this order:
      1) a parenthetical that only restates the number is removed;
      2) a sentence that restates the first mention (token overlap >= ``restatement``) becomes a
         cross-reference "(See Section N, <title>.)" - unless it holds the section's only mention of
         one of its entities, or the section would lose more than ``max_section_cut`` of its words;
      3) otherwise the sentence stays and the number becomes "the figure given in Section N".
    Nothing else is deleted, so entity mentions and section length are kept.
    """
    lines = str(report or "").split("\n")
    sections = _sections(lines)
    numbered = [sec for sec in sections if sec[2]]
    section_no = {id(sec): i for i, sec in enumerate(numbered, 1)}
    section_of = {i: k for k, sec in enumerate(sections) for i in range(sec[0], sec[1])}
    # pre-pass: a deep-dive that owns a fact keeps its mention, so other sections get one slot fewer
    pre = _Facts()
    owned: Counter = Counter()
    for n, line in enumerate(lines):
        if not _is_prose(line):
            continue
        title = sections[section_of.get(n, 0)][2]
        for sentence in _sentences(line):
            for m in _STAT_RE.finditer(sentence):
                key = pre.key(_norm_stat(m.group(0)), _context(sentence, m.start(), m.end()))
                if _owns(title, sentence, m.start()):
                    owned[key] += 1
    facts = _Facts()
    seen: Counter = Counter()
    first_at: dict = {}
    actions: list[dict] = []
    cut_words: Counter = Counter()
    section_words = Counter({k: sum(len(lines[i].split()) for i in range(sec[0], sec[1]) if _is_prose(lines[i]))
                             for k, sec in enumerate(sections)})

    def ref_for(k_origin: int) -> tuple[int, str]:
        sec = sections[k_origin]
        return section_no.get(id(sec), 0), sec[2]

    for n, line in enumerate(lines):
        if not _is_prose(line):
            continue
        k = section_of.get(n, 0)
        title = sections[k][2]
        limit = cap + (exec_extra if re.match(r"(?i)executive summary", title) else 0)
        sentences = _sentences(line)
        out = []
        for sentence in sentences:
            mentions = [(m, facts.key(_norm_stat(m.group(0)), _context(sentence, m.start(), m.end())))
                        for m in _STAT_RE.finditer(sentence)]
            over = [(m, key) for m, key in mentions
                    if seen[key] >= (limit if _owns(title, sentence, m.start()) else max(1, limit - min(owned[key], 1)))
                    ]
            new = sentence
            if over:
                # 1) parentheticals
                for m, key in over:
                    pattern = re.compile(r"\s*\((?:[^()]{0,30}?)" + re.escape(m.group(0)) + r"(?:[^()]{0,20}?)\)")
                    new, hits = pattern.subn("", new)
                    if hits:
                        actions.append({"stat": key[0], "action": "removed a repeated parenthetical", "section": title})
                left = [(m, key) for m, key in over if m.group(0) in new]
                if left:
                    origin_k, origin_sentence, _ = first_at[left[0][1]]
                    number, origin_title = ref_for(origin_k)
                    rest = " ".join(lines[i] for i in range(sections[k][0], sections[k][1]) if i != n and _is_prose(lines[i]))
                    rest += " " + " ".join(x for x in sentences if x is not sentence)
                    lone_entity = any(e not in rest for e in _entities(new))
                    words = len(new.split())
                    overlap = (len(_tokens(new) & _tokens(origin_sentence)) / max(1, len(_tokens(new) | _tokens(origin_sentence))))
                    all_over = all(seen[key] >= limit for _, key in [(m, kk) for m, kk in mentions if m.group(0) in new])
                    if number and origin_k != k and all_over and overlap >= restatement and not lone_entity and \
                            cut_words[k] + words <= max_section_cut * max(section_words[k], 1) and \
                            not re.search(r"\b(?:Figure|Table)\s+\d", new):
                        ref = f"(See Section {number}, {origin_title}.)"
                        actions.append({"stat": left[0][1][0], "action": "replaced a restatement by a cross-reference",
                                        "section": title, "sentence": sentence.strip()[:200], "reference": ref})
                        cut_words[k] += words
                        new = ref
                    elif number or origin_k == k:
                        # 3) keep the sentence; the repeated number points back to where it is given
                        for m, key in left:
                            o_no, _ = ref_for(first_at[key][0])
                            if not o_no or _owns(title, sentence, m.start()):
                                continue
                            where = "above" if first_at[key][0] == k else f"in Section {o_no}"
                            new = re.sub(r"(?:\b(?:approximately|about|around|roughly|nearly)\s+|~\s?)?(?:[+\-−]\s?(?=[₹$\d]))?"
                                         r"(?:₹\s?)?(?:[+\-−](?=\d))?" + re.escape(m.group(0).lstrip("₹+-− ")) if m.group(0)[:1] in "₹+-−"
                                         else r"(?:\b(?:approximately|about|around|roughly|nearly)\s+|~\s?)?(?:[+\-−]\s?(?=[₹$\d]))?(?:₹\s?)?"
                                         + re.escape(m.group(0)),
                                         f"the figure given {where}", new, count=1)
                            actions.append({"stat": key[0], "action": "number replaced by a section reference",
                                            "section": title, "sentence": sentence.strip()[:200], "reference": f"Section {o_no}"})
            is_exec = limit > cap
            counted: set = set()
            for m, key in mentions:
                if m.group(0) in new:
                    if not is_exec and key not in counted:  # once per sentence; the exec summary is the extra one
                        seen[key] += 1
                        counted.add(key)
                    # references point at the first BODY mention; the executive summary is a fallback
                    if not is_exec and (key not in first_at or first_at[key][2]):
                        if key not in first_at or first_at[key][2]:
                            first_at[key] = (k, new, False)
                    elif key not in first_at:
                        first_at[key] = (k, new, True)
            if new.strip():
                out.append(new)
        merged = []
        for x in out:
            if not (merged and x.startswith("(See Section") and merged[-1] == x):
                merged.append(x)
        joined = " ".join(merged)
        lines[n] = "" if (not joined.strip() or re.match(r"^\s*(?:[-*]|\d+[.)])\s*$", joined)) else joined
    if actions:
        log.info("Repetition: headline-statistic cap applied %d time(s)", len(actions))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)), actions


def fact_counts(report: str) -> Counter:
    """Mentions per fact (statistic + metric context) in the body prose, executive summary excluded."""
    lines = str(report or "").split("\n")
    facts = _Facts()
    counts: Counter = Counter()
    for sec in _sections(lines):
        if re.match(r"(?i)executive summary", sec[2]):
            continue
        for line in lines[sec[0]:sec[1]]:
            if not _is_prose(line):
                continue
            for sentence in _sentences(line):
                keys = {facts.key(_norm_stat(m.group(0)), _context(sentence, m.start(), m.end()))
                        for m in _STAT_RE.finditer(sentence)}
                for key in keys:  # a fact counts once per sentence (a calculation may repeat its inputs)
                    counts[key] += 1
    return counts


# ── metrics ──────────────────────────────────────────────────────────────────────────────────────
def repetition_metrics(report: str, charts: list | None = None) -> dict:
    lines = str(report or "").split("\n")
    sections = _sections(lines)
    body_lines = [line for sec in sections if not re.match(r"(?i)executive summary", sec[2])
                  for line in lines[sec[0]:sec[1]] if _is_prose(line)]
    counts = Counter({f"{k[0]}#{k[1]}": v for k, v in fact_counts(report).items()})
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
    near = len(near_duplicate_sentences(report))
    return {"near_duplicate_sentences": near, "max_headline_repeats": top[1], "max_headline_stat": top[0], "stats_over_cap": sum(1 for v in counts.values() if v > 2),
            "duplicate_figure_block_sentences": dup_blocks, "figure_blocks_echoing_body": echo,
            "duplicate_tables": dup_tables, "tables": len(tables)}


def near_duplicate_sentences(report: str, threshold: float = 0.6, min_tokens: int = 10) -> list[tuple[str, str, str, str]]:
    """Sentence pairs in DIFFERENT body sections whose word sets overlap >= ``threshold`` (Jaccard):
    the same point made twice, or one template repeated across sections (cohesion metric, Stage K)."""
    lines = str(report or "").split(chr(10))
    seen: list[tuple[str, str, set]] = []
    pairs = []
    for start, end, title in _sections(lines):
        if re.match(r"(?i)executive summary", title):
            continue
        for line in lines[start:end]:
            if not _is_prose(line):
                continue
            for sentence in _sentences(line):
                tokens = _tokens(sentence)
                if len(tokens) < min_tokens:
                    continue
                for other_title, other_sentence, other_tokens in seen:
                    if other_title != title and len(tokens & other_tokens) / len(tokens | other_tokens) >= threshold:
                        pairs.append((other_title, other_sentence[:160], title, sentence[:160]))
                        break
                seen.append((title, sentence, tokens))
    return pairs


_FIG_INTRO_RE = re.compile(r"^\s*(?:As\s+)?Figure\s+(\d+)\s+(?:shows|illustrates|presents|displays|depicts|compares|tracks|plots|highlights)\b",
                           re.I)


def drop_caption_restatements(report: str, charts: list | None, threshold: float = 0.5) -> tuple[str, list[str]]:
    """A "Figure N shows ..." sentence that only restates the chart's own title (word overlap >= threshold and
    no number of its own beyond the title's) is removed: the caption already says it, and across deep-dives
    the same template sentence makes the report repetitive (Stage K cohesion)."""
    titles = {}
    for chart in charts or []:
        m = re.match(r"^\s*Figure\s+(\d+)\s*:\s*(.*)$", str(chart.get("title") or "")) if isinstance(chart, dict) else None
        if m:
            titles[int(m.group(1))] = m.group(2)
    actions: list[str] = []
    pending_ref = [""]
    lines = str(report or "").split(chr(10))
    for i, line in enumerate(lines):
        if not _is_prose(line) or "Figure" not in line:
            continue
        kept = []
        for sentence in _sentences(line):
            m = _FIG_INTRO_RE.match(sentence)
            title = titles.get(int(m.group(1))) if m else None
            if title:
                body = _CITE_RE.sub("", sentence[m.end():])
                mine = _tokens(body) - {"the", "of", "and", "to", "from", "in", "for", "a", "an", "its", "with", "between"}
                theirs = _tokens(title)
                own_numbers = set(re.findall(r"\d+(?:\.\d+)?", body)) - set(re.findall(r"\d+(?:\.\d+)?", title))
                fig = m.group(1)
                others = len(re.findall(rf"\bFigure\s+{fig}\b", report)) - 1  # another sentence still points to it
                if mine and len(mine & theirs) / len(mine) >= threshold and not own_numbers:
                    if others >= 1:
                        actions.append(f"caption restatement removed: {sentence.strip()[:120]}")
                        continue
                    # the only reference: drop the restatement and carry "(Figure N)" into the next sentence
                    later = [x for x in _sentences(line) if x is not sentence]
                    nxt = next((j for j, x in enumerate(later) if line.index(x) > line.index(sentence)), None) if later else None
                    if nxt is not None:
                        pending_ref[0] = f"Figure {fig}"
                        actions.append(f"caption restatement folded into the next sentence: {sentence.strip()[:120]}")
                        continue
            if pending_ref[0]:
                ref = pending_ref[0]
                pending_ref[0] = ""
                sentence = re.sub(r"(\s*(?:\[[^\]]+\])?\s*)([.!?])?\s*$", lambda mm: f" ({ref}){mm.group(1)}{mm.group(2) or '.'}",
                                  sentence.rstrip(), count=1)
            kept.append(sentence)
        lines[i] = " ".join(kept)
    return chr(10).join(lines), actions
