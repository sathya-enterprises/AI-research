"""PDF Data Sources appendix: id, title and URL only; duplicate URLs share one entry; every citation still resolves.
The HTML preview keeps its full entries, and the source records keep all their metadata."""
import copy
import re

from routes.html_report import _render_sources_appendix, build_html_report

SOURCES = [
    {"id": "S1", "title": "Ministry statistics release", "url": "https://example.gov.in/release?id=7&utm_source=x",
     "publisher": "Ministry", "publication_date": "2026-03-01", "authority_tier": "primary official",
     "reliability_score": 0.92, "reliability_note": "regulator / official statistics"},
    {"id": "S2", "title": "Industry body note", "url": "https://industry.example.org/note", "publisher": "Body",
     "publication_date": "2025-11-02", "reliability_score": 0.7},
    {"id": "S3", "title": "Ministry statistics release (mirror)", "url": "https://www.example.gov.in/release?id=7",
     "publisher": "Ministry", "reliability_score": 0.92},
    {"id": "S4", "title": "Uploaded workbook", "url": "", "evidence_origin": "uploaded_attachment"},
]


def _entries(html: str) -> list[tuple[str, str]]:
    section = html[html.find('id="data-sources"'):]
    return re.findall(r'<li id="source-(S\d+)"[^>]*>(.*?)</li>', section, flags=re.S)


def test_compact_entries_show_only_id_title_and_url():
    rendered = _render_sources_appendix(SOURCES, source_registry=SOURCES, placement="appendix", compact=True)
    assert "gg-source-metadata" not in rendered
    for word in ("Reliability", "Tier:", "Published:", "2026-03-01", "regulator / official statistics"):
        assert word not in rendered
    assert "[S2] Industry body note" in rendered and 'href="https://industry.example.org/note"' in rendered
    assert "(No public URL supplied)" in rendered or "User-provided" not in rendered


def test_sources_with_the_same_normalised_url_share_one_entry_and_keep_every_anchor():
    rendered = _render_sources_appendix(SOURCES, source_registry=SOURCES, placement="appendix", compact=True)
    entries = _entries(rendered)
    assert [main for main, _ in entries] == ["S1", "S2", "S4"]
    assert "[S1, S3] Ministry statistics release" in rendered
    assert '<span id="source-S3" class="gg-source-alias"></span>' in rendered   # #source-S3 still resolves
    assert "Total sources: 4" in rendered                                       # ids are counted, not entries


def test_different_pages_of_one_site_are_not_merged():
    sources = [{"id": "S1", "title": "A", "url": "https://site.example/a?id=1"},
               {"id": "S2", "title": "B", "url": "https://site.example/a?id=2"}]
    entries = _entries(_render_sources_appendix(sources, source_registry=sources, compact=True))
    assert [main for main, _ in entries] == ["S1", "S2"]


def test_the_html_preview_keeps_its_full_entries():
    rendered = _render_sources_appendix(SOURCES, source_registry=SOURCES, placement="appendix")
    assert "gg-source-metadata" in rendered and "Reliability" in rendered
    assert len(_entries(rendered)) == 4                                          # nothing merged in the preview


def test_rendering_never_changes_the_source_records():
    before = copy.deepcopy(SOURCES)
    _render_sources_appendix(SOURCES, source_registry=SOURCES, placement="appendix", compact=True)
    assert SOURCES == before


def test_every_cited_id_resolves_in_the_compact_pdf_html():
    # the full builder's source registry already merges same-identity sources before the appendix; these are
    # distinct sources, and each citation must land on its own entry and URL
    sources = sorted([s for s in SOURCES if s["id"] != "S3"] + [
        {"id": "S3", "title": "Second ministry table", "url": "https://example.gov.in/table-2", "reliability_score": 0.9}],
        key=lambda s: int(s["id"][1:]))  # manifests arrive in id order (the registry numbers by position)
    report = ("## Findings\n\nOutput rose 12% in FY25 [S1]. A second table confirms it [S3]. "
              "The industry note disagrees [S2]. The upload adds detail [S4].\n")
    html = build_html_report(report, "Title", "Question", "", [], [], [], sources=sources, compact_sources=True)
    url_of = {}
    for main, body in _entries(html):
        href = re.search(r'href="([^"]+)"', body)
        for sid in [main, *re.findall(r'id="source-(S\d+)" class="gg-source-alias"', body)]:
            url_of[sid] = href.group(1).replace("&amp;", "&") if href else ""
        assert "gg-source-metadata" not in body
    expected = {s["id"]: s["url"] for s in sources}
    assert set(url_of) == set(expected)
    assert all(url_of[sid] == expected[sid] for sid in expected), url_of
    preview = build_html_report(report, "Title", "Question", "", [], [], [], sources=sources)
    assert "gg-source-metadata" in preview[preview.find('id="data-sources"'):]   # preview unchanged
