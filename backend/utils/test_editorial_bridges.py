"""Section bridges carry the report's argument: the bridge call sees the question and the thesis."""
import asyncio
import types

from utils.editorial_pass import EditedSection, editorial_pass


def test_bridges_are_written_against_the_question_and_thesis_and_never_add_numbers():
    seen = {}

    class Gateway:
        async def generate(self, **kwargs):
            seen["prompt"] = kwargs["prompt"]
            return types.SimpleNamespace(ok=True, text='{"b": "Demand explains part of the growth question.", '
                                                       '"c": "Capacity rose 30% [S1]."}')

    sections = [EditedSection(section_id=i, title=t, body=f"{t} body [S1].", summary=f"{t} summary")
                for i, t in (("a", "Traffic"), ("b", "Demand drivers"), ("c", "Airport capacity"))]
    out, report = asyncio.run(editorial_pass(sections, gateway=Gateway(), question="How has air traffic changed?",
                                             thesis="Traffic grew on new airports and low fares."))
    assert "REPORT QUESTION: How has air traffic changed?" in seen["prompt"]
    assert "THESIS: Traffic grew on new airports and low fares." in seen["prompt"]
    bodies = {s.section_id: s.body for s in out}
    assert bodies["b"].startswith("Demand explains part of the growth question.")
    assert not bodies["c"].startswith("Capacity rose")  # a bridge with a number or citation is rejected
    assert report.transitions_added == 1 and report.transitions_rejected == 1
