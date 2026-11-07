"""Figure-reference rewriting shared by the V2 section writer and assembly.

When a figure is not in the report (dropped, or a number the writer invented for
its own table), the sentence keeps its claim and citation but loses the framing
that points at a chart that does not exist. No filler phrase is inserted.
"""
from __future__ import annotations

import re

_VERB = r"(?:shows?|illustrates?|highlights?|depicts?|plots?|compares?|captures?|demonstrates?|indicates?|confirms?|reveals?|tracks?)"


def rewrite_removed_figure_refs(text: str, removed: set[int]) -> str:
    """"Figure 6 shows that FPIs sold ..." -> "FPIs sold ..."; "As Figure 6 shows, X" -> "X";
    "(see Figure 6)" / "(Figure 6)" disappear; a caption "Figure 6: 3-Month Returns" keeps only its text."""
    if not removed:
        return text
    alt = "|".join(str(n) for n in sorted(removed))
    fig = rf"(?:Figure|Fig\.)\s+(?:{alt})\b"
    text = re.sub(rf"\s*\((?:see|as shown in|cf\.)?\s*{fig}\)", "", text, flags=re.I)
    text = re.sub(rf"\b[Aa]s\s+{fig}\s+{_VERB},?\s*", "", text)
    text = re.sub(rf"\b(?:[Aa]s\s+)?(?:shown|illustrated|seen|depicted)\s+in\s+{fig},?\s*", "", text)
    text = re.sub(rf"\b{fig}\s+{_VERB}\s+(?:that\s+|how\s+)?", "", text)
    # Captions the writer gave its own tables ("**Figure 4: 3-Month Returns**", "| Figure 4: ... |").
    text = re.sub(rf"(?m)(^|\|\s*|\*\*|__)\s*{fig}\s*[:.—–-]\s*", r"\1", text)
    text = re.sub(rf"\b(?:in|from|per)\s+{fig}\b", "", text)
    text = re.sub(rf"\b{fig}\b", "the data", text)
    # Re-capitalise sentence starts the removal exposed.
    text = re.sub(r"(^|[.!?]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return re.sub(r"[ \t]{2,}", " ", text)
