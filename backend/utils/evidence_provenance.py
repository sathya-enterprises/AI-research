"""Trusted provenance checks shared by report and export validators."""
from __future__ import annotations


_CALCULATED_SECTOR_ORIGIN = "calculated_from_daily_market_data"
_CALCULATED_SECTOR_INTERNAL_URL = "internal://verified-sector-quarterly-returns"


def is_trusted_calculated_sector_source(source: object) -> bool:
    """Recognize the server-generated, quote-bearing sector return dataset.

    This dataset is calculated from fetched daily index observations rather
    than extracted from a publisher webpage. Requiring the stable origin,
    dataset identity, and exact chart evidence excerpt lets chart validators
    verify its values without describing it as a fetched publisher page.
    """
    if not isinstance(source, dict):
        return False
    origin = str(
        source.get("evidence_origin") or source.get("evidenceOrigin")
        or source.get("evidenceExcerptOrigin") or ""
    ).strip().casefold()
    if origin != _CALCULATED_SECTOR_ORIGIN:
        return False
    excerpt = str(source.get("chartEvidenceExcerpt") or "").strip()
    if not excerpt:
        return False

    title = str(source.get("title") or "").strip().casefold()
    url = str(source.get("url") or "").strip().casefold()
    if url == _CALCULATED_SECTOR_INTERNAL_URL and "calculated nse sector returns" in title:
        return True

    kind = str(source.get("kind") or source.get("source_type") or source.get("sourceType") or "").strip().casefold()
    publisher = str(source.get("publisher") or "").strip().casefold()
    return (
        not url
        and kind == "verified data"
        and publisher == "calculated from daily market data"
        and "sector return" in title
    )
