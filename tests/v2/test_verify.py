"""The proof pass is the load-bearing guarantee, so it gets tested hardest."""
import sys, os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2.models import Evidence, Finding
from des2.verify import partition, verify_findings, why_not_alertable


def _f(check="resource_404", url="https://x/a", vp="desktop", **ev):
    return Finding(check=check, kind="breakage", url=url, viewport=vp,
                   summary="s", evidence=Evidence(**ev))


@pytest.mark.asyncio
async def test_finding_that_reproduces_is_marked():
    f = _f(resource="https://x/img.jpg", status=404)
    async def recheck(url, vp):
        return [_f(resource="https://x/img.jpg", status=404)]
    out = await verify_findings([f], recheck)
    assert out[0].reproduced is True and out[0].alertable() is True


@pytest.mark.asyncio
async def test_transient_finding_dies_silently():
    """A CDN blip that does not survive a clean re-test must never reach Ed."""
    f = _f(resource="https://x/blip.jpg", status=404)
    async def recheck(url, vp):
        return []          # gone on second look
    out = await verify_findings([f], recheck)
    assert out[0].reproduced is False
    alertable, logged = partition(out)
    assert alertable == [] and len(logged) == 1


@pytest.mark.asyncio
async def test_reproducing_but_unprovable_is_still_held_back():
    """The 2026-08 beacon case: reproduces every time, names nothing."""
    vague = Finding(check="js_error", kind="breakage", url="https://x/a",
                    viewport="desktop", summary="1 console error")
    async def recheck(url, vp):
        return [Finding(check="js_error", kind="breakage", url="https://x/a",
                        viewport="desktop", summary="1 console error")]
    out = await verify_findings([vague], recheck)
    assert out[0].reproduced is True
    alertable, logged = partition(out)
    assert alertable == []
    assert "names no URL" in why_not_alertable(logged[0])


@pytest.mark.asyncio
async def test_recheck_crash_does_not_confirm_anything():
    f = _f(resource="https://x/img.jpg", status=404)
    async def recheck(url, vp):
        raise RuntimeError("browser died")
    out = await verify_findings([f], recheck)
    assert out[0].reproduced is False


@pytest.mark.asyncio
async def test_one_recheck_per_page_not_per_finding():
    calls = []
    fs = [_f(resource=f"https://x/{i}.jpg", status=404) for i in range(5)]
    async def recheck(url, vp):
        calls.append((url, vp))
        return []
    await verify_findings(fs, recheck)
    assert len(calls) == 1, "5 findings on one page must cost one revisit"


@pytest.mark.asyncio
async def test_empty_input_is_a_noop():
    async def recheck(url, vp):
        raise AssertionError("must not be called")
    assert await verify_findings([], recheck) == []
