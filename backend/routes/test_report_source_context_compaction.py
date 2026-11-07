from routes.report import (
    _compact_report_source_blocks,
    _compact_report_user_prompt,
    _filter_relevant_sources,
)


def _source_block(source_id: int, text: str) -> str:
    return (
        f"- [S{source_id}] **Exchange filing {source_id}**\n"
        f"Source: https://nsearchives.nseindia.com/corporate/filing-{source_id}.pdf\n"
        f"DIRECT PUBLISHER PAGE TEXT (fetched from this URL):\n{text}"
    )


def test_source_compactor_keeps_relevant_middle_filing_and_its_real_id():
    question = "Explain promoter stake sales in India in the past 8 quarters"
    blocks = []
    for source_id in range(1, 41):
        body = "generic exchange filing text. " * 120
        if source_id == 20:
            body = (
                "Promoter seller sold 4,00,000 shares on 12 October 2025 under Regulation 29. "
                "UNIQUE_MIDDLE_EVENT_FACT. " + body
            )
        blocks.append(_source_block(source_id, body))

    compacted, kept, omitted = _compact_report_source_blocks(
        "\n\n---\n\n".join(blocks), question, 12_000,
    )

    assert len(compacted) <= 12_000
    assert kept < len(blocks)
    assert omitted == len(blocks) - kept
    assert "[S20]" in compacted
    assert "UNIQUE_MIDDLE_EVENT_FACT" in compacted
    assert "Do not cite omitted source IDs" in compacted


def test_fallback_prompt_compactor_preserves_middle_evidence_and_instruction():
    question = "Explain promoter stake sales in India in the past 8 quarters"
    blocks = []
    for source_id in range(1, 41):
        body = "generic exchange filing text. " * 120
        if source_id == 20:
            body = (
                "Promoter seller sold 4,00,000 shares on 12 October 2025 under Regulation 29. "
                "UNIQUE_MIDDLE_EVENT_FACT. " + body
            )
        blocks.append(_source_block(source_id, body))
    prompt = (
        f"Research Question / Topic: {question}\n"
        + "brief setup context. " * 500
        + "\nSupplementary web sources (40 results):\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\nINSTRUCTIONS:\nCite the exact source IDs for transaction facts."
    )

    compacted = _compact_report_user_prompt(prompt)

    assert len(compacted) <= 30_000
    assert "[S20]" in compacted
    assert "UNIQUE_MIDDLE_EVENT_FACT" in compacted
    assert "Cite the exact source IDs" in compacted


def test_promoter_event_filter_drops_pledges_and_out_of_window_sales():
    question = (
        "Explain promoter stake sales and list promoter exits in the past 8 quarters"
    )
    sources = [
        {
            "title": "Promoter Regulation 29 sale disclosure",
            "url": "https://nsearchives.nseindia.com/corporate/recent-sale.pdf",
            "fullContent": (
                "Promoter seller sold 12,000 shares on 12 October 2025 under Regulation 29."
            ),
        },
        {
            "title": "Promoter encumbrance disclosure",
            "url": "https://www.bseindia.com/corporates/pledge.pdf",
            "fullContent": (
                "Promoter created a non-disposal undertaking and disclosed a pledge; no shares were sold."
            ),
        },
        {
            "title": "Regulation 29 disclosure",
            "url": "https://www.bseindia.com/corporates/old-sale.pdf",
            "fullContent": (
                "Promoter seller sold 74,000 shares on August 25, 2023 under Regulation 29."
            ),
        },
        {
            "title": "Quarterly shareholding pattern",
            "url": "https://www.bseindia.com/corporates/pattern.pdf",
            "fullContent": "Promoter shareholding was 62 percent in this quarter.",
        },
    ]

    filtered = _filter_relevant_sources(sources, question, intent=None, report_tier="in_depth")

    assert [row["url"] for row in filtered] == [sources[0]["url"]]


def test_promoter_strict_filter_requires_direct_promoter_share_sale_evidence():
    question = "Explain promoter stake sales and list promoter exits in the past 8 quarters"
    base = {
        "url": "https://nsearchives.nseindia.com/corporate/filing.pdf",
        "pageFetchFinalUrl": "https://nsearchives.nseindia.com/corporate/filing.pdf",
        "pageFetchStatus": "ok",
        "pageContent": "Exchange filing " * 30,
    }
    valid = {
        **base,
        "title": "Promoter sale disclosure under Regulation 29",
        "pageContent": (
            "Under Regulation 29, promoter seller Anita Rao sold 12,000 equity shares "
            "on 12 October 2025. The disclosure identifies the target company, the "
            "seller, the post-transaction shareholding and the paid-up equity capital. "
            "This is the direct publisher filing page."
        ),
    }
    business_sales = {
        **base,
        "title": "Quarterly results and promoter shareholding",
        "pageContent": (
            "Vijay Sales revenue increased during the quarter.\n\n"
            "Promoter shareholding was 51 percent. No shares were sold."
        ),
    }
    non_promoter_seller = {
        **base,
        "title": "Share sale under Regulation 29",
        "pageContent": (
            "Private equity seller sold 12,000 equity shares on 12 October 2025.\n\n"
            "The promoter group continues to manage the company."
        ),
    }
    unverified_page = {
        **valid,
        "url": "https://www.bseindia.com/xml-data/corpfiling/sale.pdf",
        "pageFetchFinalUrl": "https://www.bseindia.com/xml-data/corpfiling/sale.pdf",
        "pageFetchStatus": "timeout",
    }

    filtered = _filter_relevant_sources(
        [valid, business_sales, non_promoter_seller, unverified_page],
        question,
        intent=None,
        require_context=True,
        report_tier="in_depth",
    )

    assert filtered == [valid]
