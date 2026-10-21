from pathlib import Path


def test_report_parser_initializes_images_before_processing_provider_images():
    """Optional image fields are initialized before provider data is consumed."""
    source = Path(__file__).resolve().parents[0].joinpath("report.py").read_text(encoding="utf-8")
    parse_start = source.index("# Initialize every optional envelope field immediately.")
    image_init = source.index("images: list = []", parse_start)
    image_selection = source.index("original_images_list = parsed.get(\"images\") or []", parse_start)
    assert image_init < image_selection
    assert "if len(images) > 4:" not in source[parse_start:image_selection]


def test_primary_chart_path_normalizes_provider_shapes_before_density_assessment():
    source = Path(__file__).resolve().parents[0].joinpath("report.py").read_text(encoding="utf-8")
    normalized = source.index("chart = _normalise_inline_chart_spec(raw_chart) or raw_chart")
    density = source.index("assessment = chart_density_assessment(chart, report_tier)", normalized)
    assert normalized < density
