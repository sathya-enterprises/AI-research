"""Stage M: the research plan of MEASURES - what the report must quantify, for any topic.

The engine holds no domain list. One LLM call turns the question into the measures a report needs
(what is measured, for which entities, how it should be compared); code validates the shape; every
measure becomes data-seeking searches ("<measure> <entity> statistics by year 2016 2017 ..."), so the
year-by-year tables charts are made from are looked for on purpose, not found by luck.

    plan = await plan_measures(question, gateway, today=..., tier=...)
    queries = measure_queries(plan, question, today=..., tier=...)

The plan is also handed to the official-data planner (which datasets measure these things) and recorded
in the capture. When the call fails, a deterministic one-measure plan is built from the question words
(the Stage J data angle), so a report never depends on this call.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger("measure_plan")

CHART_KINDS = {"trend", "comparison", "share", "ranking"}
MAX_MEASURES = {"detailed": 4, "in_depth": 6}
MAX_QUERIES = {"detailed": 4, "in_depth": 8}

SYSTEM = ("You plan the quantitative evidence for a research report on ANY topic. You name the measurable "
          "quantities that would answer the question, as an analyst would before searching. Return JSON only.")
PROMPT = """QUESTION: {question}
TODAY: {today}
TIME WINDOW: {window}

List up to {n} of the most important MEASURES the report should show with numbers (charts and tables), most important first.
Return one measure for a narrow single-metric question; use more only when the question needs distinct dimensions.
A measure is a quantity that some publisher reports over time or across entities - e.g. "share of new car sales
that are electric", "net foreign portfolio investment flows", "measles vaccination coverage of one-year-olds",
"installed solar capacity", "number of data-centre projects announced".

Return {{"scope": "<the place / population / market the whole question is about, e.g. a country; '' if global>",
 "measures": [{{"measure": "<the quantity, plain words, no numbers>",
  "entities": ["<WHO is compared: country / state / company / programme / group>", ...],
  "breakdown": ["<categories the quantity splits into, e.g. solar / wind, or loans / bonds>", ...],
  "chart": "trend" | "comparison" | "share" | "ranking",
  "why": "<one line: which part of the question it answers>"}}]}}
Rules: 1-{n} measures when the question asks for measurable evidence; 0-5 entities each, taken from the question or the obvious peers it implies (empty when the
measure is only needed for the scope as a whole); kinds, types, segments or sources of the quantity go in
"breakdown", never in "entities"; never invent data values; prefer measures an official or primary publisher
(statistics office, regulator, industry body, international organisation, company filing) is likely to publish.
If the question explicitly names measures or units, preserve every named measure before considering related
drivers or adjacent indicators. Do not replace a requested measure with a related but different measure.
Do not add costs, coverage, prices, or other dimensions unless the question asks for them or clearly needs them."""


@dataclass
class Measure:
    measure: str
    entities: list[str] = field(default_factory=list)
    chart: str = "trend"
    why: str = ""
    breakdown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"measure": self.measure, "entities": self.entities, "breakdown": self.breakdown,
                "chart": self.chart, "why": self.why}


@dataclass
class MeasurePlan:
    measures: list[Measure]
    method: str = "llm"
    rejected: list[dict] = field(default_factory=list)
    scope: str = ""

    def to_dict(self) -> dict:
        return {"method": self.method, "scope": self.scope, "measures": [m.to_dict() for m in self.measures],
                "rejected": self.rejected}

    def brief(self) -> str:
        """One line per measure, for other planners' prompts."""
        head = f"Scope: {self.scope}\n" if self.scope else ""
        return head + "\n".join(
            f"- {m.measure}" + (f" ({', '.join(m.entities)})" if m.entities else "")
            + (f" by {' / '.join(m.breakdown)}" if m.breakdown else "") for m in self.measures)


def _clean(text: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip(" .;:-")[:limit]


_MEASURE_CUE_RE = re.compile(
    r"(?i)(?:%|\bpercent(?:age)?\b|\bper\s+\d[\d,]*(?:\.\d+)?\b|\bper\s+cent\b|\bper[- ]capita\b|"
    r"\b(?:rates?|ratios?|shares?|counts?|numbers?|volumes?|values?|amounts?|levels?|usage|adoption|growth|"
    r"production|consumption|capacity|output|revenue|costs?|prices?|income|profits?|coverage|incidence|"
    r"prevalence|frequency|mortality|emissions?|enrol(?:l)?ment|flows?|sales|exports?|imports?|transactions?|"
    r"payments?|cases?|deaths?|births?|spending|expenditure|intensity|availability|density|distance|weight|"
    r"yield|population|averages?|means?|medians?|indices|indexes|scores?|duration|latency|throughput|"
    r"participation|attendance|investment|applications?)\b)"
)
_PRECISE_MEASURE_RE = re.compile(
    r"(?i)(?:%|\bpercent(?:age)?\b|\bper\s+\d[\d,]*(?:\.\d+)?\b|\bper[- ]capita\b|"
    r"\b(?:rates?|ratios?|shares?|counts?|numbers?|volumes?|values?|amounts?|population|incidence|"
    r"prevalence|mortality|emissions?|duration|latency|throughput|scores?)\b)"
)
_MEASURE_ACTION_RE = re.compile(
    r"(?i)^\s*(?:please\s+)?(?:compare|show|track|measure|quantify|estimate|analy[sz]e|assess|examine|"
    r"report|identify|list|give|provide|calculate|retrieve|extract|collect|find|rank|what\s+(?:is|are|was|were)|"
    r"how\s+(?:many|much|has|have|did|does|do|is|are|was|were))\b(?:\s+me|\s+the|\s+these)?\s*"
)
_MEASURE_SCOPE_RE = re.compile(
    r"(?i)\b(?:from|since|during|over|through|until|till)\s+(?:the\s+)?"
    r"(?:(?:19|20)\d{2}|\d+\s+(?:years?|months?|quarters?|weeks?)\b|the\s+(?:last|past)\b)|"
    r"\b(?:in|across|among|between|for)\s+(?:the\s+)?(?=(?-i:[A-Z]))|"
    r"\b(?:across|among|between)\s+(?:(?:all|these|the)\s+)?(?:countries|markets|regions|groups|entities|"
    r"states|cities|companies|firms|populations)\b"
)
_MEASURE_ACTION_TAIL_RE = re.compile(
    r"(?i)\s+\b(?:changed?|evolved?|grew|increased?|decreased?|declined?|rose|fell|varied|differed|"
    r"break\s+down|trended)\b.*$"
)


def _request_text(question: str) -> str:
    """Prefer the literal user request when the planner prompt also carries a resolved topic."""
    text = str(question or "")
    marker = re.search(r"(?im)^\s*Current request\s*:\s*", text)
    return text[marker.end():].strip() if marker else text.strip()


def _split_measure_parts(text: str) -> list[str]:
    """Split a measure list at top-level conjunctions, preserving parenthetical definitions."""
    parts: list[str] = []
    start = index = depth = 0
    value = str(text or "")
    while index < len(value):
        char = value[index]
        if char in "(\u005b{":
            depth += 1
            index += 1
            continue
        if char in ")\u005d}":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0 and char in ",;" and not (
            char == "," and index > 0 and index + 1 < len(value)
            and value[index - 1].isdigit() and value[index + 1].isdigit()
        ):
            parts.append(value[start:index].strip())
            start = index + 1
            index += 1
            continue
        if depth == 0:
            match = re.match(r"(?i)(?:and|or|versus|vs\.?)\b", value[index:])
            if match and (index == 0 or not value[index - 1].isalnum()) and (
                index + match.end() == len(value) or not value[index + match.end()].isalnum()
            ):
                parts.append(value[start:index].strip())
                start = index + match.end()
                index = start
                continue
        index += 1
    parts.append(value[start:].strip())

    # Keep a coordinated phrase together when only one half contains its measurement word:
    # "research and development spending" and "usage for women and men" remain one measure.
    merged: list[str] = []
    pending: list[str] = []
    for part in parts:
        if not part:
            continue
        if not _MEASURE_CUE_RE.search(part):
            if merged and _MEASURE_CUE_RE.search(merged[-1]):
                merged[-1] = f"{merged[-1]} and {part}"
            else:
                pending.append(part)
            continue
        if pending:
            part = " and ".join(pending + [part])
            pending.clear()
        merged.append(part)
    return merged


def _explicit_measure_phrases(question: str) -> list[str]:
    """Extract literal quantitative dimensions from explicit metric requests, without topic-specific rules."""
    text = _request_text(question)
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])|[;\n]+", text)
    found: list[str] = []
    for sentence in sentences:
        raw = re.sub(r"\s+", " ", sentence).strip(" \t\r\n.,;:")
        if not raw:
            continue
        count_question = bool(re.match(r"(?i)^\s*how\s+(?:many|much)\b", raw))
        action = _MEASURE_ACTION_RE.match(raw)
        value = raw[action.end():] if action else raw
        # A phrase such as "official statistics and source methodology" is context, not a requested measure.
        has_unit = bool(_PRECISE_MEASURE_RE.search(value))
        if not action and not has_unit and not count_question:
            continue
        scope = _MEASURE_SCOPE_RE.search(value)
        if scope:
            value = value[:scope.start()]
        value = _MEASURE_ACTION_TAIL_RE.sub("", value).strip(" \t\r\n.,;:")
        value = re.sub(r"(?i)^(?:countries|companies|firms|groups|regions)\s+by\s+", "", value)
        value = re.sub(r"(?i)^(?:the|a|an)\s+", "", value).strip(" \t\r\n.,;:")
        for part in _split_measure_parts(value):
            cleaned = _clean(part, 90)
            # Acronyms are accepted only as a complete requested list item; prose acronyms
            # such as RBI in a source-preference clause are not treated as measures.
            acronym_only = bool(re.fullmatch(r"[A-Z][A-Z0-9]{1,7}(?:/[A-Z][A-Z0-9]{1,7})*", cleaned))
            if len(cleaned) < 3 or (not _MEASURE_CUE_RE.search(cleaned) and not acronym_only):
                continue
            if cleaned.casefold() not in {item.casefold() for item in found}:
                found.append(cleaned)
    return found


def _measure_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z0-9]*", str(text or "").casefold())
    stop = {"the", "and", "or", "of", "in", "on", "for", "to", "by", "with", "at", "a", "an", "as", "from"}
    normalized = set()
    for word in words:
        if word in stop:
            continue
        if word in {"using", "used"}:
            word = "use"
        elif word.endswith("ies") and len(word) > 5:
            word = word[:-3] + "y"
        elif word.endswith("s") and len(word) > 4:
            word = word[:-1]
        normalized.add(word)
    return normalized


def _measure_match_score(requested: str, planned: str) -> float:
    requested_words = _measure_tokens(requested)
    planned_words = _measure_tokens(planned)
    if not requested_words or not planned_words:
        return 0.0
    overlap = len(requested_words & planned_words)
    if len(requested_words) == 1:
        return 1.0 if overlap else 0.0
    if overlap < 2:
        return 0.0
    return overlap / len(requested_words)


def _requested_chart(question: str) -> str | None:
    text = _request_text(question)
    if re.search(r"(?i)\b(?:rank|ranking|ranked|top\s+\d*|bottom\s+\d*|highest|lowest|largest|smallest)\b", text):
        return "ranking"
    if re.search(r"(?i)\b(?:trend|over\s+time|year[- ]by[- ]year|annual|yearly|quarterly|monthly|since\s+(?:19|20)\d{2})\b", text):
        return "trend"
    if re.search(r"(?i)\b(?:share|proportion|percentage|percent|composition|mix|breakdown|distribution)\b", text):
        return "share"
    if re.search(r"(?i)\b(?:compare|comparison|versus|vs\.?)\b", text):
        return "comparison"
    return None


def _explicit_measures_are_authoritative(explicit: list[str]) -> bool:
    # One broad topic such as "EV adoption" still benefits from the analyst's
    # operational definition. Reconcile when the user named multiple dimensions
    # or specified a precise unit/measure label.
    return len(explicit) >= 2 or any(_PRECISE_MEASURE_RE.search(item) for item in explicit)


def _reconcile_explicit_measures(plan: MeasurePlan, question: str, *, tier: str) -> MeasurePlan:
    """Make clearly named measures authoritative while retaining LLM scope and matching metadata."""
    explicit = _explicit_measure_phrases(question)
    if not explicit:
        return plan
    if not _explicit_measures_are_authoritative(explicit):
        return plan

    cap = MAX_MEASURES.get(tier, 4)
    previous = list(plan.measures)
    shared_entities: list[str] = []
    entity_lists = [list(dict.fromkeys(m.entities or [])) for m in previous if m.entities]
    if entity_lists:
        common = set(entity_lists[0])
        for items in entity_lists[1:]:
            common.intersection_update(items)
        shared_entities = [name for name in entity_lists[0] if name in common]
        if not shared_entities and len({tuple(sorted(items, key=str.casefold)) for items in entity_lists}) == 1:
            shared_entities = entity_lists[0]

    requested_chart = _requested_chart(question)
    fallback = fallback_plan(question, tier=tier)
    fallback_chart = fallback.measures[0].chart if fallback.measures else "comparison"
    reconciled: list[Measure] = []
    matched_indices: set[int] = set()
    for label in explicit[:cap]:
        ranked = sorted(
            ((_measure_match_score(label, candidate.measure), index, candidate)
             for index, candidate in enumerate(previous) if index not in matched_indices),
            key=lambda entry: (entry[0], -entry[1]), reverse=True,
        )
        score, index, matched = ranked[0] if ranked else (0.0, -1, None)
        if score < 0.55:
            matched = None
        else:
            matched_indices.add(index)
        reconciled.append(Measure(
            measure=label,
            entities=list(matched.entities if matched and matched.entities else shared_entities),
            breakdown=list(matched.breakdown if matched else []),
            chart=requested_chart or (matched.chart if matched else fallback_chart),
            why=(matched.why if matched and matched.why else "Explicitly requested measure"),
        ))

    for index, candidate in enumerate(previous):
        if index not in matched_indices:
            plan.rejected.append({"measure": candidate.measure, "reason": "not aligned with explicitly requested measures"})
    for label in explicit[cap:]:
        plan.rejected.append({"measure": label, "reason": f"exceeds the {tier} measure budget ({cap})"})
    plan.measures = reconciled
    return plan


def validate(data: object, *, tier: str, question: str = "") -> MeasurePlan:
    """Keep only well-formed measures (shape checks; nothing about any domain)."""
    plan = MeasurePlan(measures=[])
    items = data.get("measures") if isinstance(data, dict) else None
    plan.scope = _clean(data.get("scope"), 60) if isinstance(data, dict) else ""
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        measure = _clean(item.get("measure"), 90)
        numeric_values = re.sub(r"(?i)\bper\s+\d[\d,]*(?:\.\d+)?\b", "", measure)
        if len(measure) < 6 or re.search(r"\d{2,}", numeric_values.replace("2.5", "")) and not re.search(r"(?i)pm\s?2\.5|co2|5g|4g", measure):
            plan.rejected.append({"measure": measure, "reason": "empty, too short or carries numbers"})
            continue
        if measure.casefold() in seen:
            continue
        seen.add(measure.casefold())
        breakdown = [_clean(b, 50) for b in _as_list(item.get("breakdown")) if _clean(b, 50)][:6]
        entities = []
        for e in _as_list(item.get("entities")):
            e = _clean(e, 50)
            if not e or e.casefold() == plan.scope.casefold():
                continue  # the scope is in every query already
            if _is_breakdown(e, measure):
                breakdown.append(e)  # "Solar Power" under "capacity for solar and wind" is a kind, not a who
                continue
            entities.append(e)
        chart = str(item.get("chart") or "trend").strip().casefold()
        plan.measures.append(Measure(measure=measure, entities=list(dict.fromkeys(entities))[:5],
                                     breakdown=list(dict.fromkeys(breakdown))[:6],
                                     chart=chart if chart in CHART_KINDS else "trend", why=_clean(item.get("why"), 160)))
    plan.measures = plan.measures[: MAX_MEASURES.get(tier, 4)]
    return _reconcile_explicit_measures(plan, question, tier=tier) if question else plan


def _as_list(value) -> list:
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, str) and value.strip() else []


def _is_breakdown(entity: str, measure: str) -> bool:
    """An 'entity' made only of the measure's own words is a kind of the measure ("Solar Power" for
    "installed capacity for solar and wind"), not an entity measured. Whole words only, so a company
    named "ReNew" or "Tata Power Renewable Energy" stays an entity."""
    words = [w for w in re.findall(r"[a-z]{3,}", entity.casefold()) if w not in {"the", "and", "power", "total"}]
    measure_words = set(re.findall(r"[a-z]{3,}", measure.casefold()))
    return 0 < len(words) <= 2 and all(w in measure_words or w.rstrip("s") in measure_words for w in words)


def fallback_plan(question: str, *, tier: str = "detailed") -> MeasurePlan:
    from utils.data_angles import subject_terms
    terms = subject_terms(question)
    text = str(question or "")
    if re.search(r"(?i)\b(rank(?:ing|ed)?|top\s+\d*|bottom\s+\d*|highest|lowest|largest|smallest|leading|biggest|best|worst)\b|\bmost\s+(?!recent|current|likely|important|relevant|useful|comprehensive|detailed)\w+", text):
        chart = "ranking"
    elif re.search(r"(?i)\b(trend|growth|change|evolv\w*|over\s+time|year[- ]by[- ]year|annual|since\s+(?:19|20)\d{2}|past\s+\d+\s+years?)\b", text):
        chart = "trend"
    elif re.search(r"(?i)\b(share|proportion|percentage|percent|composition|mix|breakdown|distribution)\b", text):
        chart = "share"
    elif re.search(r"(?i)\b(compare|comparison|versus|vs\.?|against|between|across)\b", text):
        chart = "comparison"
    else:
        # A single current-value request should not be forced into a time-series slot.
        chart = "comparison"
    explicit = _explicit_measure_phrases(question)
    if _explicit_measures_are_authoritative(explicit):
        requested_chart = _requested_chart(question) or chart
        return MeasurePlan(
            measures=[Measure(measure=label, chart=requested_chart, why="Explicitly requested measure")
                      for label in explicit[:MAX_MEASURES.get(tier, 4)]],
            method="keywords (explicit request)",
            rejected=[{"measure": label, "reason": f"exceeds the {tier} measure budget ({MAX_MEASURES.get(tier, 4)})"}
                      for label in explicit[MAX_MEASURES.get(tier, 4):]],
        )
    # A short single-metric request such as "inflation" or "GDP" still has a
    # useful quantitative subject. data_angles keeps requiring enough context
    # for its broader fallback, but this plan can search the named measure.
    plan = MeasurePlan(measures=[Measure(measure=" ".join(terms), chart=chart)] if terms else [],
                       method="keywords (planner unavailable)")
    return plan


async def plan_measures(question: str, gateway, *, today: date, tier: str) -> MeasurePlan:
    """Never raises; falls back to a keyword plan."""
    n = MAX_MEASURES.get(tier, 4)
    try:
        from utils.data_angles import window_years
        years = window_years(question, today)
        window = f"{years[0]}-{years[-1]}" if years else "the period the question asks about"
    except Exception:
        window = "the period the question asks about"
    if gateway is not None:
        try:
            result = await gateway.generate(purpose="measure_plan", section_id="plan", system=SYSTEM,
                                            prompt=PROMPT.format(question=str(question)[:2000], today=today.isoformat(),
                                                                 window=window, n=n),
                                            max_tokens=1200, temperature=0.0, json_mode=True, timeout=60, retries=1)
            data = result.json() if getattr(result, "ok", False) else None
            plan = validate(data, tier=tier, question=question)
            if plan.measures:
                log.info("Measure plan: %s", "; ".join(m.measure for m in plan.measures))
                return plan
        except Exception as exc:  # the plan must never abort a report
            log.warning("Measure plan failed: %s", type(exc).__name__)
    return fallback_plan(question, tier=tier)


def measure_queries(plan: MeasurePlan, question: str, *, today: date, tier: str, existing: list[str] | None = None) -> list[str]:
    """Data-seeking searches: per measure, one statistics query (by entity when there are entities) and,
    for in-depth reports, one table / report query; capped per tier."""
    try:
        from utils.data_angles import window_years
        years = window_years(question, today)
    except Exception:
        years = []
    span = f"{years[0]} to {years[-1]}" if len(years) >= 2 else ""
    explicit_window = bool(re.search(
        r"(?i)\b(?:19|20)\d{2}\b|\b(?:past|last|since|from|between|during|annual|yearly|year-wise|quarterly|monthly|FY|Q[1-4])\b|over\s+time",
        question or "",
    ))
    requested_span = span if explicit_window else ""
    out: list[str] = []
    scope = plan.scope if plan.scope else ""

    def where(m: Measure) -> str:  # the scope unless the measure or its entities already say it
        said = f"{m.measure} {' '.join(m.entities)}".casefold()
        scope_words = " " + " ".join(re.findall(r"[a-z0-9]+", scope.casefold())) + " "
        entities_in_scope = bool(m.entities) and all(
            " " + " ".join(re.findall(r"[a-z0-9]+", entity.casefold())) + " " in scope_words
            for entity in m.entities[:5]
        )
        return scope if scope and scope.casefold() not in said and not entities_in_scope else ""

    def search_shape(m: Measure) -> str:
        subject = " ".join(filter(None, (where(m), *m.entities[:5])))
        metric = " ".join(filter(None, (m.measure, subject)))
        if m.breakdown:
            metric += " " + " ".join(m.breakdown[:3])
        if m.chart == "trend":
            return f"{metric} annual time series by year {span} official data".strip()
        if m.chart == "share":
            return f"{metric} percentage share composition breakdown {requested_span or 'latest available data'} table".strip()
        if m.chart == "ranking":
            return f"{metric} ranked values ranking {requested_span or 'latest available year'} official table".strip()
        return f"{metric} comparison across entities {requested_span or 'latest available year'} official statistics table".strip()

    for m in plan.measures:  # every measure gets its most relevant evidence shape before a second query
        out.append(search_shape(m))
    if tier == "in_depth":
        for m in plan.measures:
            who = " ".join([where(m), *m.entities[:5]]).strip()
            metric = " ".join(filter(None, (m.measure, who, *m.breakdown[:3])))
            out.append(f"{metric} downloadable data CSV XLSX official publisher table report {span}".strip())
    have = {q.casefold() for q in existing or []}
    deduped = list(dict.fromkeys(q for q in (re.sub(r"\s+", " ", x) for x in out) if q.casefold() not in have))
    return deduped[: MAX_QUERIES.get(tier, 4)]
