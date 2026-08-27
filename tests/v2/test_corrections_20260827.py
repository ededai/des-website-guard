"""The 2026-08-27 corrections, pinned.

Three things were wrong, all found by comparing the build against advice given
in an earlier session:

  1. Coverage was bounded to save GitHub Actions minutes, on a PUBLIC repo
     where minutes are free. The constraint belonged to Cole's private repo.
  2. A finding was reported once and then went silent forever, so something
     could stay broken and unmentioned indefinitely.
  3. Only two viewports were tested, missing 320 (where layout actually
     breaks) and tablet entirely.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import report
from des2.checks_break import check_mobile_menu
from des2.checks_layout import check_tap_targets
from des2.config import load_site
from des2.discover import daily_set
from des2.models import (VIEWPORTS, Evidence, Finding, expects_mobile_nav,
                         is_touch, viewport_width)

SGT = timezone(timedelta(hours=8))
DAY0 = datetime(2026, 8, 1, 9, 0, tzinfo=SGT)


# ------------------------------------------------ 1. sweep the whole site
CFG = {
    "name": "T", "base_url": "https://x.com",
    "sitemap_url": "https://x.com/sitemap.xml",
    "wp_api_base": "https://x.com/wp-json/wp/v2",
    "templates": [{"name": "home", "url": "https://x.com/"}],
    "skip_patterns": [],
}


def _fetch(url):
    if "sitemap" in url:
        locs = "".join(f"<url><loc>https://x.com/old{i}/</loc></url>" for i in range(5))
        return f"<urlset>{locs}</urlset>"
    if "/posts" in url and "page=1" in url.replace("per_page=100", ""):
        return json.dumps([{"link": "https://x.com/fresh/"}])
    return json.dumps([])


def test_daily_sweep_covers_pages_nobody_edited():
    """The correction. A quiet break on an untouched page must be reachable."""
    urls, dropped = daily_set(CFG, now=DAY0, fetch=_fetch)
    assert "https://x.com/old3/" in urls, "unedited sitemap pages must be swept"
    assert dropped == 0


def test_value_ordering_survives_the_wider_sweep():
    """Templates first, then edits, then the rest, in case a run is cut short."""
    urls, _ = daily_set(CFG, now=DAY0, fetch=_fetch)
    assert urls[0] == "https://x.com/"
    assert urls.index("https://x.com/fresh/") < urls.index("https://x.com/old0/")


def test_cap_is_now_a_runaway_guard_not_a_budget():
    from des2.discover import SAFETY_CAP
    assert SAFETY_CAP >= 1000, "must comfortably exceed a real site"


# ------------------------------------------------ 2. escalate by age
def _log_with_open_finding(first_seen):
    return {"k": {"key": "k", "check": "booking_path_dead", "url": "https://x.com/book/",
                  "viewport": "phone", "owner": "codi", "status": "open",
                  "summary": "booking form is dead", "first_seen": first_seen.isoformat()}}


def test_silent_for_the_first_week():
    log = _log_with_open_finding(DAY0)
    assert report.escalations(log, now=DAY0 + timedelta(days=6)) == []


def test_escalates_once_at_a_week():
    log = _log_with_open_finding(DAY0)
    out = report.escalations(log, now=DAY0 + timedelta(days=7))
    assert len(out) == 1 and out[0]["escalation_level"] == 1
    # and does not nag again the next day
    assert report.escalations(log, now=DAY0 + timedelta(days=8)) == []


def test_escalates_harder_at_a_fortnight():
    log = _log_with_open_finding(DAY0)
    report.escalations(log, now=DAY0 + timedelta(days=7))
    out = report.escalations(log, now=DAY0 + timedelta(days=14))
    assert len(out) == 1 and out[0]["escalation_level"] == 2
    assert "STILL BROKEN" in report.format_escalation(out[0], now=DAY0 + timedelta(days=14))
    # two mentions total, then quiet again
    assert report.escalations(log, now=DAY0 + timedelta(days=40)) == []


def test_fixed_findings_never_escalate():
    log = _log_with_open_finding(DAY0)
    log["k"]["status"] = "fixed"
    assert report.escalations(log, now=DAY0 + timedelta(days=30)) == []


def test_reopening_resets_the_clock():
    log = _log_with_open_finding(DAY0)
    log["k"].update(status="reopened", reopened_at=(DAY0 + timedelta(days=20)).isoformat(),
                    escalation_level=0)
    assert report.escalations(log, now=DAY0 + timedelta(days=22)) == []
    assert len(report.escalations(log, now=DAY0 + timedelta(days=27))) == 1


def test_escalation_message_names_the_age_and_owner():
    log = _log_with_open_finding(DAY0)
    out = report.escalations(log, now=DAY0 + timedelta(days=7))
    txt = report.format_escalation(out[0], now=DAY0 + timedelta(days=7))
    assert "7 days" in txt and "booking form is dead" in txt and "Codi" in txt


def test_corrupt_timestamp_does_not_crash_escalation():
    log = _log_with_open_finding(DAY0)
    log["k"]["first_seen"] = "not a date"
    assert report.escalations(log, now=DAY0 + timedelta(days=30)) == []


# ------------------------------------------------ heartbeat tells the truth
def test_weekly_all_clear_admits_a_standing_backlog():
    log = _log_with_open_finding(DAY0)
    txt = report.heartbeat_text(165, "TRW", known_open=report.open_count(log))
    assert "1 known issue" in txt and "nothing new broken" in txt


def test_genuinely_clean_site_says_so():
    txt = report.heartbeat_text(165, "TRW", known_open=0)
    assert "nothing broken" in txt and "known issue" not in txt


# ------------------------------------------------ 3. four viewports
def test_the_widths_that_matter_are_covered():
    assert sorted(viewport_width(v) for v in VIEWPORTS) == [320, 390, 768, 1440]


def test_touch_applies_to_every_handheld_width():
    assert is_touch("phone_small") and is_touch("phone") and is_touch("tablet")
    assert not is_touch("desktop")


def test_mobile_nav_expected_only_below_the_site_breakpoint():
    cfg = load_site("trw")
    assert expects_mobile_nav("phone_small", cfg) and expects_mobile_nav("phone", cfg)
    # 768 is above TRW's 760 breakpoint, where the desktop nav shows instead
    assert not expects_mobile_nav("tablet", cfg)
    assert not expects_mobile_nav("desktop", cfg)


class FakePage:
    def __init__(self, payload):
        self._p = payload

    async def evaluate(self, script, arg=None):
        return self._p


@pytest.mark.asyncio
async def test_tap_targets_now_checked_at_320_and_on_tablet():
    payload = [{"sel": "a.icon", "w": 20, "h": 20, "text": "x"}]
    for vp in ("phone_small", "phone", "tablet"):
        assert len(await check_tap_targets(FakePage(payload), "https://x.com/", vp)) == 1
    assert await check_tap_targets(FakePage(payload), "https://x.com/", "desktop") == []


@pytest.mark.asyncio
async def test_menu_check_never_runs_where_the_burger_is_meant_to_be_hidden():
    """Without this, every desktop and tablet page would report a dead menu."""
    cfg = load_site("trw")
    hostile = FakePage({"hasButton": False, "hasDrawer": True, "candidates": 3})
    assert await check_mobile_menu(hostile, "https://x.com/", "desktop", cfg) == []
    assert await check_mobile_menu(hostile, "https://x.com/", "tablet", cfg) == []
    assert len(await check_mobile_menu(hostile, "https://x.com/", "phone_small", cfg)) == 1


# ------------------------------------------------ 4. layout: pre-existing vs new
# Created BY fix 1: sweeping all 165 pages across three touch viewports would
# have produced roughly 2,400 tap-target findings on day one, every one of them
# the same handful of footer icons that have looked that way for months.
from des2.baseline import capture, classify_layout, layout_key, remember_layout  # noqa: E402


def _layout(sel, check="tap_target_small"):
    return Finding(check=check, kind="layout", url="https://x.com/", viewport="phone",
                   summary="small target", evidence=Evidence(selector=sel, numbers={"width_px": 36}))


def test_longstanding_design_stays_quiet():
    old = remember_layout(capture({"has_nav": True}), [_layout("a.footer-social-btn")])
    new, pre = classify_layout([_layout("a.footer-social-btn")], old)
    assert new == [] and len(pre) == 1


def test_a_newly_broken_element_still_alerts():
    old = remember_layout(capture({"has_nav": True}), [_layout("a.footer-social-btn")])
    new, pre = classify_layout([_layout("a.footer-social-btn"), _layout("div.hero", "horizontal_scroll")], old)
    assert [f.check for f in new] == ["horizontal_scroll"]
    assert len(pre) == 1


def test_first_sight_announces_nothing():
    """Day one must not dump the whole site's design history on Ed."""
    new, pre = classify_layout([_layout("a.x"), _layout("a.y")], None)
    assert new == [] and len(pre) == 2


def test_key_is_stable_when_pixels_wobble():
    a = _layout("a.footer-social-btn")
    b = _layout("a.footer-social-btn")
    b.evidence.numbers = {"width_px": 37}      # a pixel of drift between runs
    assert layout_key(a) == layout_key(b)


def test_memory_survives_a_baseline_roundtrip(tmp_path):
    from des2.baseline import load_baseline, save_baseline
    fp = remember_layout(capture({"has_nav": True}), [_layout("a.footer-social-btn")])
    save_baseline("https://x.com/", "phone", fp, root=tmp_path)
    back = load_baseline("https://x.com/", "phone", root=tmp_path)
    assert back.layout_defects == ["tap_target_small|a.footer-social-btn"]
