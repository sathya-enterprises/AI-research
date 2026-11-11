"""Is a recurring name the PUBLISHER of the evidence rather than a subject of the report? (any topic)

A ministry, statistics office or regulator is named in almost every passage it published ("according to MNRE"),
so text counting makes it look like the most-discussed entity and it used to get a deep-dive section. Instead of
a list of known bodies, the role is read from the run's own evidence:

* the name is the publisher of one of the sources, or the first label of a source's host ("mnre" in mnre.gov.in);
* the name is a governing-body name ("Ministry of ...", "Department of ...", "Bureau of ...", "... Statistics Office");
* the evidence introduces it as the abbreviation of such a name ("Ministry of New and Renewable Energy (MNRE)").

A body that is itself the topic (the question names it) is not demoted.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

_BODY_NAME = re.compile(
    r"(?i)^(?:the\s+)?(?:ministry|department|dept\.?|government|govt\.?|bureau|directorate|office|secretariat|"
    r"statistics|statistical|census|central statistics|national statistic\w*)\b"
    r"|\b(?:ministry|department|directorate|bureau)\s+of\b"
    r"|\b(?:statistics|statistical)\s+(?:office|agency|bureau|authority|service|institute)\b")


def _host_label(url: str) -> str:
    try:
        host = (urlparse(str(url or "")).hostname or "").casefold().removeprefix("www.")
    except ValueError:
        return ""
    return host.split(".", 1)[0] if host else ""


class PublisherRoles:
    """Built once per evidence store; ``is_body(name)`` answers for entity names."""

    def __init__(self, store, question: str = ""):
        self.publishers: set[str] = set()
        self.host_labels: set[str] = set()
        self.expanded: dict[str, str] = {}
        self._passages = list(getattr(store, "passages", None) or [])
        texts = []
        for passage in getattr(store, "passages", None) or []:
            publisher = re.sub(r"\s+", " ", str(getattr(passage, "publisher", "") or "")).strip().casefold()
            if publisher:
                self.publishers.add(publisher)
            label = _host_label(getattr(passage, "url", ""))
            if len(label) >= 3:
                self.host_labels.add(label)
            texts.append(str(getattr(passage, "text", "") or "")[:20000])
        # "Ministry of New and Renewable Energy (MNRE)" -> MNRE is a body
        joined = "\n".join(texts)
        for m in re.finditer(r"((?:[A-Z][a-z]+|of|and|for|the|&)(?:\s+(?:[A-Z][a-z]+|of|and|for|the|&)){1,9})\s*\(([A-Z][A-Za-z&]{1,9})\)",
                             joined):
            long_name, short = m.group(1).strip(), m.group(2)
            if _BODY_NAME.search(long_name):
                self.expanded.setdefault(short, long_name)
        self.question = str(question or "").casefold()

    def is_body(self, name: str) -> bool:
        text = re.sub(r"\s+", " ", str(name or "")).strip()
        if not text:
            return False
        key = text.casefold()
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", self.question):
            return False  # the question is about this body itself
        if _BODY_NAME.search(text) or text in self.expanded:
            return True
        if key in self.publishers or key.replace(" ", "") in self.host_labels:
            # a company that publishes its own investor pages is still a subject when OTHER sources write about
            # it; a think tank or data body is named mostly on its own pages or as an attribution
            return self._mostly_as_source(text)
        return False

    def _mostly_as_source(self, name: str) -> bool:
        key = name.casefold()
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", re.I)
        attribution = re.compile(
            rf"(?i)(?:according to|data from|source:|sources?:|cited by|estimates? (?:by|from)|report(?:ed)? by|"
            rf"analysis by|published by)\s+(?:the\s+)?{re.escape(name)}"
            rf"|{re.escape(name)}(?:'s)?\s+(?:data|report|analysis|estimates?|said|says|noted|reported|database)")
        as_source = as_subject = 0
        for passage in self._passages:
            text = str(getattr(passage, "text", "") or "")
            hits = len(pattern.findall(text))
            if not hits:
                continue
            own = (str(getattr(passage, "publisher", "") or "").casefold() == key
                   or _host_label(getattr(passage, "url", "")) == key.replace(" ", ""))
            if own:
                as_source += hits
            else:
                cited = len(attribution.findall(text))
                as_source += min(cited, hits)
                as_subject += hits - min(cited, hits)
        return as_source > 0 and as_source >= 0.6 * (as_source + as_subject)
