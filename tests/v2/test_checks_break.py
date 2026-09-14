"""Breakage checks, with the documented false positives pinned as regressions.

Two of these tests are the whole reason Des is being rebuilt. If either ever
goes red, the guard has regressed into the thing that made Ed stop trusting it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import checks_break as cb

URL = "https://therightworkshop.com/brands/peugeot/"


class FakePage:
    """Minimal stand-in: evaluate() returns canned values in order."""
    def __init__(self, results, selectors=None):
        self._results = list(results)
        self._selectors = selectors or {}

    async def evaluate(self, script, arg=None):
        if not self._results:
            return None
        return self._results.pop(0)

    async def query_selector(self, sel):
        return self._selectors.get(sel)


# ------------------------------------------------ THE regression: beacon noise
def test_third_party_beacon_failures_are_not_site_faults():
    """The August 2026 incident, encoded.

    A GA beacon aborting at page teardown and a Google Maps tile 404 must
    produce NOTHING. Only the site's own asset counts.
    """
    failures = [
        ("https://www.google-analytics.com/g/collect?v=2", 404),
        ("https://maps.googleapis.com/maps/vt?pb=abc", 404),
        ("https://pixel.wp.com/g.gif?blog=1", 404),
        ("https://static.cloudflareinsights.com/beacon.js", 503),
    ]
    assert cb.check_resource_failures(failures, URL, "desktop") == []


def test_first_party_failure_is_reported_with_its_url():
    failures = [("https://therightworkshop.com/wp-content/uploads/gone.jpg", 404)]
    out = cb.check_resource_failures(failures, URL, "desktop")
    assert len(out) == 1
    assert out[0].evidence.resource.endswith("gone.jpg")
    assert out[0].evidence.status == 404 and out[0].evidence.is_hard()


def test_photon_cdn_counts_as_first_party():
    """i0.wp.com serves the site's own media, so its 404s are ours."""
    failures = [("https://i0.wp.com/therightworkshop.com/x.jpg?ssl=1", 404)]
    assert len(cb.check_resource_failures(failures, URL, "desktop")) == 1


def test_resource_failures_dedupe_and_cap():
    failures = [("https://therightworkshop.com/a.jpg", 404)] * 3
    assert len(cb.check_resource_failures(failures, URL, "desktop")) == 1
    many = [(f"https://therightworkshop.com/{i}.jpg", 404) for i in range(20)]
    assert len(cb.check_resource_failures(many, URL, "desktop")) == cb.MAX_PER_CHECK


# ------------------------------------------------ console vs network
def test_resource_console_lines_never_count_as_js_errors():
    """Chrome uses one message for both. Only the network log decides resources."""
    lines = ["Failed to load resource: the server responded with a status of 404 ()",
             "Failed to load resource: net::ERR_ABORTED",
             "net::ERR_NAME_NOT_RESOLVED"]
    assert cb.check_js_errors(lines, URL, "desktop") == []


def test_css_and_browser_noise_is_not_a_js_error():
    lines = ["Ignored @property rule", "[issue] something", "[warning] meh",
             "Tracking Prevention blocked storage"]
    assert cb.check_js_errors(lines, URL, "desktop") == []


def test_genuine_js_error_is_reported_with_its_text():
    lines = ["Uncaught TypeError: Cannot read properties of null (reading 'addEventListener')"]
    out = cb.check_js_errors(lines, URL, "desktop")
    assert len(out) == 1 and "Uncaught TypeError" in out[0].evidence.note
    assert out[0].evidence.is_hard()


# ------------------------------------------------ page status
def test_page_error_fires_on_5xx_and_404():
    assert cb.check_page_error(500, URL, "desktop")[0].evidence.status == 500
    assert cb.check_page_error(404, URL, "desktop")[0].check == "page_error"


def test_healthy_statuses_are_silent():
    for s in (200, 301, 302, None):
        assert cb.check_page_error(s, URL, "desktop") == []


# ------------------------------------------------ images
@pytest.mark.asyncio
async def test_broken_images_reported_with_src():
    page = FakePage([None, [{"src": "https://therightworkshop.com/x.jpg",
                             "alt": "a car", "sel": "img.hero"}]])
    out = await cb.check_broken_images(page, URL, "desktop")
    assert len(out) == 1 and out[0].evidence.resource.endswith("x.jpg")
    assert out[0].evidence.selector == "img.hero"


@pytest.mark.asyncio
async def test_no_broken_images_is_silent():
    page = FakePage([None, []])
    assert await cb.check_broken_images(page, URL, "desktop") == []


@pytest.mark.asyncio
async def test_browser_failure_does_not_invent_findings():
    class Boom(FakePage):
        async def evaluate(self, script, arg=None):
            raise RuntimeError("page crashed")
    assert await cb.check_broken_images(Boom([]), URL, "desktop") == []


# ------------------------------------------------ chrome
@pytest.mark.asyncio
async def test_missing_nav_and_footer_are_found():
    cfg = {"nav_selector": "nav", "footer_selector": "footer"}
    out = await cb.check_chrome(FakePage([], selectors={}), URL, "desktop", cfg)
    assert {f.check for f in out} == {"missing_nav", "missing_footer"}


@pytest.mark.asyncio
async def test_present_chrome_is_silent():
    cfg = {"nav_selector": "nav", "footer_selector": "footer"}
    page = FakePage([], selectors={"nav": object(), "footer": object()})
    assert await cb.check_chrome(page, URL, "desktop", cfg) == []


# ------------------------------------------------ THE regression: shadow burger
@pytest.mark.asyncio
async def test_invisible_burger_is_never_treated_as_the_control():
    """The other August 2026 incident, encoded.

    A hidden 0x0 core button matched first in document order and produced a
    critical false positive across 73 healthy pages. When candidates exist but
    none is tappable, that is reported as such, and crucially the check does
    NOT go on to claim the menu is dead by clicking a ghost.
    """
    cfg = {"mobile_nav_toggle_selector": "button[aria-label*=menu]",
           "mobile_nav_panel_selector": ".mmenu"}
    page = FakePage([{"hasButton": False, "hasDrawer": True, "candidates": 2}])
    out = await cb.check_mobile_menu(page, URL, "phone", cfg)
    assert len(out) == 1
    assert "none is tappable" in out[0].summary
    assert out[0].evidence.numbers["candidates"] == 2


@pytest.mark.asyncio
async def test_working_menu_is_silent():
    cfg = {"mobile_nav_toggle_selector": ".burger", "mobile_nav_panel_selector": ".mmenu"}
    page = FakePage([{"hasButton": True, "hasDrawer": True, "candidates": 1}, True])
    assert await cb.check_mobile_menu(page, URL, "phone", cfg) == []


@pytest.mark.asyncio
async def test_dead_menu_is_reported():
    cfg = {"mobile_nav_toggle_selector": ".burger", "mobile_nav_panel_selector": ".mmenu"}
    page = FakePage([{"hasButton": True, "hasDrawer": True, "candidates": 1}, False])
    out = await cb.check_mobile_menu(page, URL, "phone", cfg)
    assert len(out) == 1 and out[0].check == "mobile_menu_dead"


@pytest.mark.asyncio
async def test_menu_check_does_not_run_on_desktop():
    cfg = {"mobile_nav_toggle_selector": ".burger", "mobile_nav_panel_selector": ".mmenu"}
    assert await cb.check_mobile_menu(FakePage([]), URL, "desktop", cfg) == []


# ------------------------------------------------ THE regression: wpautop false positive
def test_ordinary_section_closing_is_not_an_autop_finding():
    """The dropped weak signature, pinned as a regression.

    `</p>\\s*</div>\\s*</section>` matched 121 of 211 TRW pages with zero real
    damage (T-1, deep-sweep-2026-09-13.md) because it also matches a
    perfectly ordinary hand-authored paragraph closing at the end of a
    section. It must never fire again.
    """
    html = "<section><div><p>worth more than knowing the trim level.</p>   </div> </section>"
    assert cb.check_autop_injection(html, URL, "desktop") == []


def test_card_anchor_closed_by_p_is_reported():
    html = '<a class="brand-card" href="/x"></p><p>next</p>'
    out = cb.check_autop_injection(html, URL, "desktop")
    assert len(out) == 1 and out[0].check == "autop_injection"
    assert "card_anchor_closed_by_p" in out[0].evidence.note
    assert out[0].owner == "bryan"


def test_p_closing_anchor_is_reported():
    html = "<a href='/x'>text<p></a>"
    out = cb.check_autop_injection(html, URL, "desktop")
    assert len(out) == 1 and "p_closing_anchor" in out[0].evidence.note


def test_p_wrapped_script_and_comment_are_reported():
    for html in ("<p><script>alert(1)</script></p>", "<p><!-- broken --></p>"):
        out = cb.check_autop_injection(html, URL, "desktop")
        assert len(out) == 1 and "p_wrapped_script_or_comment" in out[0].evidence.note


def test_clean_html_is_silent():
    html = "<html><body><section><article><p>Hello world.</p></article></section></body></html>"
    assert cb.check_autop_injection(html, URL, "desktop") == []


def test_autop_finding_can_prove_itself_and_is_not_prereproduced():
    out = cb.check_autop_injection("<p><!--x", URL, "desktop")
    assert out[0].evidence.is_hard()
    assert out[0].reproduced is False


def test_every_breakage_finding_can_prove_itself():
    produced = (cb.check_page_error(500, URL, "desktop")
                + cb.check_resource_failures([("https://therightworkshop.com/a.jpg", 404)], URL, "desktop")
                + cb.check_js_errors(["Uncaught ReferenceError: x is not defined"], URL, "desktop"))
    assert produced
    for f in produced:
        assert f.evidence.is_hard(), f.check
        assert f.reproduced is False, "only the verify pass may set reproduced"
