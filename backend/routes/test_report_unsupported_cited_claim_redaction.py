import routes.report as report


def test_unsupported_cited_numeric_sentence_is_removed_and_disclosed():
    text = (
        "## Sector rotation\n\n"
        "Nifty Auto declined 17.3% in March [S1]. "
        "The discussion also compares demand and valuation trends [S2]."
    )
    sources = [
        {
            "id": "S1",
            "_citedClaimGroups": [{
                "groupId": "unsupported",
                "sourceIds": ["S1"],
                "claim": "Nifty Auto declined 17.3% in March",
                "sentence": "Nifty Auto declined 17.3% in March [S1].",
                "claimTokens": ["percent:17.3"],
                "supportByToken": {},
            }],
        },
    ]

    cleaned, _charts, count = report._sanitize_unverified_financial_claims(text, [], sources)

    assert count == 1
    assert "Nifty Auto declined 17.3%" not in cleaned
    assert "demand and valuation trends" in cleaned
    assert cleaned.count("Quantitative evidence limitation:") == 1


def test_unsupported_claim_redaction_is_idempotent():
    text = (
        "## Sector rotation\n\n"
        "Nifty Auto declined 17.3% in March [S1].\n"
        "> **Quantitative evidence limitation:** one or more numerical statements were omitted because their cited sources did not verify the figures."
    )
    sources = [{
        "id": "S1",
        "_citedClaimGroups": [{
            "groupId": "unsupported",
            "sourceIds": ["S1"],
            "claim": "Nifty Auto declined 17.3% in March",
            "sentence": "Nifty Auto declined 17.3% in March [S1].",
            "claimTokens": ["percent:17.3"],
            "supportByToken": {},
        }],
    }]

    first, _, first_count = report._sanitize_unverified_financial_claims(text, [], sources)
    second, _, second_count = report._sanitize_unverified_financial_claims(first, [], sources)

    assert first_count == 1
    assert second_count == 0
    assert second.count("Quantitative evidence limitation:") == 1
