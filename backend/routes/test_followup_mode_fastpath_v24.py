"""Follow-up chip requests must be recognized as constrained generation."""
from routes.chat import _is_follow_up_generation_request


def test_followup_mode_flag_is_recognized_and_not_inferred_for_normal_chat():
    assert _is_follow_up_generation_request({"_followUpMode": True}) is True
    assert _is_follow_up_generation_request({"_followUpMode": False}) is False
    assert _is_follow_up_generation_request({"messages": []}) is False
    assert _is_follow_up_generation_request(None) is False
