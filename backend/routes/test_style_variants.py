import random

from routes.style_variants import (
    PALETTES, VARIANT_COUNT, current_style_variant, describe_variant, normalize_style_variant, pick_style_variant,
    set_current_style_variant, variant_css, variant_from_html, variant_print_css, variant_theme,
)


def _luminance(hex_color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_there_are_thousands_of_distinct_variants():
    assert VARIANT_COUNT >= 200
    names = {describe_variant(n)["name"] + describe_variant(n)["table"] for n in range(1, VARIANT_COUNT + 1)}
    assert len(names) == VARIANT_COUNT


def test_every_palette_is_readable():
    for name, primary, accent, soft, paper, card, rule, ink, muted in PALETTES:
        assert _contrast("#FFFFFF", primary) >= 7, name          # cover and band heading text
        assert _contrast(ink, paper) >= 7, name                  # body text
        assert _contrast(ink, card) >= 7, name                   # table cells
        assert _contrast(muted, paper) >= 4.5, name              # captions, labels
        assert _contrast(accent, paper) >= 3, name               # kickers, bars on the page
        assert _contrast(soft, primary) >= 4.5, name             # kicker on the cover / band
        assert _contrast(primary, paper) >= 7, name              # table header text, sub-headings


def test_variant_number_validation_and_context():
    assert normalize_style_variant("12") == 12
    assert normalize_style_variant(0) is None and normalize_style_variant(VARIANT_COUNT + 1) is None
    assert normalize_style_variant("abc") is None and normalize_style_variant(None) is None
    assert 1 <= pick_style_variant(random.Random(4)) <= VARIANT_COUNT
    assert set_current_style_variant(7) == 7 and current_style_variant() == 7
    assert set_current_style_variant(None) is None and current_style_variant() is None


def test_no_variant_changes_nothing():
    for style in ("market_currents", "feature_article", None):
        assert variant_css(style, None) == "" and variant_theme(style, None) == {} and variant_print_css(style, None) == ""


def test_variant_css_theme_and_print_follow_the_palette():
    spec = describe_variant(42)
    css = variant_css("market_currents", 42)
    assert spec["colors"]["primary"] in css and "[data-style-variant]" in css
    theme = variant_theme("market_currents", 42)
    assert theme["chartColors"][0] == spec["colors"]["primary"] and theme["chartColors"][1] == spec["colors"]["accent"]
    assert spec["colors"]["paper"] in variant_print_css("market_currents", 42)
    assert spec["colors"]["accent"] in variant_css("feature_article", 42)


def test_rendered_html_carries_the_variant_and_the_pdf_reads_it_back():
    from routes.html_report import build_html_report
    report = "## Overview\n\nGold imports rose 24% in 2025-26 [S1].\n\n## Outlook\n\nDemand may recover [S1]."
    try:
        set_current_style_variant(123)
        html_doc = build_html_report(report, "Gold", "Gold imports", "", [], [], [], report_style="market_currents")
    finally:
        set_current_style_variant(None)
    assert variant_from_html(html_doc) == 123
    assert describe_variant(123)["colors"]["primary"] in html_doc
    plain = build_html_report(report, "Gold", "Gold imports", "", [], [], [], report_style="market_currents")
    assert variant_from_html(plain) is None and "data-style-variant" not in plain
