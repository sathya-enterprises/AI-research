from routes.html_report import _render_image_block
from routes.report import _explicit_action_target, _normalize_action_heading


def test_image_source_link_uses_source_page_not_asset_url():
    registry = [{"id":"S1","title":"Example Article","publisher":"Example News","url":"https://example.com/article"}]
    html = _render_image_block({"url":"https://cdn.example.com/photo.jpg", "sourceUrl":"https://example.com/article", "sourceTitle":"Example Article", "publisher":"Example News", "caption":"Photo"}, 1, registry)
    assert 'href="https://example.com/article"' in html
    assert 'href="https://cdn.example.com/photo.jpg"' not in html
    assert 'gg-source-page-link' in html


def test_image_without_source_page_does_not_link_asset_as_source():
    html = _render_image_block({"url":"https://cdn.example.com/photo.jpg", "caption":"Photo"}, 1, [])
    assert 'href="https://cdn.example.com/photo.jpg"' not in html
    assert html == ''  # images without publisher/source-page provenance are suppressed


def test_action_target_matches_numbered_heading():
    assert _normalize_action_heading('3. Geopolitical & Operational Risks') == _normalize_action_heading('Geopolitical & Operational Risks')
    assert _explicit_action_target('Target section: "Geopolitical & Operational Risks"\nInstruction: add a table') == 'Geopolitical & Operational Risks'
