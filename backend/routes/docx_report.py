"""Editable Word export for Growth Gradual research reports.

The DOCX is a native Office Open XML document: headings, paragraphs, lists,
links, source appendix and data tables are editable in Word. Supported structured
charts are native Word charts with embedded Excel data plus editable data tables;
unsupported chart types retain their data as editable tables rather than images.
"""
from __future__ import annotations

import io
import base64
import html
import logging
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.opc.part import Part
from docx.opc.packuri import PackURI
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response

from routes.source_manifest import normalise_source_manifest
from utils.chart_evidence_audit import audit_chart_evidence_consistency, sanitize_charts_for_publication

router = APIRouter()
log = logging.getLogger("docx")

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
NAVY = "1A1F4E"
GREEN = "167E58"
PALE = "F1F4F8"
MUTED = "5D667A"

# A citation group is intentionally parsed as a single unit so each source ID
# becomes its own real hyperlink in Word instead of an inert string.
_CITATION_GROUP_RE = re.compile(r"(?<![A-Za-z0-9])\[(S\d+(?:\s*[,;]\s*S\d+)*)\]", re.I)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_INLINE_TOKEN_RE = re.compile(
    r"(\[[^\]]+\]\(https?://[^\s)]+\)|\[(?:S\d+)(?:\s*[,;]\s*S\d+)*\]|https?://[^\s<>]+|\*\*.+?\*\*|(?<!\*)\*[^*]+\*|`[^`]+`)",
    re.I,
)
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_CHART_MARKER_RE = re.compile(r"\[CHART_(\d+)\]", re.I)
_IMAGE_MARKER_RE = re.compile(r"\[(WEB_IMG(?:_(\d+))?|IMAGE_(\d+)|FILE_IMG_(\d+)|PAGE_IMG_(\d+))\]", re.I)
_CONTROL_MARKER_RE = re.compile(r"\[RISK_MATRIX\]", re.I)
_FAILURE_SENTINELS = (
    "all llm keys exhausted or rate-limited. try again in a minute.",
    "could not retrieve data for this topic. please try again.",
    "invalid request body.",
)


def _safe_url(value: Any) -> str:
    url = str(value or "").strip()
    try:
        p = urlparse(url)
        return url if p.scheme in {"http", "https"} and p.netloc else ""
    except Exception:
        return ""


def _add_hyperlink(paragraph, text: str, url: str, color: str = GREEN, underline: bool = True):
    """Add an actual external hyperlink relationship to a Word paragraph."""
    url = _safe_url(url)
    if not url:
        paragraph.add_run(text)
        return
    part = paragraph.part
    rel_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    c = OxmlElement("w:color")
    c.set(qn("w:val"), color)
    rpr.append(c)
    if underline:
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
    run.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _set_cell_shading(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def _set_cell_margins(cell, top=90, start=105, bottom=90, end=105):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for m, v in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{m}"))
        if node is None:
            node = OxmlElement(f"w:{m}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(v))
        node.set(qn("w:type"), "dxa")


def _style_table(table, header=True):
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True
    for ri, row in enumerate(table.rows):
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            _set_cell_margins(cell)
            if header and ri == 0:
                _set_cell_shading(cell, NAVY)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.color.rgb = RGBColor(255, 255, 255)
                        run.font.size = Pt(8.5)
            else:
                if ri % 2 == 0:
                    _set_cell_shading(cell, PALE)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in paragraph.runs:
                        run.font.size = Pt(8.5)


def _add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Page ")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(MUTED)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    paragraph._p.append(fld)


def _source_map(sources: list[dict]) -> dict[str, dict]:
    return {str(s.get("id") or "").upper(): s for s in sources if isinstance(s, dict) and s.get("id")}


def _add_inline(paragraph, text: str, source_by_id: dict[str, dict]):
    """Small Markdown inline parser for emphasis, links and canonical citations."""
    last = 0
    for match in _INLINE_TOKEN_RE.finditer(text):
        if match.start() > last:
            paragraph.add_run(text[last:match.start()])
        token = match.group(0)
        link_match = _MARKDOWN_LINK_RE.fullmatch(token)
        citation_match = _CITATION_GROUP_RE.fullmatch(token)
        if link_match:
            _add_hyperlink(paragraph, link_match.group(1), link_match.group(2))
        elif citation_match:
            ids = re.findall(r"S\d+", citation_match.group(1), re.I)
            paragraph.add_run("[")
            for ix, source_id in enumerate(ids):
                if ix:
                    paragraph.add_run(", ")
                src = source_by_id.get(source_id.upper()) or {}
                _add_hyperlink(paragraph, source_id.upper(), src.get("url", ""))
            paragraph.add_run("]")
        elif token.startswith("http://") or token.startswith("https://"):
            url = token.rstrip(".,;)")
            _add_hyperlink(paragraph, url, url)
            if url != token:
                paragraph.add_run(token[len(url):])
        elif token.startswith("**") and token.endswith("**"):
            run = paragraph.add_run(token[2:-2]); run.bold = True
        elif token.startswith("*") and token.endswith("*"):
            run = paragraph.add_run(token[1:-1]); run.italic = True
        elif token.startswith("`") and token.endswith("`"):
            run = paragraph.add_run(token[1:-1]); run.font.name = "Consolas"; run.font.size = Pt(9)
        else:
            paragraph.add_run(token)
        last = match.end()
    if last < len(text):
        paragraph.add_run(text[last:])


def _split_table_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [cell.strip() for cell in raw.split("|")]


def _add_markdown_table(doc, lines: list[str], source_by_id: dict[str, dict]):
    parsed = [_split_table_row(line) for line in lines if line.strip()]
    if not parsed:
        return
    if len(parsed) > 1 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", c or "") for c in parsed[1]):
        parsed.pop(1)
    width = max((len(row) for row in parsed), default=0)
    if width <= 0:
        return
    table = doc.add_table(rows=1, cols=width)
    for ci in range(width):
        cell = table.rows[0].cells[ci]
        cell.text = ""
        _add_inline(cell.paragraphs[0], parsed[0][ci] if ci < len(parsed[0]) else "", source_by_id)
    for cells in parsed[1:]:
        row = table.add_row()
        for ci in range(width):
            cell = row.cells[ci]
            cell.text = ""
            _add_inline(cell.paragraphs[0], cells[ci] if ci < len(cells) else "", source_by_id)
    _style_table(table)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def _flatten_presentation_blocks(value: Any) -> list[dict]:
    """Walk the dynamic presentation object to locate structured risk/table data."""
    found: list[dict] = []
    seen: set[int] = set()
    def walk(node: Any, depth: int = 0):
        if depth > 12 or id(node) in seen:
            return
        if isinstance(node, (dict, list)):
            seen.add(id(node))
        if isinstance(node, dict):
            kind = str(node.get("kind") or "").casefold()
            if kind in {"risk_matrix", "table", "timeline", "comparison"}:
                found.append(node)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)
    walk(value)
    return found


def _add_risk_matrix(doc, blocks: list[dict], source_by_id: dict[str, dict]) -> bool:
    for block in blocks:
        if str(block.get("kind") or "").casefold() != "risk_matrix":
            continue
        risks = block.get("risks") or []
        if not risks:
            continue
        title = str(block.get("title") or "Risk matrix")
        doc.add_heading(title, level=3)
        table = doc.add_table(rows=1, cols=4)
        headers = ["Risk", "Likelihood", "Impact / severity", "Mitigation"]
        for i, header in enumerate(headers):
            table.rows[0].cells[i].text = header
        for item in risks[:100]:
            row = table.add_row()
            vals = [item.get("name", ""), item.get("likelihood", ""), item.get("impact", ""), item.get("mitigation", "")]
            for i, val in enumerate(vals):
                row.cells[i].text = str(val or "")
                for p in row.cells[i].paragraphs:
                    _add_inline(p, p.text, source_by_id)
        _style_table(table)
        return True
    return False


def _chart_series(chart: dict) -> list[dict]:
    series = chart.get("series") if isinstance(chart.get("series"), list) else []
    clean = []
    for idx, s in enumerate(series[:20]):
        if not isinstance(s, dict):
            continue
        points = s.get("data") or []
        if isinstance(points, dict):
            points = list(points.values())
        if not isinstance(points, list):
            continue
        clean_points = []
        for point in points[:500]:
            if not isinstance(point, dict):
                continue
            label = str(point.get("label") or point.get("x") or point.get("date") or point.get("name") or "")
            value = point.get("value", point.get("y", point.get("amount", "")))
            if label or value not in (None, ""):
                clean_points.append((label, value))
        if clean_points:
            clean.append({"name": str(s.get("name") or f"Series {idx + 1}"), "points": clean_points})
    return clean


def _chart_xml_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _chart_native_payload(chart: dict):
    """Return chart tables suitable for a native embedded Excel-backed chart."""
    series = _chart_series(chart)
    chart_type = str(chart.get("type") or "").casefold().replace("-", "_").replace(" ", "_")
    if chart_type in {"horizontal_bar", "bar_horizontal"}:
        chart_type = "bar_horizontal"
    elif chart_type in {"grouped_bar", "bar_grouped"}:
        chart_type = "bar_grouped"
    elif chart_type in {"stacked_bar", "bar_stacked"}:
        chart_type = "bar_stacked"
    elif chart_type in {"spline", "step_line", "stepline", "sparkline"}:
        chart_type = "line"
    elif chart_type in {"area", "stacked_area"}:
        chart_type = "area"
    if chart_type not in {"bar", "bar_horizontal", "bar_grouped", "bar_stacked", "line", "area", "pie", "doughnut"}:
        return None
    if not series:
        return None
    if chart_type in {"pie", "doughnut"} and len(series) != 1:
        return None
    labels: list[str] = []
    value_maps = []
    for item in series:
        value_map = {}
        for label, value in item["points"]:
            if label not in labels:
                labels.append(label)
            try:
                value_map[label] = float(str(value).replace(",", "").replace("%", "").strip())
            except (ValueError, TypeError):
                return None
        value_maps.append(value_map)
    if not labels or not any(v for mapping in value_maps for v in mapping.values()):
        # Zero-only charts are still valid if data exists, but an actually empty
        # data set should not create a blank native chart object.
        if not labels or not any(mapping for mapping in value_maps):
            return None
    return chart_type, labels, series, value_maps


def _make_chart_xml(chart: dict, index: int, chart_type: str, labels: list[str], series: list[dict], value_maps: list[dict]) -> bytes:
    """Build a compact OOXML chart part with cached points and an embedded workbook."""
    n = len(labels)
    end_row = n + 1
    chart_name = _chart_xml_text(chart.get("title") or f"Chart {index}")
    # Chart XML cache makes the chart immediately visible in Word; the embedded
    # workbook is linked so users can choose Chart Design → Edit Data in Word.
    series_xml = []
    for si, item in enumerate(series):
        col_num = si + 2
        col = chr(64 + col_num) if col_num <= 26 else "A"
        source_name = _chart_xml_text(item["name"])
        cat_points = "".join(f'<c:pt idx="{i}"><c:v>{_chart_xml_text(label)}</c:v></c:pt>' for i, label in enumerate(labels))
        values = value_maps[si]
        val_points = "".join(f'<c:pt idx="{i}"><c:v>{values[label]:.12g}</c:v></c:pt>' for i, label in enumerate(labels) if label in values)
        color = re.sub(r"[^0-9A-Fa-f]", "", str(item.get("color") or GREEN)).upper()[:6] or GREEN
        tx = (
            f'<c:tx><c:strRef><c:f>Data!${col}$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{source_name}</c:v></c:pt></c:strCache></c:strRef></c:tx>'
        )
        cat = (
            f'<c:cat><c:strRef><c:f>Data!$A$2:$A${end_row}</c:f>'
            f'<c:strCache><c:ptCount val="{n}"/>{cat_points}</c:strCache></c:strRef></c:cat>'
        )
        val = (
            f'<c:val><c:numRef><c:f>Data!${col}$2:${col}${end_row}</c:f>'
            f'<c:numCache><c:formatCode>General</c:formatCode><c:ptCount val="{len(values)}"/>{val_points}</c:numCache></c:numRef></c:val>'
        )
        style = f'<c:spPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:ln><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:ln></c:spPr>'
        series_xml.append(f'<c:ser><c:idx val="{si}"/><c:order val="{si}"/>{tx}{cat}{val}{style}</c:ser>')

    if chart_type in {"bar", "bar_horizontal", "bar_grouped", "bar_stacked"}:
        direction = "bar" if chart_type == "bar_horizontal" else "col"
        grouping = "stacked" if chart_type == "bar_stacked" else "clustered"
        plot = (
            f'<c:barChart><c:barDir val="{direction}"/><c:grouping val="{grouping}"/><c:varyColors val="0"/>'
            + "".join(series_xml) +
            '<c:gapWidth val="65"/><c:overlap val="0"/><c:axId val="48650112"/><c:axId val="48672768"/></c:barChart>'
            '<c:catAx><c:axId val="48650112"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="b"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="48672768"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/></c:catAx>'
            '<c:valAx><c:axId val="48672768"/><c:scaling><c:orientation val="minMax"/><c:min val="0"/></c:scaling><c:delete val="0"/><c:axPos val="l"/><c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="DDE3EA"/></a:solidFill></a:ln></c:spPr></c:majorGridlines><c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="48650112"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>'
        )
    elif chart_type in {"line", "area"}:
        plot_type = "areaChart" if chart_type == "area" else "lineChart"
        plot = f'<c:{plot_type}><c:grouping val="standard"/><c:varyColors val="0"/>' + "".join(series_xml)
        if chart_type == "line":
            plot += '<c:marker val="1"/>'
        plot += ('<c:axId val="48650112"/><c:axId val="48672768"/></c:' + plot_type + '>'
                 '<c:catAx><c:axId val="48650112"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="b"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="48672768"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/></c:catAx>'
                 '<c:valAx><c:axId val="48672768"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="l"/><c:majorGridlines><c:spPr><a:ln w="6350"><a:solidFill><a:srgbClr val="DDE3EA"/></a:solidFill></a:ln></c:spPr></c:majorGridlines><c:numFmt formatCode="General" sourceLinked="1"/><c:majorTickMark val="none"/><c:minorTickMark val="none"/><c:tickLblPos val="nextTo"/><c:crossAx val="48650112"/><c:crosses val="autoZero"/><c:crossBetween val="between"/></c:valAx>')
    else:
        plot = f'<c:{chart_type}Chart><c:varyColors val="1"/>' + series_xml[0] + '<c:firstSliceAng val="0"/>'
        if chart_type == "doughnut":
            plot += '<c:holeSize val="58"/>'
        plot += f'</c:{chart_type}Chart>'

    chart_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<c:date1904 val="0"/><c:lang val="en-US"/><c:roundedCorners val="0"/><c:chart>'
        f'<c:title><c:tx><c:strRef><c:f>Data!$Z$1</c:f><c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>{chart_name}</c:v></c:pt></c:strCache></c:strRef></c:tx><c:overlay val="0"/></c:title>'
        f'<c:plotArea><c:layout/>{plot}</c:plotArea>'
        '<c:legend><c:legendPos val="b"/><c:layout/><c:overlay val="0"/></c:legend>'
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/><c:showDLblsOverMax val="0"/>'
        '</c:chart><c:spPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln><a:noFill/></a:ln></c:spPr>'
        '<c:printSettings><c:headerFooter/><c:pageMargins b="0.25" l="0.25" r="0.25" t="0.25" header="0.3" footer="0.3"/><c:pageSetup/></c:printSettings>'
        '<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData></c:chartSpace>'
    )
    return chart_xml.encode("utf-8")


def _add_native_word_chart(doc, paragraph, chart: dict, index: int, payload) -> bool:
    """Insert a true editable Word chart linked to an embedded Excel workbook."""
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
        native = payload or _chart_native_payload(chart)
        if not native:
            return False
        chart_type, labels, series, value_maps = native
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.cell(1, 1, "Category")
        for si, item in enumerate(series, start=2):
            sheet.cell(1, si, item["name"])
        for ri, label in enumerate(labels, start=2):
            sheet.cell(ri, 1, label)
            for si, values in enumerate(value_maps, start=2):
                sheet.cell(ri, si, values.get(label))
        sheet.cell(1, 26, str(chart.get("title") or f"Chart {index}"))
        for col_idx in range(1, min(28, 2 + len(series))):
            sheet.column_dimensions[get_column_letter(col_idx)].width = 20
        embedded = io.BytesIO()
        workbook.save(embedded)

        package = doc.part.package
        embedded_name = package.next_partname("/word/embeddings/Microsoft_Excel_Worksheet%d.xlsx")
        embedded_part = Part(embedded_name, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", embedded.getvalue(), package)
        chart_name = package.next_partname("/word/charts/chart%d.xml")
        chart_part = Part(chart_name, "application/vnd.openxmlformats-officedocument.drawingml.chart+xml", _make_chart_xml(chart, index, chart_type, labels, series, value_maps), package)
        chart_part.relate_to(embedded_part, RT.PACKAGE, is_external=False)
        rel_id = doc.part.relate_to(chart_part, RT.CHART, is_external=False)

        drawing = OxmlElement("w:drawing")
        inline = OxmlElement("wp:inline")
        inline.set("distT", "0"); inline.set("distB", "0"); inline.set("distL", "0"); inline.set("distR", "0")
        extent = OxmlElement("wp:extent")
        extent.set("cx", str(6_000_000)); extent.set("cy", str(3_100_000))
        inline.append(extent)
        effect = OxmlElement("wp:effectExtent")
        for side in ("l", "t", "r", "b"): effect.set(side, "0")
        inline.append(effect)
        docpr = OxmlElement("wp:docPr")
        docpr.set("id", str(100 + index)); docpr.set("name", f"Editable chart {index}"); docpr.set("descr", str(chart.get("title") or f"Chart {index}"))
        inline.append(docpr)
        frame_props = OxmlElement("wp:cNvGraphicFramePr")
        locks = OxmlElement("a:graphicFrameLocks")
        locks.set("noChangeAspect", "1")
        frame_props.append(locks); inline.append(frame_props)
        graphic = OxmlElement("a:graphic")
        graphic_data = OxmlElement("a:graphicData")
        graphic_data.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/chart")
        chart_ref = OxmlElement("c:chart")
        chart_ref.set(qn("r:id"), rel_id)
        graphic_data.append(chart_ref); graphic.append(graphic_data); inline.append(graphic)
        drawing.append(inline)
        run = OxmlElement("w:r"); run.append(drawing)
        paragraph._p.append(run)
        return True
    except Exception as exc:
        log.warning("DOCX: could not create native editable chart %d; using editable data table: %s", index, type(exc).__name__)
        return False


def _add_chart_data(doc, chart: dict, index: int, source_by_id: dict[str, dict]):
    title = str(chart.get("title") or f"Chart {index}")
    p = doc.add_paragraph()
    p.style = doc.styles["Heading 3"]
    _add_inline(p, title, source_by_id)
    series = _chart_series(chart)
    chart_payload = _chart_native_payload(chart)
    if chart_payload:
        _add_native_word_chart(doc, doc.add_paragraph(), chart, index, chart_payload)
    columns = chart.get("columns") if isinstance(chart.get("columns"), list) else []
    rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
    if series:
        labels: list[str] = []
        for s in series:
            for label, _ in s["points"]:
                if label not in labels:
                    labels.append(label)
        headers = ["Category"] + [s["name"] + (f" ({chart.get('unit')})" if chart.get("unit") else "") for s in series]
        has_bars = str(chart.get("type") or "").casefold() in {"bar", "horizontal_bar", "horizontal-bar", "stacked_bar", "grouped_bar", "waterfall", "funnel"} and len(series) == 1
        if has_bars:
            headers.append("Relative bar (editable text)")
        table = doc.add_table(rows=1, cols=len(headers))
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        maps = [{label: val for label, val in s["points"]} for s in series]
        numeric_values = []
        for valmap in maps[:1]:
            for value in valmap.values():
                try: numeric_values.append(float(str(value).replace(",", "")))
                except (TypeError, ValueError): pass
        max_abs = max([abs(x) for x in numeric_values], default=0.0)
        for label in labels:
            row = table.add_row()
            row.cells[0].text = label
            for si, valmap in enumerate(maps):
                row.cells[si + 1].text = str(valmap.get(label, "—"))
            if has_bars:
                value = maps[0].get(label, "")
                try:
                    n = float(str(value).replace(",", ""))
                    blocks = min(18, max(0, round(abs(n) / max_abs * 18))) if max_abs else 0
                    row.cells[-1].text = ("−" if n < 0 else "") + ("▰" * blocks)
                except (TypeError, ValueError):
                    row.cells[-1].text = ""
        _style_table(table)
        note = doc.add_paragraph("Chart data are native editable Word table cells; change these values directly in Word.")
        note.paragraph_format.space_after = Pt(6)
        for run in note.runs:
            run.italic = True; run.font.size = Pt(8); run.font.color.rgb = RGBColor.from_string(MUTED)
    elif columns and rows:
        data = [[str(v) for v in columns]] + [[str(v) for v in row] for row in rows if isinstance(row, (list, tuple))]
        _add_markdown_table(doc, ["| " + " | ".join(r) + " |" for r in data], source_by_id)
    else:
        p = doc.add_paragraph("No structured observations were supplied for this chart, so values were not fabricated in the Word export.")
        for run in p.runs:
            run.italic = True; run.font.color.rgb = RGBColor.from_string(MUTED)


def _add_inline_chart_marker(doc, chart: dict, index: int, source_by_id: dict[str, dict]):
    _add_chart_data(doc, chart, index, source_by_id)


def _image_bytes(item: Any) -> bytes | None:
    """Decode and normalize an already-hydrated image to PNG bytes.

    Only inline/data payloads are accepted here. Remote URLs must first pass
    through the frontend's allow-listed image hydration proxy.
    """
    if not isinstance(item, dict):
        return None
    raw = str(item.get("data") or item.get("url") or "").strip()
    if not raw or raw.startswith(("http://", "https://")):
        return None
    try:
        if raw.startswith("data:image/"):
            _header, payload = raw.split(",", 1)
            data = base64.b64decode(payload, validate=False)
        else:
            data = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    if len(data) < 16 or len(data) > 4_000_000:
        return None
    # Pillow validates the payload and normalizes PNG/JPEG/GIF/WebP to a
    # Word-compatible PNG, avoiding fragile byte-signature handling.
    try:
        from PIL import Image as PILImage
        im = PILImage.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        out = io.BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return None


def _insert_image(doc: Document, item: dict, index: int) -> bool:
    data = _image_bytes(item)
    if not data:
        return False
    try:
        picture = doc.add_picture(io.BytesIO(data), width=Inches(6.15))
        picture._inline.docPr.set("descr", str(item.get("caption") or item.get("name") or f"Report image {index}"))
        caption = str(item.get("caption") or item.get("name") or "").strip()
        source_url = item.get("sourceUrl") or item.get("sourceURL") or item.get("source_page_url")
        if caption or _safe_url(source_url):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            if caption:
                run = p.add_run(caption)
                run.italic = True
                run.font.size = Pt(8)
                run.font.color.rgb = RGBColor.from_string(MUTED)
            if _safe_url(source_url):
                if caption:
                    p.add_run(" · ")
                _add_hyperlink(p, "Source page", str(source_url))
            p.paragraph_format.space_after = Pt(7)
        return True
    except Exception as exc:
        log.warning("DOCX: skipped image %d after decode/render error: %s", index, type(exc).__name__)
        return False


def _configure_document(doc: Document):
    sec = doc.sections[0]
    sec.top_margin = Inches(0.68)
    sec.bottom_margin = Inches(0.65)
    sec.left_margin = Inches(0.78)
    sec.right_margin = Inches(0.78)
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Aptos"
    normal.font.size = Pt(9.5)
    normal.font.color.rgb = RGBColor.from_string("202839")
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.12
    for name, size, color in (("Title", 26, NAVY), ("Heading 1", 19, NAVY), ("Heading 2", 14, GREEN), ("Heading 3", 11, NAVY), ("Heading 4", 10, GREEN)):
        style = styles[name]
        style.font.name = "Aptos Display" if name in {"Title", "Heading 1", "Heading 2"} else "Aptos"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before = Pt(9 if name != "Title" else 0)
        style.paragraph_format.space_after = Pt(4)
    header = sec.header.paragraphs[0]
    header.text = "GROWTH GRADUAL  ·  RESEARCH INTELLIGENCE"
    header.alignment = WD_ALIGN_PARAGRAPH.LEFT
    for run in header.runs:
        run.font.size = Pt(8)
        run.font.bold = True
        run.font.color.rgb = RGBColor.from_string(GREEN)
    footer = sec.footer.paragraphs[0]
    _add_page_number(footer)


def build_docx_report(
    report: str,
    title: str = "Research Report",
    question: str = "",
    summary: str = "",
    key_stats: list | None = None,
    charts: list | None = None,
    sources: list | None = None,
    presentation: dict | None = None,
    source_documents: list | None = None,
    images: list | None = None,
    file_images: list | None = None,
    report_tier: str = "detailed",
) -> bytes:
    """Build a fully editable DOCX using only supplied report facts/data."""
    doc = Document()
    _configure_document(doc)
    registry = normalise_source_manifest(sources or [])
    source_by_id = _source_map(registry)

    title = re.sub(r"\s+", " ", str(title or "Research Report")).strip()[:240]
    doc.core_properties.title = title
    # OOXML core-properties subject is limited to 255 characters. Long user
    # questions are rendered as editable subtitle text below, but must not make
    # python-docx abort the whole export while writing metadata.
    doc.core_properties.subject = str(question or title)[:255]
    doc.core_properties.author = "Growth Gradual"
    doc.core_properties.keywords = f"Research report, {report_tier}, Growth Gradual"

    title_p = doc.add_paragraph(style="Title")
    title_p.paragraph_format.keep_with_next = True
    title_p.add_run(title)
    if question and question.strip() and question.strip().casefold() != title.casefold():
        subtitle = doc.add_paragraph()
        subtitle.paragraph_format.space_after = Pt(5)
        run = subtitle.add_run(question.strip()[:500])
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor.from_string(MUTED)
    meta = doc.add_paragraph()
    meta.paragraph_format.space_after = Pt(10)
    meta_run = meta.add_run(f"Prepared {datetime.now().astimezone().strftime('%d %B %Y')}  ·  {str(report_tier).replace('_', ' ').title()} report")
    meta_run.font.size = Pt(8)
    meta_run.font.color.rgb = RGBColor.from_string(MUTED)

    stats = [x for x in (key_stats or []) if isinstance(x, dict) and (x.get("label") or x.get("value"))][:12]
    if stats:
        table = doc.add_table(rows=0, cols=2)
        # Two KPIs per row; labels and values remain ordinary editable cells.
        for start in range(0, len(stats), 2):
            row = table.add_row()
            for offset in range(2):
                cell = row.cells[offset]
                cell.text = ""
                item_ix = start + offset
                if item_ix < len(stats):
                    item = stats[item_ix]
                    p = cell.paragraphs[0]
                    label = p.add_run(str(item.get("label") or "Metric") + "\n")
                    label.bold = True; label.font.size = Pt(8); label.font.color.rgb = RGBColor.from_string(GREEN)
                    value = p.add_run(str(item.get("value") or "—"))
                    value.bold = True; value.font.size = Pt(15); value.font.color.rgb = RGBColor.from_string(NAVY)
                    if item.get("change"):
                        change = p.add_run("\n" + str(item.get("change")))
                        change.font.size = Pt(8); change.font.color.rgb = RGBColor.from_string(MUTED)
                _set_cell_shading(cell, "F2F6F4")
                _set_cell_margins(cell, top=140, bottom=140)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        doc.add_paragraph().paragraph_format.space_after = Pt(1)

    charts = [c for c in (charts or []) if isinstance(c, dict)][:100]
    web_images = [dict(im) for im in (images or []) if isinstance(im, dict)][:6]
    file_image_list = [dict(im) for im in (file_images or []) if isinstance(im, dict)][:20]
    image_slots = web_images + file_image_list
    image_rendered: set[int] = set()
    presentation_blocks = _flatten_presentation_blocks(presentation or {})
    chart_rendered: set[int] = set()

    # Unwrap accidental escaped newlines/fences like the PDF/HTML renderers do.
    text = str(report or "").replace("\\n", "\n").strip()
    text = re.sub(r"^```(?:markdown|md|text)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```\s*$", "", text)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i += 1; continue
        # Render Markdown tables before interpreting the individual pipe rows.
        if "|" in stripped and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]):
            table_lines = [stripped, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_lines.append(lines[i].strip()); i += 1
            _add_markdown_table(doc, table_lines, source_by_id)
            continue
        # If the generator left a chart token in the Markdown, place its native
        # editable data table exactly at that location.
        chart_match = _CHART_MARKER_RE.search(stripped)
        if chart_match:
            chart_idx = int(chart_match.group(1)) - 1
            before = stripped[:chart_match.start()].strip()
            if before:
                p = doc.add_paragraph(); _add_inline(p, before, source_by_id)
            if 0 <= chart_idx < len(charts):
                _add_inline_chart_marker(doc, charts[chart_idx], chart_idx + 1, source_by_id)
                chart_rendered.add(chart_idx)
            else:
                p = doc.add_paragraph("This chart reference had no matching data payload; no values were invented.")
                for r in p.runs: r.italic = True
            after = stripped[chart_match.end():].strip()
            if after:
                p = doc.add_paragraph(); _add_inline(p, after, source_by_id)
            i += 1; continue
        image_match = _IMAGE_MARKER_RE.search(stripped)
        if image_match:
            before = stripped[:image_match.start()].strip()
            if before:
                p = doc.add_paragraph(); _add_inline(p, before, source_by_id)
            web_number = image_match.group(2) or image_match.group(3)
            file_number = image_match.group(4) or image_match.group(5)
            if web_number:
                image_index = int(web_number) - 1
            elif file_number:
                image_index = len(web_images) + int(file_number) - 1
            else:
                image_index = next((idx for idx in range(len(image_slots)) if idx not in image_rendered), -1)
            if 0 <= image_index < len(image_slots):
                if _insert_image(doc, image_slots[image_index], image_index + 1):
                    image_rendered.add(image_index)
                else:
                    p = doc.add_paragraph("Figure data was not available for embedding; the figure is not fabricated.")
                    for r in p.runs: r.italic = True; r.font.color.rgb = RGBColor.from_string(MUTED)
            else:
                p = doc.add_paragraph("Figure reference has no hydrated image data in the export payload.")
                for r in p.runs: r.italic = True; r.font.color.rgb = RGBColor.from_string(MUTED)
            after = stripped[image_match.end():].strip()
            if after:
                p = doc.add_paragraph(); _add_inline(p, after, source_by_id)
            i += 1; continue
        if _CONTROL_MARKER_RE.search(stripped):
            visible = _CONTROL_MARKER_RE.sub("", stripped).strip()
            if visible:
                p = doc.add_paragraph(); _add_inline(p, visible, source_by_id)
            # Do not expose control markers to the reader. Materialize a risk
            # matrix only when the actual structured payload contains one.
            if re.search(r"\[RISK_MATRIX\]", stripped, re.I):
                if not _add_risk_matrix(doc, presentation_blocks, source_by_id):
                    p = doc.add_paragraph("Risk matrix data was not supplied as a structured block in this export. The report's narrative risk discussion is retained above.")
                    for r in p.runs: r.italic = True; r.font.color.rgb = RGBColor.from_string(MUTED)
            i += 1; continue
        if re.match(r"^\s*[-*_]{3,}\s*$", raw):
            i += 1; continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading:
            level = min(4, len(heading.group(1)))
            heading_text = heading.group(2).strip()
            # Avoid an immediate duplicate report-title heading if Markdown has
            # the same H1 title as the document cover/title area.
            if not (level == 1 and heading_text.casefold() == title.casefold()):
                p = doc.add_paragraph(style=f"Heading {level}" if level > 1 else "Heading 1")
                _add_inline(p, heading_text, source_by_id)
            i += 1; continue
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", raw)
        if bullet:
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.left_indent = Inches(0.2)
            _add_inline(p, bullet.group(1), source_by_id)
            i += 1; continue
        numbered = re.match(r"^\s*\d+[.)]\s+(.+)$", raw)
        if numbered:
            p = doc.add_paragraph(style="List Number")
            _add_inline(p, numbered.group(1), source_by_id)
            i += 1; continue
        quote = re.match(r"^\s*>\s?(.*)$", raw)
        if quote:
            p = doc.add_paragraph(style="Quote")
            _add_inline(p, quote.group(1), source_by_id)
            i += 1; continue
        # Join wrapped markdown lines into one paragraph until structural line.
        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            ns = nxt.strip()
            if (not ns or re.match(r"^#{1,6}\s", ns) or re.match(r"^\s*[-*+]\s+", nxt)
                    or re.match(r"^\s*\d+[.)]\s+", nxt) or re.match(r"^\s*>\s?", nxt)
                    or ("|" in ns and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1]))
                    or _CHART_MARKER_RE.search(ns) or _CONTROL_MARKER_RE.search(ns)):
                break
            paragraph_lines.append(ns); i += 1
        p = doc.add_paragraph()
        _add_inline(p, " ".join(paragraph_lines), source_by_id)

    # Any chart not explicitly placed by a marker must still be represented in
    # the editable document; add a generated appendix only if required.
    unplaced = [idx for idx in range(len(charts)) if idx not in chart_rendered]
    if unplaced:
        doc.add_heading("Editable chart data", level=1)
        for idx in unplaced:
            _add_chart_data(doc, charts[idx], idx + 1, source_by_id)

    # Preserve selected figures even when the writer supplied an image but did
    # not place an explicit marker. They remain ordinary replaceable Word images.
    unplaced_images = [idx for idx in range(len(image_slots)) if idx not in image_rendered and _image_bytes(image_slots[idx])]
    if unplaced_images:
        doc.add_heading("Supporting Figures", level=1)
        for idx in unplaced_images:
            if _insert_image(doc, image_slots[idx], idx + 1):
                image_rendered.add(idx)

    # Uploaded source files and RAG documents get their own provenance section.
    # This records what informed the report without dumping arbitrary source
    # contents into the export or pretending every file was fully cited.
    attached_docs = [d for d in (source_documents or []) if isinstance(d, dict) and (d.get("name") or d.get("text"))][:100]
    if attached_docs:
        doc.add_page_break()
        doc.add_heading("Uploaded / RAG Documents", level=1)
        doc.add_paragraph("These are the user-provided or indexed documents supplied as research context. Individual statements should still be checked against their originating document before external reliance.")
        for item in attached_docs:
            p = doc.add_paragraph(style="List Bullet")
            p.add_run(str(item.get("name") or "Unnamed document")).bold = True
            file_type = str(item.get("file_type") or item.get("mimeType") or "source document")
            detail = doc.add_paragraph(f"Type: {file_type}")
            detail.paragraph_format.left_indent = Inches(0.25)
            for run in detail.runs:
                run.font.size = Pt(8); run.font.color.rgb = RGBColor.from_string(MUTED)

    # Preserve the complete, stable S# registry and clearly indicate source
    # fetch status rather than asserting that every linked page was verified.
    if registry:
        doc.add_page_break()
        doc.add_heading("Data Sources", level=1)
        doc.add_paragraph("Source identifiers in the report resolve to the entries below. A hyperlink indicates the source location, not independent verification of every claim.")
        for src in registry:
            source_id = str(src.get("id") or "").upper()
            title_text = str(src.get("title") or "Untitled source")
            p = doc.add_paragraph(style="List Bullet")
            if source_id:
                p.add_run(f"[{source_id}] ")
            _add_hyperlink(p, title_text, src.get("url", ""))
            details = []
            if src.get("publisher"):
                details.append(str(src["publisher"]))
            if src.get("publication_date"):
                details.append(str(src["publication_date"]))
            # Fetch failures remain in the audit payload/logs, not repeated as
            # implementation diagnostics under each client-facing source link.
            if details:
                detail_p = doc.add_paragraph(" · ".join(details))
                detail_p.paragraph_format.left_indent = Inches(0.25)
                detail_p.paragraph_format.space_after = Pt(5)
                for r in detail_p.runs:
                    r.font.size = Pt(8); r.font.color.rgb = RGBColor.from_string(MUTED)

    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


@router.post("")
async def generate_docx(request: Request):
    started = time.perf_counter()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid request body"}, status_code=400)
    report = str(body.get("report") or "")
    if not report.strip():
        return JSONResponse({"error": "No report content"}, status_code=400)
    normalized_report = report.strip().casefold()
    if normalized_report in _FAILURE_SENTINELS or normalized_report.startswith("## report generation error"):
        return JSONResponse({"error": "The report failed to generate, so there is nothing to export yet. Please regenerate the report."}, status_code=422)
    _docx_charts_in = body.get("charts") if isinstance(body.get("charts"), list) else []
    report, _docx_charts, _chart_heal = sanitize_charts_for_publication(
        report, _docx_charts_in, body.get("sources", []), question=str(body.get("question") or ""),
        require_publisher_verification=True,
    )
    if _chart_heal["removedCharts"] or _chart_heal["prunedPoints"]:
        log.warning("DOCX: export gate removed %d unsupported chart(s) and %d chart point(s) instead of failing; %d chart(s) kept",
                    _chart_heal["removedCharts"], _chart_heal["prunedPoints"], len(_docx_charts))
    chart_findings = _chart_heal["remainingFindings"]
    if chart_findings:
        return JSONResponse({
            "error": "This report contains chart values that conflict with verification status or lack supporting source evidence. Resolve the findings before export.",
            "publicationStatus": "FAIL", "findings": chart_findings[:20], "exported": False,
        }, status_code=422)
    try:
        content = build_docx_report(
            report=report,
            title=str(body.get("title") or "Research Report"),
            question=str(body.get("question") or ""),
            summary=str(body.get("summary") or ""),
            key_stats=body.get("keyStats") if isinstance(body.get("keyStats"), list) else [],
            charts=_docx_charts,
            sources=body.get("sources") if isinstance(body.get("sources"), list) else [],
            presentation=body.get("presentation") if isinstance(body.get("presentation"), dict) else {},
            source_documents=body.get("sourceDocuments") if isinstance(body.get("sourceDocuments"), list) else [],
            images=body.get("images") if isinstance(body.get("images"), list) else [],
            file_images=body.get("fileImages") if isinstance(body.get("fileImages"), list) else [],
            report_tier=str(body.get("reportTier") or "detailed"),
        )
    except Exception as exc:
        log.exception("DOCX: build failed")
        return JSONResponse({"error": "Word document export failed. Please retry or check the backend logs."}, status_code=500)
    filename_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    log.info("DOCX: ready title=%r bytes=%d elapsed_ms=%.0f", str(body.get("title") or "Research Report")[:80], len(content), (time.perf_counter() - started) * 1000)
    return Response(
        content=content,
        media_type=DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="growth-gradual-report-{filename_date}.docx"', "Cache-Control": "no-store"},
    )
