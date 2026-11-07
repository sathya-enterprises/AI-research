"""Regression tests for surgical report Action image intent."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from routes.report import _action_requests_image_change


def test_negative_image_preservation_is_not_image_edit():
    cases = [
        "Do NOT change unrelated charts or images.",
        "Do not modify any images.",
        "Preserve the existing images.",
        "Leave all unrelated images unchanged.",
        "Do NOT alter charts or images.",
        "Keep unrelated images unchanged.",
    ]
    for text in cases:
        assert not _action_requests_image_change(text), text


def test_positive_image_operations_are_image_edits():
    cases = [
        "Replace the image in Section 2.",
        "Remove the image and replace it with a properly sourced EV image.",
        "Change the image in this section.",
        "Swap the current image for another relevant image.",
        "Update the image in Section 3.",
        "Remove the existing image.",
        "Use a new relevant image with a proper source link.",
    ]
    for text in cases:
        assert _action_requests_image_change(text), text


def test_mixed_instruction_triggers_only_for_positive_image_clause():
    text = (
        "Edit only Section 2. Do NOT change unrelated charts or images. "
        "Replace the image in Section 2 with a properly sourced EV image."
    )
    assert _action_requests_image_change(text)
