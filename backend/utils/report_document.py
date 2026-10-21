"""Renderer-neutral semantic model for generated research reports.

The LLM remains responsible for *what* the report says.  This module turns the
LLM's markdown + structured artifacts into a lightweight semantic document
model so HTML/PDF renderers can decide *how* to lay it out without hard-coding
section templates.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportBlock:
    kind: str
    text: str = ""
    count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReportSectionModel:
    id: str
    title: str
    body: str
    blocks: list[ReportBlock] = field(default_factory=list)
    word_count: int = 0
    prose_chars: int = 0
    chart_count: int = 0
    image_count: int = 0
    table_count: int = 0
    bullet_count: int = 0
    quote_count: int = 0

    @property
    def visual_count(self) -> int:
        return self.chart_count + self.image_count + self.table_count

    @property
    def is_data_rich(self) -> bool:
        return self.chart_count > 0 or self.table_count > 0 or self.bullet_count >= 4


@dataclass
class ReportDocument:
    title: str
    question: str
    summary: str
    tier: str = "detailed"
    sections: list[ReportSectionModel] = field(default_factory=list)
    chart_count: int = 0
    image_count: int = 0
    source_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "question": self.question,
            "summary": self.summary,
            "tier": self.tier,
            "chart_count": self.chart_count,
            "image_count": self.image_count,
            "source_count": self.source_count,
            "sections": [
                {
                    "id": s.id,
                    "title": s.title,
                    "word_count": s.word_count,
                    "prose_chars": s.prose_chars,
                    "chart_count": s.chart_count,
                    "image_count": s.image_count,
                    "table_count": s.table_count,
                    "bullet_count": s.bullet_count,
                    "quote_count": s.quote_count,
                    "blocks": [
                        {"kind": b.kind, "count": b.count, "text": b.text[:200], "metadata": b.metadata}
                        for b in s.blocks
                    ],
                }
                for s in self.sections
            ],
        }


# Writers have emitted both `#` and `##` for section-level headings over time.
# Treat either as a dynamic report section; `###` remains an in-section
# analytical subsection and is not promoted into the section navigation.
_H2_RE = re.compile(r"^#{1,2}\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^(?:[-•*]|\d+[.)])\s+")
_CHART_RE = re.compile(r"\[CHART_(\d+)\]", re.I)
_IMAGE_RE = re.compile(r"\[(?:WEB_IMG|FILE_IMG|PAGE_IMG)_(\d+)\]", re.I)


def _split_sections(report: str) -> list[tuple[str, str]]:
    lines = str(report or "").replace("\r\n", "\n").split("\n")
    sections: list[tuple[str, str]] = []
    title = ""
    body: list[str] = []
    for line in lines:
        m = _H2_RE.match(line.strip())
        if m:
            if title or any(x.strip() for x in body):
                sections.append((title or "Report", "\n".join(body).strip()))
            title = m.group(1).strip()
            body = []
        else:
            body.append(line)
    if title or any(x.strip() for x in body):
        sections.append((title or "Report", "\n".join(body).strip()))
    return [(t, b) for t, b in sections if t or b]


def _analyse_section(title: str, body: str, index: int) -> ReportSectionModel:
    text = str(body or "")
    lines = text.splitlines()
    chart_count = len(_CHART_RE.findall(text))
    image_count = len(_IMAGE_RE.findall(text))
    table_count = 0
    bullet_count = 0
    quote_count = 0
    in_table = False
    paragraphs: list[str] = []

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(">"):
            quote_count += 1
        if _BULLET_RE.match(line):
            bullet_count += 1
        if line.startswith("|") and line.endswith("|"):
            if i + 1 < len(lines) and re.match(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$", lines[i + 1].strip()):
                table_count += 1
                in_table = True
                continue
            if in_table:
                continue
        else:
            in_table = False
        if not line.startswith(("#", "[CHART_", "[WEB_IMG_", "[FILE_IMG_", "[PAGE_IMG_", ">", "|")) and not _BULLET_RE.match(line):
            paragraphs.append(line)

    blocks: list[ReportBlock] = []
    if paragraphs:
        blocks.append(ReportBlock("prose", text=" ".join(paragraphs[:2]), count=len(paragraphs)))
    if bullet_count:
        blocks.append(ReportBlock("bullets", count=bullet_count))
    if table_count:
        blocks.append(ReportBlock("table", count=table_count))
    if chart_count:
        blocks.append(ReportBlock("chart", count=chart_count))
    if image_count:
        blocks.append(ReportBlock("image", count=image_count))
    if quote_count:
        blocks.append(ReportBlock("quote", count=quote_count))
    if len(blocks) > 1:
        blocks.append(ReportBlock("mixed", count=len(blocks)))

    words = re.findall(r"\S+", text)
    return ReportSectionModel(
        id=f"section-{index + 1}",
        title=title.strip() or f"Section {index + 1}",
        body=text,
        blocks=blocks,
        word_count=len(words),
        prose_chars=sum(len(p) for p in paragraphs),
        chart_count=chart_count,
        image_count=image_count,
        table_count=table_count,
        bullet_count=bullet_count,
        quote_count=quote_count,
    )


def build_report_document(
    report: str,
    title: str,
    question: str,
    summary: str,
    *,
    tier: str = "detailed",
    charts: list | None = None,
    images: list | None = None,
    sources: object = None,
) -> ReportDocument:
    """Build the semantic model used by adaptive renderers.

    No section template is introduced here: the model reflects the actual
    headings and evidence blocks present in the generated report.
    """
    parsed = [_analyse_section(t, b, i) for i, (t, b) in enumerate(_split_sections(report))]
    return ReportDocument(
        title=str(title or question or "Research Report"),
        question=str(question or ""),
        summary=str(summary or ""),
        tier=str(tier or "detailed"),
        sections=parsed,
        chart_count=len(charts or []),
        image_count=len(images or []),
        source_count=len(sources or []) if isinstance(sources, (list, tuple)) else 0,
    )
