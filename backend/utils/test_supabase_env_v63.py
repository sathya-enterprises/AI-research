from utils.supabase_env import clean_supabase_url, is_placeholder
import utils.supabase_env as se


def test_clean_url_strips_quotes_paths_and_slashes():
    assert clean_supabase_url('  "https://abc.supabase.co/rest/v1/" ') == "https://abc.supabase.co"
    assert clean_supabase_url("abc.supabase.co") == "https://abc.supabase.co"
    assert clean_supabase_url("") == ""


def test_placeholder_detected():
    assert is_placeholder("https://your-project.supabase.co")
    assert not is_placeholder("https://abcdxyz.supabase.co")


def test_screener_url_falls_back_when_override_does_not_resolve(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://good.supabase.co")
    monkeypatch.setenv("SCREENER_SUPABASE_URL", "https://stale.supabase.co")
    monkeypatch.setattr(se, "host_resolves", lambda url, timeout=4.0: (url.startswith("https://good"), "x"))
    assert se.screener_url() == "https://good.supabase.co"


def test_screener_url_keeps_working_override(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://good.supabase.co")
    monkeypatch.setenv("SCREENER_SUPABASE_URL", "https://other.supabase.co")
    monkeypatch.setattr(se, "host_resolves", lambda url, timeout=4.0: (True, "x"))
    assert se.screener_url() == "https://other.supabase.co"
