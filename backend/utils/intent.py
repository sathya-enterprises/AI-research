"""
Generic request-intent resolver
================================
Single decision point for the questions that used to be answered by a pile
of topic-specific regexes scattered through routes/report.py ("is this a
market-news digest?", "is this a stock question?", "is this a tax
question?", ...). Each of those was a hard-coded branch for ONE kind of
request; a brand-new kind of question (e.g. "what are analysts saying about
the RBI's rate decision") matched none of them and fell back to generic,
undifferentiated handling no matter how well the branch pattern worked for
the cases it *did* cover.

This module asks the model instead: for ANY request, in ANY domain —

  1. what is the user actually asking (a short free-text intent label),
  2. what topic/entity does it refer to, resolving pronouns/follow-ups
     ("give it as a report", "make that a report", "analyze this") against
     the recent conversation,
  3. whether it's a follow-up at all,
  4. which KINDS of evidence would actually help answer it, drawn from a
     small controlled vocabulary (news, expert/analyst opinion, financials,
     market data, regulatory information, historical data, comparisons,
     uploaded documents, or none — general knowledge is enough), and
  5. whether the user wants a formal report vs. a quick answer.

Nothing here is specific to finance, or to news, or to any named company —
new question types are handled by the SAME call deciding a different
`evidence_needed` set, not by adding another branch to this file.

Callers (chat orchestrator, report pipeline) use `evidence_needed` to decide
which tools/searches to run and `resolved_topic`/`is_followup` to ground a
short follow-up before researching. Nothing downstream is required to use
every field — e.g. a report request only cares about evidence types that
map to a web search angle, chat may only care about resolved_topic.

Degrades gracefully: if no Groq key is configured, every key is rate
limited, or the model returns something unparseable, `resolve_request_intent`
falls back to `_heuristic_intent` — a deterministic, keyword-based, still
domain-neutral guess — and marks `source="heuristic"` so callers/logs can
tell the difference. It never raises and never returns None.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from utils.keys import get_groq_keys, is_rate_limited, mark_rate_limited, round_robin

log = logging.getLogger("intent")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Small/fast model — this is one short JSON completion, not a report body.
_INTENT_MODEL = "openai/gpt-oss-20b"

# Controlled vocabulary of evidence *kinds*, not topics. New question types
# are handled by the model picking a different subset of these — never by
# adding a new entry here per-topic.
EVIDENCE_TYPES = [
    "news",             # recent headlines / current events
    "expert_opinion",   # analyst/expert commentary, ratings, opinions
    "financials",       # company/entity financial figures, fundamentals
    "market_data",      # live prices, index levels, OHLC, market moves
    "regulatory",       # laws, policy, compliance, regulator actions
    "historical",       # trends / time series / past performance
    "comparison",       # explicit comparison across named items
    "documents",        # the user's own uploaded file(s) / indexed docs
    "general_knowledge",  # no retrieval needed — model knowledge suffices
]

_VALID_EVIDENCE = set(EVIDENCE_TYPES)


@dataclass
class RequestIntent:
    resolved_topic: str = ""
    is_followup: bool = False
    intent_label: str = ""
    evidence_needed: list[str] = field(default_factory=lambda: ["general_knowledge"])
    wants_report: bool = False
    notes: str = ""
    source: str = "heuristic"  # "llm" | "heuristic" | "none"

    def needs(self, evidence_type: str) -> bool:
        return evidence_type in self.evidence_needed

    def as_meta(self) -> dict:
        return {
            "resolvedTopic": self.resolved_topic,
            "isFollowup": self.is_followup,
            "intent": self.intent_label,
            "evidenceNeeded": self.evidence_needed,
            "wantsReport": self.wants_report,
            "source": self.source,
        }


_SYSTEM_PROMPT = f"""You are the intent-resolution stage of a general-purpose research/report assistant that must handle ANY topic — finance, business, science, technology, policy, health, sport, culture, or anything else — with no fixed list of question types.

Given the user's latest message and, if provided, the recent conversation, decide:

1. "resolved_topic": the actual subject the request is about, in a short noun phrase — NOT the user's instruction, not the full research question, and not the requested output format. Strip boilerplate such as "generate a comprehensive research report", "assess whether", "evaluate whether", "produce a full report", etc. For example, "Generate a comprehensive research report assessing whether India's FMCG industry can sustain growth through 2030" must resolve to "India's FMCG industry"; "...whether India's private-bank credit cycle can sustain..." must resolve to "India's private-bank credit cycle". If the message is a short follow-up that leans on a pronoun or has no subject of its own ("give it as a report", "make that a report", "analyze this", "what about its peers", "give me more detail"), resolve it from the conversation — name the real company/person/topic/event being discussed. If the message already names its own subject, just restate that subject; do not go looking for a different one in the conversation.
2. "is_followup": true only if you had to lean on the conversation to know what the message is about.
3. "intent_label": a short free-text label for what the user wants (e.g. "latest news", "expert/analyst commentary", "financial analysis", "comparison", "explainer", "report request", "casual conversation"). Do not pick from a fixed list — describe it in a few words.
4. "evidence_needed": pick ONLY the evidence kinds from this exact set that this SPECIFIC request genuinely needs — {sorted(_VALID_EVIDENCE)}. Be conservative and literal:
   - Include "news" only if the user is asking about recent headlines/current events/what's happening.
   - Include "expert_opinion" ONLY if the user explicitly asks for expert, analyst, or third-party opinions/commentary/ratings/views — never add it just because the topic is finance or business. A plain "latest news on X" must NOT include expert_opinion.
   - Include "financials" / "market_data" only if real figures, prices, or fundamentals matter to the answer.
   - Include "regulatory" only if laws/policy/compliance/regulators are actually relevant.
   - Include "historical" only if trend/time-series/past-performance matters.
   - Include "comparison" only if the request explicitly compares two or more named items.
   - Include "documents" only if the request refers to the user's own uploaded file/attachment.
   - If nothing above genuinely applies and your own knowledge is enough, use exactly ["general_knowledge"].
   - Never include every category "to be safe" — an unnecessary evidence type leads to sections/searches the user didn't ask for.
5. "wants_report": true if the user is asking for a report/document/write-up rather than a short conversational answer.
6. "notes": one short sentence explaining the evidence_needed choice, or empty string.

Respond with ONLY a valid JSON object, no other text. Use JSON boolean values; do not write placeholders where a boolean value belongs. Example shape:
{{"resolved_topic": "India renewable energy", "is_followup": false, "intent_label": "report request", "evidence_needed": ["historical", "regulatory"], "wants_report": true, "notes": "A forward-looking sector report needs trend and policy evidence."}}"""


def _extract_json(text: str) -> dict | None:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1], strict=False)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _coerce_intent(parsed: dict, fallback_question: str) -> RequestIntent | None:
    if not isinstance(parsed, dict):
        return None
    raw_topic = str(parsed.get("resolved_topic") or "").strip()
    # LLM output is normalized too: a malformed/over-literal model response
    # must not reintroduce report boilerplate into downstream search.
    resolved_topic = _extract_research_topic(raw_topic) if raw_topic else _extract_research_topic(fallback_question)
    resolved_topic = resolved_topic or fallback_question
    evidence = parsed.get("evidence_needed")
    if not isinstance(evidence, list):
        evidence = []
    evidence = [e for e in evidence if isinstance(e, str) and e in _VALID_EVIDENCE]
    if not evidence:
        evidence = ["general_knowledge"]
    return RequestIntent(
        resolved_topic=resolved_topic,
        is_followup=bool(parsed.get("is_followup")),
        intent_label=str(parsed.get("intent_label") or "").strip(),
        evidence_needed=evidence,
        wants_report=bool(parsed.get("wants_report")),
        notes=str(parsed.get("notes") or "").strip(),
        source="llm",
    )


# ─── Heuristic fallback ──────────────────────────────────────────────────────
# Deterministic and keyword-based, but keyed on generic EVIDENCE KINDS, not
# on any specific topic/company/domain — the same fallback shape applies
# whether the request is about a stock, a drug approval, or a sports league.
_FOLLOWUP_WORD_RE = re.compile(
    r"\b(it|its|that|this|those|these|they|them|the same|"
    r"give it|make that|turn (?:it|that|this)|analyze this|analyse this)\b",
    re.IGNORECASE,
)
_REPORT_WORD_RE = re.compile(r"\b(report|write[- ]up|document|detailed analysis|deep[- ]dive)\b", re.IGNORECASE)
_NEWS_RE = re.compile(r"\b(news|headlines?|latest|breaking|today'?s|recent(?:ly)?\s+(?:developments?|events?))\b", re.IGNORECASE)
# Deliberately NOT anchored to finance-specific titles ("expert"/"analyst")
# alone — a domain-neutral "what do <some group> think/say" shape catches
# any profession (virologists, economists, historians, engineers, ...)
# asking for third-party opinion, without a per-domain word list.
_EXPERT_RE = re.compile(
    r"\b(experts?|analysts?|commentary|management commentary|analyst views?|analyst commentary|"
    r"institutional analyst(?:s)?|investor views?|viewpoints?|opinions?|ratings?|"
    r"what\s+(?:do|does|are|is)\s+\w+(?:\s+\w+){0,2}\s+"
    r"(?:think|say|believe|expect|predict|forecast|warn|argue))\b",
    re.IGNORECASE,
)
# "margin[s]" alone is ambiguous (leveraged "margin trading" vs. profit
# "margins") — only treat it as a financials signal alongside another
# genuinely financial-figures word, not on its own.
_FINANCIALS_RE = re.compile(
    r"\b(revenue|profit|profitability|earnings|financials?|fundamentals?|valuation|p/?e ratio|balance sheet|"
    r"profit margins?|gross margins?|margin expansion|nim|net interest margin|roa|roe|return on assets|return on equity|"
    r"credit growth|deposit growth|credit-to-deposit|funding costs?|funding cost|provisioning|capital adequacy|"
    r"liquidity|borrower leverage|leverage|rental yields?|rental yield|warehouse rents?|rents?|occupancy|"
    r"capex|financing costs?|financing cost|returns?|return thresholds?|pricing power|commodity inflation|"
    r"advertising and promotion|distribution costs?|inventory)\b",
    re.IGNORECASE,
)
_MARKET_DATA_RE = re.compile(r"\b(price|prices|stock price|share price|index|market cap|nifty|sensex|ohlc|quote)\b", re.IGNORECASE)
_REGULATORY_RE = re.compile(
    r"\b(regulat\w*|complian\w*|sebi|rbi|polic(?:y|ies)|law|laws|legislation|"
    r"\bban\b|banned|banning|approval|approvals|licen[cs]e[sd]?)\b",
    re.IGNORECASE,
)
_HISTORICAL_RE = re.compile(r"\b(trend|historical|history|over time|past \d+|since \d{4}|quarter[- ]on[- ]quarter|year[- ]on[- ]year|yoy|qoq|outlook|forecast|projection|growth trajectory|by 20\d{2}|through 20\d{2}|20\d{2}[–-]\d{2}|20\d{2}\s*[-–]\s*20\d{2})\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"\bvs\.?\b|\bversus\b|\bcompar", re.IGNORECASE)
_SCIENTIFIC_RE = re.compile(
    r"\b(scientific|science|virolog(?:y|ical)|virus|viral|pathogen|influenza|h5n1|h5n6|h7n7|"
    r"clinical|epidemiolog(?:y|ical)|genomic|genomics|transmission|vaccine|vaccines|antiviral|"
    r"surveillance|study|studies|trial|trials|case(?:s)?|mortality|immunity|infectious|zoonotic)\b",
    re.IGNORECASE,
)


def _clean_research_topic_text(text: str) -> str:
    """Normalize a candidate topic while removing report-writing boilerplate.

    This is intentionally generic: it does not contain finance/company/topic
    names.  The goal is to prevent instruction phrases such as "Generate a
    comprehensive research report assessing whether" from becoming the
    resolved research subject when the LLM intent call is unavailable.
    """
    text = re.sub(r"\s+", " ", (text or "")).strip(" \t\r\n.,;:")
    if not text:
        return ""

    # Common report-request lead-ins.  Keep this deliberately broad and
    # domain-neutral so the same fallback works for finance, science,
    # technology, policy, etc.
    text = re.sub(
        r"^(?:please\s+)?(?:generate|create|prepare|produce|provide|write|make|build|draft|conduct|perform|carry out|give me)\s+"
        r"(?:(?:a|an|the)\s+)?"
        r"(?:(?:fully|well|properly)\s+)?"
        r"(?:(?:brief|executive|short|detailed|comprehensive|in-depth|deep-dive|deep|research)\s+)*"
        r"(?:research\s+)?(?:report|analysis|study|review|breakdown|overview)\b"
        r"(?:\s+(?:assessing|evaluating|examining|determining|analyzing|analysing|on|about|of|for|regarding))?\s*",
        "", text, flags=re.IGNORECASE,
    )

    # Report-depth/output words are control metadata, never research entities.
    # Keep this generic so phrases such as "Create a Brief research report on X"
    # resolve to X rather than sending "Brief" to market/entity search.
    text = re.sub(
        r"\b(?:brief|executive|short|detailed|comprehensive|in[- ]depth|deep[- ]research|deep[- ]dive|research)\s+(?=(?:research\s+)?(?:report|analysis|study|review)\b)",
        "", text, flags=re.IGNORECASE,
    )

    # For imperative report prompts such as "conduct a report on X. Analyze Y",
    # the first sentence after the boilerplate normally names the actual subject.
    # Keep later analytical instructions out of the resolved topic.
    if "." in text or "\n" in text:
        text = re.split(r"[.\n]", text, maxsplit=1)[0].strip()

    # The most important pattern for the user's current report prompts:
    # "... assessing whether <SUBJECT> can/is/will ...".  Capture only
    # the noun phrase before the predicate rather than the entire research
    # question.
    m = re.search(
        r"(?:assess(?:ing)?|evaluat(?:e|ing)|examin(?:e|ing)|determin(?:e|ing)|analyz(?:e|ing)|analys(?:e|ing))"
        r"\s+(?:the\s+)?(?:question\s+of\s+)?(?:whether|if)\s+(.+)$",
        text, flags=re.IGNORECASE,
    )
    if m:
        text = m.group(1).strip()

    # If a plain "whether <subject> ..." survived the first pass, handle
    # it as well.
    text = re.sub(r"^(?:whether|if)\s+", "", text, flags=re.IGNORECASE).strip()

    # Extract the subject noun phrase from "whether X can/is/will ...".
    # Predicate words are intentionally generic.  The first one after
    # "whether" is used as the boundary; if no predicate is found we keep
    # the whole clause rather than guessing.
    predicate = re.search(
        r"\s+(?=(?:can|could|will|would|should|may|might|must|is|are|was|were|has|have|had|remains?|remain|continues?|continue|faces?|face|offers?|offer|supports?|support|sustains?|sustain|generates?|generate)\b)",
        text, flags=re.IGNORECASE,
    )
    if predicate:
        candidate = text[:predicate.start()].strip(" \t,;:-")
        if len(candidate.split()) >= 2:
            text = candidate

    # Remove trailing request-only clauses when they remain attached to a
    # topic.  These are not part of the subject used for search/relevance.
    text = re.split(
        r"\s+(?:through|by|over|for)\s+(?:FY)?\d{4}\b", text, maxsplit=1, flags=re.IGNORECASE
    )[0].strip()
    text = re.sub(r"\s+\b(?:and|while|with|considering|including)\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" \t\r\n.,;:")


def is_search_audit_request(question: str, explicit_mode: str | None = None) -> bool:
    """Detect an explicit search-only/source-audit request, not a normal report ask."""
    mode = str(explicit_mode or "").strip().casefold().replace("-", "_")
    if mode in {"search_audit", "search_only", "source_audit", "search_test"}:
        return True
    q = str(question or "")
    has_audit_marker = bool(re.search(
        r"\b(?:search[- ]layer\s+test|search[- ]only\s+test|source\s+audit|search\s+audit)\b",
        q, re.IGNORECASE,
    ))
    says_no_report = bool(re.search(
        r"\b(?:do not|don't|dont|without)\s+(?:generate|write|create|produce)\s+(?:a\s+)?(?:normal\s+)?research\s+report\b|\bnot a report[- ]generation request\b",
        q, re.IGNORECASE,
    ))
    return has_audit_marker and says_no_report


def _extract_search_audit_topic(question: str) -> str:
    """Extract the actual research subject from the user's longer audit instructions."""
    raw = str(question or "").replace("\r", "\n")
    patterns = [
        r"(?:search(?:\s+the\s+web)?\s+for|find)\s+(?:(?:current|recent|credible|relevant|official|verifiable|publicly\s+available)[,\s]+)*(?:information|sources|details|results)\s+(?:about|on|for)\s+(.+?)(?=[\n.;]|$)",
        r"(?:search(?:\s+the\s+web)?\s+for|find)\s+(?:(?:current|recent|credible|relevant|official|verifiable|publicly\s+available)[,\s]+)*(.+?)(?=[\n.;]|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            topic = re.sub(r"\s+", " ", match.group(1)).strip(" *#\t,;:-")
            if topic:
                return topic
    for line in (line.strip() for line in raw.splitlines() if line.strip()):
        if re.search(r"search[- ]layer\s+test|source\s+audit|search\s+audit", line, re.I):
            continue
        cleaned = re.sub(r"^\*{1,2}|\*{1,2}$|^#+\s*", "", line).strip()
        if cleaned and not re.match(r"^(?:run \d+|do not|don't|return|only include|at the end|important:)", cleaned, re.I):
            return cleaned.rstrip(".;")
    return ""


def _extract_research_topic(question: str) -> str:
    """Return the actual subject of a research request, not its task wording."""
    raw = str(question or "")
    if is_search_audit_request(raw):
        audit_topic = _extract_search_audit_topic(raw)
        if audit_topic:
            return audit_topic
    q = re.sub(r"\s+", " ", raw).strip()
    if not q:
        return ""

    # Exact high-value shape: "... assessing whether <subject> <predicate> ...".
    m = re.search(
        r"(?:assess(?:ing)?|evaluat(?:e|ing)|examin(?:e|ing)|determin(?:e|ing)|analyz(?:e|ing)|analys(?:e|ing))"
        r"\s+(?:the\s+)?(?:question\s+of\s+)?(?:whether|if)\s+(.+?)(?=\s+(?:can|could|will|would|should|may|might|must|is|are|was|were|has|have|had|remains?|remain|continues?|continue|faces?|face|offers?|offer|supports?|support|sustains?|sustain|generates?|generate)\b)",
        q, flags=re.IGNORECASE,
    )
    if m:
        topic = m.group(1).strip(" \t,;:-")
        if topic:
            return topic

    cleaned = _clean_research_topic_text(q)
    if cleaned:
        # For a simple "report on X" request, strip the preposition but
        # preserve the actual subject.
        cleaned = re.sub(
            r"^(?:on|about|regarding|concerning|into)\s+", "", cleaned, flags=re.IGNORECASE
        ).strip()
        if cleaned:
            return cleaned
    return q


def _heuristic_intent(
    question: str, conversation_context: str, has_rag: bool, has_files: bool,
) -> RequestIntent:
    q = question or ""
    is_followup = bool(conversation_context.strip()) and (
        len(q.split()) <= 8 and bool(_FOLLOWUP_WORD_RE.search(q))
    )
    # Critical fallback behavior: when Groq is unavailable, resolve the
    # subject from the research instruction instead of using the first N
    # characters of the raw prompt.  The previous behavior turned phrases
    # such as "Generate a comprehensive research report assessing whether"
    # into the search topic, which contaminated Tavily and market-entity
    # resolution.
    resolved_topic = _extract_research_topic(q) or q
    last_assistant = ""
    if is_followup:
        # Same lightweight grounding technique used elsewhere in the
        # pipeline: pull a short topic phrase out of the last assistant
        # turn rather than leaving the follow-up subject-less.
        turns = [t.strip() for t in conversation_context.split("\n\n") if t.strip()]
        for t in reversed(turns):
            if t.startswith("Assistant:"):
                last_assistant = t[len("Assistant:"):].strip()
                break
        if last_assistant:
            first_sentence = re.split(r"[.\n]", last_assistant, maxsplit=1)[0].strip()
            # Truncate on a word boundary, not mid-word — a hard [:80] slice
            # can cut a query-bound phrase in half (e.g. "...its c").
            topic_phrase = first_sentence[:80]
            if len(first_sentence) > 80:
                topic_phrase = topic_phrase.rsplit(" ", 1)[0]
            if topic_phrase:
                resolved_topic = f"{q} — {topic_phrase}" if q.lower() not in topic_phrase.lower() else topic_phrase

    # Scan for evidence signals over the FULL last answer (capped, but much
    # more than the short topic_phrase used for the search-query string
    # above) — a follow-up like "turn this into a report" carries no
    # evidence signal of its own, and the keyword that reveals what kind of
    # evidence the follow-up still needs (e.g. "news", "virologists...
    # think") often sits later in the prior answer than the first ~80
    # characters, which is all resolved_topic keeps for query brevity.
    scan_text = f"{q} {last_assistant[:800]}" if is_followup else q

    evidence: list[str] = []
    if _NEWS_RE.search(scan_text):
        evidence.append("news")
    if _EXPERT_RE.search(scan_text):
        evidence.append("expert_opinion")
    if _FINANCIALS_RE.search(scan_text):
        evidence.append("financials")
    if _MARKET_DATA_RE.search(scan_text):
        evidence.append("market_data")
    if _REGULATORY_RE.search(scan_text):
        evidence.append("regulatory")
    if _HISTORICAL_RE.search(scan_text):
        evidence.append("historical")
    if _COMPARISON_RE.search(scan_text):
        evidence.append("comparison")
    # When the LLM intent resolver is unavailable, retain a scientific
    # evidence signal for clearly scientific/health research requests instead
    # of downgrading them to generic news/history. This keeps the downstream
    # domain-specific research and relevance gate active for topics such as
    # H5N1 even during Groq outages.
    if _SCIENTIFIC_RE.search(scan_text):
        if "historical" not in evidence and _HISTORICAL_RE.search(scan_text):
            evidence.append("historical")
        if "news" not in evidence and _NEWS_RE.search(scan_text):
            evidence.append("news")
    if has_rag or has_files:
        evidence.append("documents")

    wants_report = bool(_REPORT_WORD_RE.search(q))
    if wants_report and _SCIENTIFIC_RE.search(scan_text):
        # A report explicitly about a scientific topic should search recent
        # evidence plus historical/clinical/surveillance material when the
        # generic keyword signals alone did not select enough coverage.
        for kind in ("news", "historical"):
            if kind not in evidence:
                evidence.append(kind)
    if not evidence:
        # Bug fix: a bare "write a detailed report on X" / "deep-dive on X"
        # carries none of the specific keyword signals above (no "news",
        # no "vs", no "trend"...), so this used to fall all the way through
        # to evidence=["general_knowledge"] — which downstream in
        # _build_multi_angle_search_queries maps to NO angle suffix at all
        # (see _EVIDENCE_ANGLE_SUFFIX), collapsing the search to a single
        # base query. That's exactly backwards: an explicit "report"/
        # "deep-dive" request is asking for MORE research depth, not less,
        # and this heuristic path only runs when the LLM intent call is
        # already unavailable (no Groq keys / rate-limited / bad response)
        # — precisely the condition under which the request most needs a
        # robust, non-LLM-dependent multi-angle fallback instead of a
        # silent single-query downgrade. Give a bare report request a
        # broad-but-cheap default evidence set (recency + trend) so it
        # still gets a multi-angle search; a request with no report/deep-dive
        # wording and no other signal is left as genuine general_knowledge
        # (e.g. "explain how photosynthesis works" shouldn't trigger a news
        # search just because it's a question).
        evidence = ["news", "historical"] if wants_report else ["general_knowledge"]

    return RequestIntent(
        resolved_topic=resolved_topic,
        is_followup=is_followup,
        intent_label="report request" if wants_report else "",
        evidence_needed=evidence,
        wants_report=wants_report,
        notes="heuristic fallback — keyword-based evidence detection",
        source="heuristic",
    )


async def resolve_request_intent(
    question: str,
    conversation_context: str = "",
    has_rag: bool = False,
    has_files: bool = False,
    timeout: float = 8.0,
) -> RequestIntent:
    """Never raises, never returns None. Falls back to a deterministic
    heuristic (see module docstring) when the LLM call is unavailable or
    fails."""
    question = (question or "").strip()
    if not question:
        return RequestIntent(source="none")

    keys = get_groq_keys()
    if keys:
        user_content = question[:800]
        if conversation_context.strip():
            user_content = (
                f"RECENT CONVERSATION (context only, for resolving a pronoun or "
                f"subject-less follow-up):\n{conversation_context[-1500:]}\n\n"
                f"LATEST MESSAGE: {question[:800]}"
            )
        for key in round_robin(keys)[:3]:
            if is_rate_limited(f"{key}:{_INTENT_MODEL}"):
                continue
            try:
                payload = {
                    "model": _INTENT_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    # gpt-oss models spend part of max_tokens on hidden reasoning. At 400 the
                    # JSON was truncated, Groq answered HTTP 400 "failed to validate json", and
                    # the retry came back empty, so every report fell back to the heuristic
                    # intent. Give it room and keep reasoning short.
                    "max_tokens": 1500,
                    "temperature": 0.1,
                    "reasoning_effort": "low",
                    "response_format": {"type": "json_object"},
                }
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post(
                        GROQ_URL,
                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                        json=payload,
                    )
                    if res.status_code == 400:
                        error_detail = ""
                        try:
                            error_detail = str((res.json().get("error") or {}).get("message") or "")
                        except Exception:
                            pass
                        if "failed to validate json" in error_detail.casefold() or "response_format" in error_detail.casefold() or "reasoning" in error_detail.casefold():
                            # Some compatible model/provider combinations reject JSON mode even though
                            # the model can return valid JSON in its message. Retry once without the
                            # response_format field; downstream _extract_json still validates it.
                            retry_payload = dict(payload)
                            retry_payload.pop("response_format", None)
                            retry_payload.pop("reasoning_effort", None)
                            log.info("Intent resolver retrying once without JSON response_format after provider HTTP 400")
                            res = await client.post(
                                GROQ_URL,
                                headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
                                json=retry_payload,
                            )
                if res.status_code == 429:
                    log.warning("Intent-resolution Groq HTTP 429 model=%s key=...%s; cooling down key/model", _INTENT_MODEL, key[-4:])
                    mark_rate_limited(f"{key}:{_INTENT_MODEL}", 60_000)
                    continue
                if not res.is_success:
                    detail = ""
                    try:
                        error_body = res.json().get("error", {})
                        if isinstance(error_body, dict):
                            detail = str(error_body.get("message") or error_body.get("type") or error_body.get("code") or "")
                    except Exception:
                        detail = ""
                    # Error bodies are useful for differentiating expired credentials
                    # (401/403) from rejected request payloads (400), but never log
                    # Authorization headers or any API key itself.
                    detail = re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", detail)
                    detail = re.sub(r"gsk_[A-Za-z0-9_-]+", "[redacted-key]", detail)[:220]
                    log.warning("Intent-resolution Groq HTTP %d model=%s key=...%s detail=%s",
                                res.status_code, _INTENT_MODEL, key[-4:], detail or "no provider error detail")
                    if res.status_code in (401, 403):
                        mark_rate_limited(f"{key}:{_INTENT_MODEL}", 24 * 60 * 60_000)
                    continue
                content = res.json()["choices"][0]["message"]["content"]
                parsed = _extract_json(content)
                intent = _coerce_intent(parsed, question) if parsed else None
                if intent:
                    log.info(
                        "Intent resolved via LLM: topic=%r followup=%s evidence=%s wants_report=%s",
                        intent.resolved_topic[:80], intent.is_followup, intent.evidence_needed, intent.wants_report,
                    )
                    return intent
            except Exception as exc:
                log.debug("Intent-resolution Groq call failed on key ...%s: %s", key[-4:], exc)
                continue

    log.info("Intent resolution: LLM unavailable/exhausted — using heuristic fallback")
    return _heuristic_intent(question, conversation_context, has_rag, has_files)
