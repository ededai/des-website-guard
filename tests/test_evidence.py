"""
Unit tests for reporters/evidence.py — the single pipeline-boundary evidence
formatter (C-EVID). No network, no browser.

    ./.venv/bin/python -m pytest tests/test_evidence.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporters import evidence


def test_humanize_single_key_dict():
    assert evidence.humanize({"issue": "menu_no_autoclose"}) == "issue: menu_no_autoclose"


def test_humanize_nested_dict_with_samples_no_repr_artifacts():
    out = evidence.humanize({
        "issue": "menu_maroon_leak",
        "samples": [{"state": "focus", "color": "rgb(128,0,32)", "txt": "Book"}],
    })
    assert "issue: menu_maroon_leak" in out
    assert "state=focus" in out
    assert "{" not in out and "}" not in out and "'" not in out


def test_humanize_string_passthrough():
    assert evidence.humanize("already a string") == "already a string"


def test_humanize_none():
    assert evidence.humanize(None) == ""


def test_humanize_list_of_dicts_no_repr_artifacts():
    out = evidence.humanize([{"a": 1}, {"a": 2}])
    assert "{" not in out and "}" not in out and "'" not in out


def test_humanize_list_caps_at_three_with_more_suffix():
    out = evidence.humanize(["one", "two", "three", "four", "five"])
    assert "(+2 more)" in out
    assert "four" not in out and "five" not in out


def test_humanize_truncates_long_output():
    out = evidence.humanize("x" * 5000)
    assert len(out) <= 1804  # 1800 + "..."
    assert out.endswith("...")


def test_humanize_collapses_whitespace():
    out = evidence.humanize("a   b\n\nc")
    assert out == "a b c"


def test_escape_html_basic():
    assert evidence.escape_html("<b>&x</b>") == "&lt;b&gt;&amp;x&lt;/b&gt;"


def test_escape_html_non_string_input():
    assert evidence.escape_html(42) == "42"
