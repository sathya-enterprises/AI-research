from routes.source_manifest import build_source_registry, normalise_source_manifest
from utils.source_ids import (
    chart_source_ids, manifest_ids, next_source_number, normalize_sid, reconcile_chart_source_ids,
    unresolved_chart_references,
)

MANIFEST = [
    {"id": "S1", "title": "A", "url": "https://a.example/1"},
    {"id": "S3", "title": "C", "url": "https://c.example/3"},
    {"id": "S7", "title": "G", "url": "https://g.example/7"},
]


def _chart(**extra):
    base = {"type": "bar", "title": "T", "series": [{"name": "s", "data": [{"label": "Bank", "value": 3.2}]}]}
    base.update(extra)
    return base


def test_normalize_and_chart_source_ids_read_every_location():
    chart = _chart(sources=["S1"], source_ids="S3")
    chart["series"][0]["data"][0].update(evidenceSource="s7", evidenceQuote="Bank | 3.2")
    chart["derivedFrom"] = [{"sourceId": "S9"}]
    assert normalize_sid(" [s12] ") == "S12"
    assert chart_source_ids(chart) == ["S1", "S3", "S7", "S9"]


def test_point_pointing_at_missing_source_is_repointed_to_the_one_that_holds_the_quote():
    chart = _chart(sourceIds=["S2"])
    chart["series"][0]["data"][0].update(evidenceSource="S2", evidenceQuote="Bank | 3.2 | 2025 results", evidenceValue="3.2")
    texts = {"S1": "unrelated", "S7": "Header\nBank | 3.2 | 2025 results\nfooter"}
    out, meta = reconcile_chart_source_ids([chart], MANIFEST, texts)
    point = out[0]["series"][0]["data"][0]
    assert point["evidenceSource"] == "S7"
    assert out[0]["sourceIds"] == ["S7"] and out[0]["source"] == ["S7"]
    assert meta["pointsRepointed"] == 1
    assert chart["series"][0]["data"][0]["evidenceSource"] == "S2", "input must not be mutated"


def test_unresolvable_quote_is_dropped_so_grounding_can_retry():
    chart = _chart(sourceIds=["S3"])
    chart["series"][0]["data"][0].update(evidenceSource="S99", evidenceQuote="Bank | 3.2 | not anywhere")
    out, meta = reconcile_chart_source_ids([chart], MANIFEST, {"S1": "x", "S3": "y"})
    point = out[0]["series"][0]["data"][0]
    assert "evidenceSource" not in point and "evidenceQuote" not in point
    assert point["value"] == 3.2
    assert out[0]["sourceIds"] == ["S3"] and meta["pointsUngrounded"] == 1


def test_declared_ids_kept_while_any_point_is_unquoted():
    chart = _chart(sourceIds=["S3"])
    chart["series"][0]["data"] = [
        {"label": "A", "value": 1.0, "evidenceSource": "S7", "evidenceQuote": "A | 1.0 | quote text"},
        {"label": "B", "value": 2.0},
    ]
    out, _ = reconcile_chart_source_ids([chart], MANIFEST, {"S7": "A | 1.0 | quote text"})
    assert out[0]["sourceIds"] == ["S3", "S7"]


def test_all_quoted_points_narrow_declared_ids_to_the_real_source():
    chart = _chart(sourceIds=["S1", "S3"])
    chart["series"][0]["data"] = [{"label": "A", "value": 1.0, "evidenceSource": "S7", "evidenceQuote": "A | 1.0 | quote text"}]
    out, _ = reconcile_chart_source_ids([chart], MANIFEST, {"S7": "A | 1.0 | quote text"})
    assert out[0]["sourceIds"] == ["S7"]


def test_orphan_chart_level_ids_are_removed_from_every_key():
    chart = _chart(sourceIds=["S3", "S42"], source=["S3", "S42"], sources=["S42"])
    out, _ = reconcile_chart_source_ids([chart], MANIFEST)
    assert out[0]["sourceIds"] == ["S3"] and out[0]["source"] == ["S3"] and "sources" not in out[0]
    assert unresolved_chart_references(out, MANIFEST) == []
    assert unresolved_chart_references([chart], MANIFEST)[0]["missing"] == ["S42"]


def test_heatmap_without_series_keeps_declared_ids():
    chart = {"type": "heatmap", "title": "H", "rows": ["a"], "columns": ["b"], "values": [[1.0]], "source": "S7"}
    out, _ = reconcile_chart_source_ids([chart], MANIFEST)
    assert out[0]["sourceIds"] == ["S7"]


def test_unresolved_placeholders_are_not_valid_targets():
    manifest = MANIFEST + [{"id": "S9", "source_status": "unresolved", "title": "missing"}]
    assert "S9" not in manifest_ids(manifest)
    assert next_source_number(manifest) == 10


def test_registry_extension_preserves_existing_ids_with_gaps():
    prior = normalise_source_manifest(build_source_registry([
        {"title": f"Src {n}", "url": f"https://x.example/{n}"} for n in range(1, 6)]))
    cited_only = [prior[0], prior[2], prior[4]]                      # S1, S3, S5
    combined = build_source_registry(cited_only, additional_sources=[{"title": "New", "url": "https://x.example/new"}])
    assert [s["id"] for s in combined] == ["S1", "S3", "S5", "S6"]
