from routes.html_report import _render_sources_appendix


def test_sources_appendix_is_a_compact_numbered_link_list():
    sources = [
        {"id": "S1", "title": "First source title", "url": "https://example.com/first", "publisher": "Example"},
        {"id": "S2", "title": "Second source title", "url": "https://example.com/second", "publisher": "Example"},
    ]
    rendered = _render_sources_appendix(
        sources, source_registry=sources, report_tier="detailed", placement="appendix"
    )

    assert 'class="gg-source-list"' in rendered
    assert 'id="source-S1"' in rendered and 'id="source-S2"' in rendered
    assert '<span class="gg-source-title-text">First source title</span> (<a' in rendered
    assert '<a class="gg-source-link" href="https://example.com/first"' in rendered
    assert rendered.count('class="gg-source-line"') == 2
    assert "gg-source-provenance" not in rendered
    assert "verify the linked page" not in rendered.lower()
    assert "source-identity" not in rendered


def test_brief_appendix_keeps_only_cited_ids_without_changing_anchor_ids():
    sources = [
        {"id": "S1", "title": "Uncited source", "url": "https://example.com/unused"},
        {"id": "S2", "title": "Cited source", "url": "https://example.com/used"},
    ]
    rendered = _render_sources_appendix(
        sources, source_registry=sources, report_tier="brief", cited_source_ids={"S2"}
    )

    assert "Cited source" in rendered
    assert "Uncited source" not in rendered
    assert 'id="source-S2"' in rendered
    assert 'href="https://example.com/used"' in rendered


def test_missing_source_url_is_honest_and_non_warning_style():
    sources = [{"id": "S3", "title": "Source with no URL", "url": ""}]
    rendered = _render_sources_appendix(sources, source_registry=sources, report_tier="detailed")

    assert "Source with no URL" in rendered
    assert "(No public URL supplied)" in rendered
    assert "WARNING" not in rendered
    assert "CAUTION" not in rendered


def test_source_appendix_does_not_render_internal_extraction_diagnostics():
    sources = [{
        "id": "S1", "title": "Publisher research page", "url": "https://example.com/report",
        "pageFetchStatus": "empty_extraction", "evidenceOrigin": "search_extract_only",
        "evidenceAvailable": True, "claimEvidenceMatched": False,
        "unmatchedNumericClaims": ["ratio:3.2"],
    }]
    rendered = _render_sources_appendix(
        sources, source_registry=sources, report_tier="detailed", placement="appendix"
    )
    assert "Publisher research page" in rendered
    assert "https://example.com/report" in rendered
    assert "Publisher page could not be fully extracted" not in rendered
    assert "empty extraction" not in rendered.lower()
    assert "search-index excerpt only" not in rendered.lower()
    assert "Confirm cited figures at the source" not in rendered
    assert "gg-source-provenance" not in rendered
