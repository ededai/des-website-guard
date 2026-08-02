"""
Reveal-safe, capped JPEG screenshot capture for critical/high findings
(D-01). Adapts the scroll/settle/JPEG pattern already proven in
tools/audit_capture.py (lines 250-285) rather than inventing new Playwright
plumbing.

These JPEGs are NEVER committed: they are base64-inlined into the HTML
report by reporters/html_report.py and uploaded to Telegram from disk by
reporters/telegram.send_photo. .des-shots/ is gitignored.
"""
import os
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SHOT_DIR = Path(os.environ.get("DES_SHOT_DIR", ROOT / ".des-shots"))

# Repo/Telegram bloat cap: one alert-worth of evidence per sweep is enough;
# a runaway sweep must not fill the report/queue with dozens of JPEGs.
MAX_SHOTS_PER_SWEEP = 12

# Scroll-reveal trap from the des-overhaul post-mortem: `.fu` fade-up
# elements on AURA (and equivalents on TRW) sit at opacity 0 until an
# IntersectionObserver fires, so an un-forced capture is a blank cream page.
REVEAL_FORCE_CSS = (
    ".fu,.fu-1,.fu-2,.fu-3,.fade-up,.fade-in,.reveal,.wow,.animate,"
    "[data-aos],[class*='fade'],[class*='reveal']"
    "{opacity:1!important;transform:none!important;transition:none!important;"
    "animation:none!important;visibility:visible!important;}"
)

_taken = 0
_cap_notice_shown = False


def reset():
    """Test hook. Resets the module-level shot counter and cap notice."""
    global _taken, _cap_notice_shown
    _taken = 0
    _cap_notice_shown = False


def _slugify(url):
    p = urlparse(url).path.strip("/")
    return p.replace("/", "__") if p else "home"


async def capture_alert_shot(page, url, viewport):
    """Capture a viewport-sized JPEG of `page` for a critical/high finding.
    Returns the file path, or None once the per-sweep cap is hit or on any
    capture failure. A screenshot problem must never fail a sweep or lose
    the underlying finding."""
    global _taken, _cap_notice_shown
    if _taken >= MAX_SHOTS_PER_SWEEP:
        if not _cap_notice_shown:
            print(f"DES: screenshot cap reached ({MAX_SHOTS_PER_SWEEP}/sweep), "
                  f"further alerts ship without a photo")
            _cap_notice_shown = True
        return None
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        # Force reveal-class opacity FIRST, before any scroll/settle.
        await page.add_style_tag(content=REVEAL_FORCE_CSS)
        # Belt-and-braces for observers that already fired with a transition
        # in flight.
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1500)
        slug = _slugify(url)
        f = SHOT_DIR / f"{slug}-{viewport}-{_taken}.jpg"
        # Viewport-sized (full_page defaults False). A full-page capture of
        # a 12k-pixel article is what would blow the sendPhoto limit and the
        # report size.
        await page.screenshot(path=str(f), type="jpeg", quality=60)
        _taken += 1
        return str(f)
    except Exception:
        return None
