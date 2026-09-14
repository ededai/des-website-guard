"""Context headers: the Cloudflare-403 regression, encoded.

des2/fetch.py used to set only the viewport for non-mobile contexts, so
Playwright sent its default HeadlessChrome UA and Cloudflare 403'd every
first request on desktop/laptop (T-2, deep-sweep-2026-09-13.md). Mobile
contexts were never affected -- Playwright's device UA already reads as real
Mobile Safari -- so the fix must apply only to non-mobile viewports, and must
never include Upgrade-Insecure-Requests (that header broke CORS preflights
to fonts.gstatic.com on 2026-09-13).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import fetch


class FakeBrowser:
    """Captures the kwargs new_context() would hand to Playwright."""
    def __init__(self):
        self.calls = []

    async def new_context(self, **kwargs):
        self.calls.append(kwargs)
        return object()


@pytest.mark.asyncio
async def test_desktop_context_gets_a_real_chrome_ua_and_client_hints():
    browser = FakeBrowser()
    await fetch.new_context(browser, "desktop")
    kwargs = browser.calls[0]
    assert "HeadlessChrome" not in kwargs.get("user_agent", "")
    assert "Chrome" in kwargs["user_agent"] and "Macintosh" in kwargs["user_agent"]
    headers = kwargs["extra_http_headers"]
    assert headers["Sec-CH-UA-Mobile"] == "?0"
    assert headers["Sec-CH-UA-Platform"] == '"macOS"'
    assert "Sec-CH-UA" in headers
    assert headers["Accept-Language"].startswith("en-SG")


def test_upgrade_insecure_requests_is_never_sent():
    """That header replays on CORS preflights and fabricates console errors
    on fonts.gstatic.com / cloudflareinsights.com. Found the hard way, never
    to come back."""
    assert "Upgrade-Insecure-Requests" not in fetch.DESKTOP_HEADERS


@pytest.mark.asyncio
async def test_mobile_context_is_untouched_by_the_desktop_headers():
    browser = FakeBrowser()
    await fetch.new_context(browser, "phone")
    kwargs = browser.calls[0]
    assert "user_agent" not in kwargs
    assert "extra_http_headers" not in kwargs
    assert kwargs["is_mobile"] is True


from des2.models import VIEWPORTS  # noqa: E402


@pytest.mark.asyncio
async def test_every_non_mobile_viewport_gets_headers():
    browser = FakeBrowser()
    for name, vp in VIEWPORTS.items():
        if vp.get("is_mobile"):
            continue
        await fetch.new_context(browser, name)
    assert browser.calls, "expected at least one non-mobile viewport"
    for kwargs in browser.calls:
        assert kwargs.get("user_agent") == fetch.DESKTOP_UA
        assert kwargs.get("extra_http_headers") == fetch.DESKTOP_HEADERS
