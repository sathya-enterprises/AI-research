"""A structured plan for a natural-language report edit: what to do, to what, where, with which evidence.

The plan routes the edit; it does not write anything:

* element-level edits that add something at a precise place (an explanation under a chart, a paragraph after a
  given paragraph) are generated as one new block and spliced in, so every other character of the report stays
  as it was;
* everything else (rewrite, expand, simplify, correct, restructure, tables, chart changes inside a section, and any
  instruction the patterns below do not recognise) goes to the existing section editor, pointed at the resolved
  section - that editor is an LLM rewrite of one section and handles open-ended instructions;
* when the target cannot be resolved with confidence the plan carries a question instead of a guess.

Intent detection uses verbs and nouns, never topics, so unforeseen wording still maps to an operation, and an
instruction with no recognised verb is treated as a general modification of its target.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_OPS = {
    "explain": r"\b(?:explain|describe|interpret|discuss|analy[sz]e|walk\s+through|elaborate\s+on\s+what)\b"
               r"|\bwhat\s+(?:does|do)\b.{0,60}\b(?:show|mean|demonstrate)\b"
               r"|\b(?:add|write|include|provide|give)\b.{0,40}\b(?:explanation|commentary|interpretation|insights?|takeaways?|analysis)\b",
    "add": r"\b(?:add|insert|include|append|create|introduce|put|write|draft|build|generate|make)\b",
    "delete": r"\b(?:remove|delete|drop|cut|eliminate|get\s+rid\s+of)\b",
    "replace": r"\b(?:replace|swap|substitute)\b",
    "expand": r"\b(?:expand|elaborate|extend|deepen|more\s+detail(?:ed)?|in\s+more\s+depth)\b",
    "simplify": r"\b(?:simplify|shorten|condense|summari[sz]e|plain\s+(?:language|english)|non-?technical|easier\s+to\s+read)\b",
    "reorganize": r"\b(?:move|reorder|reorgani[sz]e|restructure|rearrange|swap\s+the\s+order)\b",
    "research": r"\b(?:research|look\s*up|search|find\s+(?:reliable\s+)?(?:sources?|data|statistics|figures)|latest|most\s+recent|current|up[- ]to[- ]date)\b",
    "verify": r"\b(?:verify|fact[- ]?check|validate|double[- ]check|check\s+(?:the|whether|if)|confirm)\b",
    "correct": r"\b(?:correct|fix|wrong|inaccurate|incorrect|error)\b",
    "format": r"\b(?:format(?:ting)?|heading\s+consistency|readability|transitions?|typos?|consistent\s+headings?)\b",
    "modify": r"\b(?:rewrite|revise|edit|update|change|improve|rephrase|reword|strengthen|tighten|polish)\b",
}
_OUTPUTS = {
    "chart": r"\b(?:charts?|graphs?|plots?|visuali[sz]ations?|figures?)\b",
    "table": r"\b(?:tables?|tabular|matrix)\b",
    "citation": r"\b(?:citations?|sources?|references?)\b",
    "image": r"\b(?:images?|pictures?|photos?)\b",
}
_METRICS = (r"sales|share|market\s+share|revenue|profit|margin|production|output|imports?|exports?|prices?|cost|capacity|"
            r"growth|penetration|registrations?|subsid(?:y|ies)|investment|employment|unemployment|inflation|gdp|"
            r"demand|supply|volume|users?|subscribers?|emissions?|tariffs?|duty|rate|yield|returns?|valuation|"
            r"deployment|installations?|units|shipments|deliveries|consumption|generation|stations?")
_PERIOD_RE = re.compile(r"\b(?:FY\s*'?\d{2,4}(?:\s*[-–]\s*\d{2,4})?|(?:19|20)\d{2}(?:\s*[-–]\s*(?:19|20)?\d{2})?|Q[1-4]\s*(?:FY)?\s*'?\d{2,4})\b", re.I)
_STOP_CAPS = {"Add", "Explain", "Insert", "Replace", "Remove", "Update", "Create", "The", "A", "An", "In", "On", "Page",
              "Figure", "Chart", "Graph", "Table", "Section", "Please", "Can", "Make", "Write", "Include", "Compare",
              "Below", "Above", "After", "Before", "Find", "Use", "Show", "Rewrite", "Expand", "Simplify", "Verify",
              "Check", "Also", "And", "With", "For", "Of", "To", "This", "That", "It"}


@dataclass
class EvidenceNeed:
    entity: str
    metric: str
    periods: list = field(default_factory=list)

    def describe(self) -> str:
        return " ".join(x for x in (self.entity, self.metric, ", ".join(self.periods)) if x)


@dataclass
class EditPlan:
    instruction: str
    operations: list
    outputs: list
    explicit_research: bool
    needs: list
    location: object = None       # utils.edit_location.Location or None
    route: str = "section"        # explain_element | insert_element | section
    question: str = ""

    def to_dict(self) -> dict:
        loc = self.location
        return {"operations": self.operations, "outputs": self.outputs, "explicitResearch": self.explicit_research,
                "needs": [n.describe() for n in self.needs], "route": self.route, "question": self.question,
                "location": None if loc is None else {"element": getattr(getattr(loc, "element", None), "id", None),
                                                       "relation": loc.relation, "page": loc.page, "reason": loc.reason}}


def operations(instruction: str) -> list[str]:
    text = str(instruction or "")
    found = [op for op, pat in _OPS.items() if re.search(pat, text, re.I)]
    return found or ["modify"]


def outputs(instruction: str) -> list[str]:
    text = re.sub(r"\b(?:on|in|below|under|after|above|before)\s+(?:the\s+)?(?:\w+\s+){0,3}(?:chart|graph|figure|table|visuali[sz]ation)\b",
                  " ", str(instruction or ""), flags=re.I)  # "explain the text below the chart": the chart is a place, not an output
    found = [kind for kind, pat in _OUTPUTS.items() if re.search(pat, text, re.I)]
    return found or ["prose"]


def evidence_needs(instruction: str) -> list[EvidenceNeed]:
    """Facts the edit asks for: an entity (a country, company, ...), a measure and periods, read from the wording.
    Generic: capitalised names and measure nouns, no topic lists."""
    text = str(instruction or "")
    periods = sorted({re.sub(r"\s+", "", p) for p in _PERIOD_RE.findall(text)})
    metrics = [m.group(0).lower() for m in re.finditer(rf"\b(?:{_METRICS})\b", text, re.I)]
    names = []
    for m in re.finditer(r"\b([A-Z][A-Za-z&.\-]+(?:\s+[A-Z][A-Za-z&.\-]+){0,3})\b", text):
        words = m.group(1).strip().split()
        while words and words[0] in _STOP_CAPS:   # "Add China" -> "China"
            words = words[1:]
        name = " ".join(words)
        if len(name) < 2 or _PERIOD_RE.fullmatch(name):
            continue
        names.append(name)
    names = list(dict.fromkeys(names))
    if not metrics:
        return [EvidenceNeed(n, "", periods) for n in names] if names and periods else []
    metric = metrics[0]
    return [EvidenceNeed(n, metric, periods) for n in names] or [EvidenceNeed("", metric, periods)]


def plan(instruction: str, elements: list, charts: list | None = None, *, resolve_location=None) -> EditPlan:
    ops = operations(instruction)
    outs = outputs(instruction)
    p = EditPlan(instruction=instruction, operations=ops, outputs=outs,
                 explicit_research=bool(re.search(_OPS["research"], instruction or "", re.I)),
                 needs=evidence_needs(instruction))
    if resolve_location is not None:
        p.location = resolve_location(instruction, elements, charts)
    loc = p.location
    if loc is not None and getattr(loc, "question", ""):
        p.question = loc.question
        return p
    element = getattr(loc, "element", None)
    if element is not None and "explain" in ops and element.kind in {"chart", "table"} and \
            not ({"replace", "delete"} & set(ops)) and "chart" not in [o for o in outs if element.kind != "chart"]:
        p.route = "explain_element"
    elif element is not None and "add" in ops and outs == ["prose"] and element.kind != "heading" \
            and not ({"replace", "delete", "reorganize"} & set(ops)):
        p.route = "insert_element"
    else:
        p.route = "section"
    return p
