import fitz

from routes import pdf as P


def _pdf(pages):
    doc = fitz.open()
    for lines, top in pages:
        page = doc.new_page(width=595, height=842)
        page.draw_rect(page.rect, color=None, fill=(0.96, 0.94, 0.91))  # paper background, as the house style draws it
        y = 60
        if top:
            page.insert_text((50, y), top, fontsize=9)
            y += 20
        for _ in range(lines):
            page.insert_text((50, y), "Body text line of the report with enough characters to count.", fontsize=10)
            y += 14
        page.insert_text((280, 820), "12", fontsize=7)  # footer furniture
    return doc.tobytes()


def test_a_section_after_a_mostly_empty_page_is_found():
    data = _pdf([(50, ""), (4, ""), (50, "S E C T I O N  0 3"), (50, ""), (52, "S E C T I O N  0 4"), (10, "")])
    assert P._sparse_section_starts(data) == ["03"]


def test_full_pages_before_sections_are_left_alone():
    data = _pdf([(50, ""), (50, ""), (50, "S E C T I O N  0 2"), (5, "")])
    assert P._sparse_section_starts(data) == []


def test_flowing_marks_only_the_named_section_and_adds_the_override():
    html_doc = ('<html><head></head><body class="gg-house--market_currents"><main class="gg-report-sections">'
                '<section id="a" class="gg-section gg-reveal"><div class="gg-section-heading" data-section-number="02"></div></section>'
                '<section id="b" class="gg-section gg-reveal"><div class="gg-section-heading" data-section-number="03"></div></section>'
                '</main></body></html>')
    out = P._with_flowing_sections(html_doc, ["03"])
    assert '<section id="b" class="gg-section gg-section--flow gg-reveal">' in out
    assert '<section id="a" class="gg-section gg-reveal">' in out
    assert 'id="gg-pdf-section-flow"' in out and "break-before: auto" in out
    assert P._with_flowing_sections(html_doc, ["09"]) == html_doc


def test_render_pass_renders_again_only_when_a_section_can_move_up():
    calls = []
    sparse = _pdf([(50, ""), (4, ""), (50, "S E C T I O N  0 3"), (10, "")])
    full = _pdf([(50, ""), (50, ""), (10, "")])
    html_doc = ('<html><head></head><body><section id="b" class="gg-section x">'
                '<div data-section-number="03"></div></section></body></html>')

    def render(doc):
        calls.append(doc)
        return full if "gg-section--flow" in doc else sparse
    assert P._render_with_flowing_sections(render, html_doc) == full and len(calls) == 2
    calls.clear()
    assert P._render_with_flowing_sections(lambda doc: calls.append(doc) or full, html_doc) == full and len(calls) == 1
