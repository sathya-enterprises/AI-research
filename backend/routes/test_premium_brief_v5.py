"""V5: evidence-safe visuals and conservative PDF pagination regressions."""
from routes.pdf import _trim_furniture_only_middle_pages

SOURCES = [{"id":"S1", "title":"Market data", "url":"https://example.org/source"}]


def test_blank_middle_page_removed_but_real_content_preserved():
    import fitz
    pdf = fitz.open()
    for content in ("First", "", "Last"):
        page = pdf.new_page()
        if content:
            page.insert_text((80, 120), content)
    cleaned = fitz.open(stream=_trim_furniture_only_middle_pages(pdf.tobytes()), filetype="pdf")
    assert len(cleaned) == 2
    assert "First" in cleaned[0].get_text() and "Last" in cleaned[1].get_text()
