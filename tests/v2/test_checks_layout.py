"""Layout checks: the 'looks off' half of the brief.

Every finding must carry the measured numbers, and every check must stay quiet
on a clean page. The tolerance tests matter as much as the detection ones: a
layout checker that fires on sub-pixel rounding is noise with extra steps.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import checks_layout as cl

URL = "https://therightworkshop.com/brands/peugeot/"


class FakePage:
    """Returns one canned payload per evaluate call."""
    def __init__(self, payload):
        self._payload = payload

    async def evaluate(self, script, arg=None):
        return self._payload


class DeadPage:
    async def evaluate(self, script, arg=None):
        raise RuntimeError("execution context destroyed")


# --------------------------------------------------------- horizontal scroll
@pytest.mark.asyncio
async def test_horizontal_scroll_reported_with_measurements():
    page = FakePage({"overflow": 37, "viewportWidth": 390,
                     "offenders": [{"sel": "div.hero", "right": 427, "left": 0, "width": 427}]})
    out = await cl.check_horizontal_scroll(page, URL, "phone")
    assert len(out) == 1
    f = out[0]
    assert f.check == "horizontal_scroll" and f.kind == "layout"
    assert f.evidence.selector == "div.hero"
    assert f.evidence.numbers["overflow_px"] == 37
    assert f.evidence.is_hard()


@pytest.mark.asyncio
async def test_subpixel_rounding_is_not_a_defect():
    page = FakePage({"overflow": 1, "viewportWidth": 390, "offenders": []})
    assert await cl.check_horizontal_scroll(page, URL, "phone") == []


@pytest.mark.asyncio
async def test_clean_page_scrolls_nowhere():
    page = FakePage({"overflow": 0, "offenders": []})
    assert await cl.check_horizontal_scroll(page, URL, "desktop") == []


# --------------------------------------------------------- text overflow
@pytest.mark.asyncio
async def test_cut_off_text_is_reported():
    page = FakePage([{"sel": "h2.title", "dx": 40, "dy": 0, "text": "Servicing and repair"}])
    out = await cl.check_text_overflow(page, URL, "phone")
    assert len(out) == 1 and out[0].evidence.numbers["hidden_x_px"] == 40
    assert "Servicing" in out[0].evidence.note


@pytest.mark.asyncio
async def test_tiny_overflow_within_tolerance_is_ignored():
    page = FakePage([{"sel": "p", "dx": 2, "dy": 1, "text": "x"}])
    assert await cl.check_text_overflow(page, URL, "phone") == []


# --------------------------------------------------------- overlap
@pytest.mark.asyncio
async def test_real_collision_is_reported():
    page = FakePage([{"a": "h2.headline", "b": "p.body", "area": 5000, "share": 0.6}])
    out = await cl.check_element_overlap(page, URL, "desktop")
    assert len(out) == 1
    assert out[0].evidence.numbers["overlap_px2"] == 5000
    assert "p.body" in out[0].evidence.note


@pytest.mark.asyncio
async def test_touching_borders_are_not_a_collision():
    page = FakePage([{"a": "h2", "b": "p", "area": 12, "share": 0.01}])
    assert await cl.check_element_overlap(page, URL, "desktop") == []


# --------------------------------------------------------- clipping
@pytest.mark.asyncio
async def test_clipped_heading_is_reported():
    page = FakePage([{"sel": "h1", "text": "COE Results", "parent": "div.wrap",
                      "cutX": 22, "cutY": 0}])
    out = await cl.check_clipped_content(page, URL, "phone")
    assert len(out) == 1 and out[0].evidence.numbers["cut_x_px"] == 22
    assert "div.wrap" in out[0].evidence.note


# --------------------------------------------------------- tap targets
@pytest.mark.asyncio
async def test_small_tap_target_reported_on_phone():
    page = FakePage([{"sel": "a.icon", "w": 20, "h": 20, "text": "x"}])
    out = await cl.check_tap_targets(page, URL, "phone")
    assert len(out) == 1
    assert out[0].evidence.numbers == {"width_px": 20, "height_px": 20, "minimum_px": 44}


@pytest.mark.asyncio
async def test_tap_targets_not_checked_on_desktop():
    """A mouse does not need a 44px target."""
    page = FakePage([{"sel": "a.icon", "w": 20, "h": 20, "text": "x"}])
    assert await cl.check_tap_targets(page, URL, "desktop") == []


# --------------------------------------------------------- shared guarantees
@pytest.mark.asyncio
async def test_every_check_survives_a_dead_browser_without_inventing_findings():
    for check in cl.ALL_LAYOUT_CHECKS:
        assert await check(DeadPage(), URL, "phone") == []


@pytest.mark.asyncio
async def test_every_check_is_silent_on_empty_results():
    for check in cl.ALL_LAYOUT_CHECKS:
        assert await check(FakePage([]), URL, "phone") == []


@pytest.mark.asyncio
async def test_results_are_capped_so_one_bad_template_is_not_a_hundred_pings():
    many = [{"sel": f"a{i}", "w": 10, "h": 10, "text": "x"} for i in range(30)]
    out = await cl.check_tap_targets(FakePage(many), URL, "phone")
    assert len(out) == cl.MAX_PER_CHECK


@pytest.mark.asyncio
async def test_every_layout_finding_can_prove_itself():
    payloads = {
        cl.check_horizontal_scroll: {"overflow": 40, "viewportWidth": 390,
                                     "offenders": [{"sel": "div", "right": 430, "left": 0, "width": 430}]},
        cl.check_text_overflow: [{"sel": "p", "dx": 30, "dy": 0, "text": "t"}],
        cl.check_element_overlap: [{"a": "h2", "b": "p", "area": 5000, "share": 0.6}],
        cl.check_clipped_content: [{"sel": "h1", "text": "t", "parent": "div", "cutX": 20, "cutY": 0}],
        cl.check_tap_targets: [{"sel": "a", "w": 10, "h": 10, "text": "t"}],
    }
    for check, payload in payloads.items():
        out = await check(FakePage(payload), URL, "phone")
        assert out, f"{check.__name__} should have produced a finding"
        for f in out:
            assert f.kind == "layout"
            assert f.evidence.is_hard(), f"{f.check} cannot prove itself"
            assert f.evidence.numbers, f"{f.check} must carry measurements"
            assert f.reproduced is False
