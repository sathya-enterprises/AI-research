import json
import sys
from pathlib import Path

from starlette.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.report import _explicit_action_chart_target, _remove_named_action_chart


def test_chart_title_action_removes_only_the_named_chart_and_keeps_registry_aligned():
    report = (
        "## Findings\n"
        "\nSupported narrative before the visuals.\n"
        "\n**Annual Solar Capacity Additions (FY19 to FY26)**\n"
        "[CHART_2]\n"
        "\nCited narrative after the visual [S4].\n"
        "\n## Comparison\n\n[CHART_1] and [CHART_3]\n"
    )
    charts = [
        {"title": "Cumulative Solar vs Wind Installed Capacity"},
        {"title": "Annual Solar Capacity Additions (FY19 to FY26)"},
        {"title": "Supporting Data Table", "type": "table"},
    ]
    instruction = "Remove only the chart titled 'Annual Solar Capacity Additions (FY19 to FY26)'."

    assert _explicit_action_chart_target(instruction) == charts[1]["title"]
    response = _remove_named_action_chart(report, charts, charts[1]["title"])
    payload = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert payload["chartRemoved"] is True
    assert payload["surgicalEdit"] == "remove_named_chart"
    findings, comparison = payload["report"].split("## Comparison", 1)
    assert "[CHART_1]" not in findings
    assert "[CHART_2]" in comparison  # former chart 3 was renumbered after removing chart 2
    assert "[CHART_3]" not in payload["report"]
    assert "[CHART_1] and [CHART_2]" in payload["report"]
    assert "Annual Solar Capacity Additions (FY19 to FY26)" not in payload["report"]
    assert "Supported narrative before the visuals." in payload["report"]
    assert "Cited narrative after the visual [S4]." in payload["report"]
    assert [chart["title"] for chart in payload["charts"]] == [
        "Cumulative Solar vs Wind Installed Capacity",
        "Supporting Data Table",
    ]


def test_chart_title_action_fails_closed_when_title_is_missing_or_ambiguous():
    report = "## Findings\n\n[CHART_1]"
    missing = _remove_named_action_chart(report, [{"title": "Known chart"}], "Unknown chart")
    duplicate = _remove_named_action_chart(
        report,
        [{"title": "Known chart"}, {"title": "Known chart"}],
        "Known chart",
    )

    for response in (missing, duplicate):
        assert isinstance(response, JSONResponse)
        assert response.status_code == 422
        payload = json.loads(response.body.decode("utf-8"))
        assert payload["preserved"] is True
        assert "report" not in payload
