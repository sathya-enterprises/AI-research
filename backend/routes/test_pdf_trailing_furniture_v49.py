from __future__ import annotations

import io

import pytest

fitz = pytest.importorskip("fitz")

from routes.pdf import _trim_trailing_blank_pages


def _two_page_pdf(with_content_on_last: bool) -> bytes:
    doc = fitz.open()
    first = doc.new_page()
    first.insert_text((60, 90), "Report content")
    last = doc.new_page()
    last.insert_text((35, 22), "Growth Gradual | Research Intelligence")
    last.insert_text((35, last.rect.height - 20), "Growth Gradual | Report | 2")
    if with_content_on_last:
        last.draw_rect(fitz.Rect(70, 120, 520, 260), color=(0.1, 0.3, 0.5))
    output = io.BytesIO()
    doc.save(output)
    doc.close()
    return output.getvalue()


def test_trailing_header_footer_only_page_is_removed():
    trimmed = fitz.open(stream=_trim_trailing_blank_pages(_two_page_pdf(False)), filetype="pdf")
    assert len(trimmed) == 1
    assert "Report content" in trimmed[0].get_text()


def test_trailing_page_with_a_chart_or_table_drawing_is_preserved():
    trimmed = fitz.open(stream=_trim_trailing_blank_pages(_two_page_pdf(True)), filetype="pdf")
    assert len(trimmed) == 2
