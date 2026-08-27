"""Breakage checks: things that are wrong regardless of what the page used to be.

These always fire, unlike standards, which only fire when a page LOSES them.

The hard-won rule in here is where resource failures come from. Chrome prints
"Failed to load resource: ..." with NO URL attached, and prints the same line
for a genuine 404 and for a beacon aborting as the page closes. In August 2026
that ambiguity produced a finding that reproduced forever, named nothing, and
sent a ten-agent investigation after a site that was perfectly healthy. So:
resource failures are judged ONLY from the network log, first-party only, and
always with the exact URL. Console text is never evidence of a resource fault.
"""
from __future__ import annotations

from urllib.parse import urlparse

from des2.models import Evidence, Finding, expects_mobile_nav, owner_for

# Console lines that describe a RESOURCE, not a JavaScript fault. Excluded from
# the JS check by construction so the two can never be confused again.
RESOURCE_MSG_MARKERS = ("Failed to load resource", "net::ERR_")

# Browser and CSS noise. None of these is a JavaScript error (2026-06-20).
CONSOLE_NOISE = (
    "Ignored @property rule", "Content Security Policy", "Tracking Prevention",
    "third-party cookie", "[issue]", "[warning]", "[debug]",
)

# Hosts that are OUR problem even though the hostname differs: the wp.com
# Photon CDN serves the site's own media.
FIRST_PARTY_EXTRA_HOSTS = {"i0.wp.com", "i1.wp.com", "i2.wp.com", "i3.wp.com"}

MAX_PER_CHECK = 5          # one broken template must not become a hundred findings
MIN_TAP_TARGET_PX = 44


def is_resource_msg(text: str) -> bool:
    return any(m in text for m in RESOURCE_MSG_MARKERS)


def is_console_noise(text: str, extra: tuple = ()) -> bool:
    return any(p in text for p in CONSOLE_NOISE) or any(p in text for p in (extra or ()))


def first_party_failures(failures, page_url: str) -> list[tuple[str, int]]:
    """(url, status) pairs that are the site's own problem. Deduped, order kept.

    Third-party beacons, analytics and map tiles are excluded: their transient
    failures are not site defects, and treating them as such is exactly the
    false positive this module exists to prevent.
    """
    host = urlparse(page_url).hostname or ""
    out, seen = [], set()
    for url, status in failures or []:
        h = urlparse(url).hostname or ""
        ours = h == host or (host and h.endswith("." + host)) or h in FIRST_PARTY_EXTRA_HOSTS
        if ours and (url, status) not in seen:
            seen.add((url, status))
            out.append((url, status))
    return out


def _f(check: str, url: str, viewport: str, summary: str, ev: Evidence) -> Finding:
    return Finding(check=check, kind="breakage", url=url, viewport=viewport,
                   summary=summary, evidence=ev, owner=owner_for(check))


def check_page_error(status, url: str, viewport: str) -> list[Finding]:
    """A page a visitor cannot read. Blocked/challenge pages are the gate's job."""
    if status is None or 200 <= int(status) < 400:
        return []
    return [_f("page_error", url, viewport,
               f"page returned HTTP {status}", Evidence(status=int(status), resource=url))]


def check_resource_failures(failures, url: str, viewport: str) -> list[Finding]:
    """First-party subresources that answered >= 400, straight from the network log."""
    out = []
    for res, status in first_party_failures(failures, url)[:MAX_PER_CHECK]:
        out.append(_f("resource_404", url, viewport,
                      f"resource failed with HTTP {status}",
                      Evidence(resource=res, status=status)))
    return out


def check_js_errors(console_errors, url: str, viewport: str,
                    extra_noise: tuple = ()) -> list[Finding]:
    """Genuine uncaught JavaScript only, quoting the actual error text."""
    out = []
    for text in (console_errors or []):
        text = str(text)
        if is_resource_msg(text) or is_console_noise(text, extra_noise):
            continue
        out.append(_f("js_error", url, viewport, "uncaught JavaScript error",
                      Evidence(note=text[:300], selector="document")))
        if len(out) >= MAX_PER_CHECK:
            break
    return out


async def check_broken_images(page, url: str, viewport: str) -> list[Finding]:
    """Images the browser tried and failed to load.

    Lazy-loading is triggered first, then the network is allowed to settle,
    otherwise an image that simply had not started loading looks broken.
    """
    try:
        await page.evaluate("""async () => {
            const step = Math.max(window.innerHeight, 600);
            for (let y = 0; y <= document.body.scrollHeight; y += step) {
                window.scrollTo(0, y);
                await new Promise(r => setTimeout(r, 120));
            }
            window.scrollTo(0, 0);
        }""")
        broken = await page.evaluate("""() => {
            const out = [];
            for (const img of document.images) {
                // complete && naturalWidth === 0 means the browser finished and failed.
                if (img.complete && img.naturalWidth === 0 && img.src) {
                    out.push({src: img.src, alt: img.alt || '',
                              sel: img.id ? '#' + img.id : (img.className ? 'img.' + String(img.className).split(' ')[0] : 'img')});
                }
            }
            return out.slice(0, 5);
        }""")
    except Exception:
        return []
    return [_f("broken_images", url, viewport, "image failed to load",
               Evidence(resource=b.get("src"), selector=b.get("sel"),
                        note=(b.get("alt") or "")[:120]))
            for b in (broken or [])[:MAX_PER_CHECK]]


async def check_chrome(page, url: str, viewport: str, site_cfg: dict) -> list[Finding]:
    """Nav and footer present, accepting both chrome generations."""
    out = []
    nav_sel = site_cfg.get("nav_selector") or "nav"
    foot_sel = site_cfg.get("footer_selector") or "footer"
    try:
        if not await page.query_selector(nav_sel):
            out.append(_f("missing_nav", url, viewport, "no navigation on the page",
                          Evidence(selector=nav_sel)))
        if not await page.query_selector(foot_sel):
            out.append(_f("missing_footer", url, viewport, "no footer on the page",
                          Evidence(selector=foot_sel)))
    except Exception:
        return []
    return out


async def check_mobile_menu(page, url: str, viewport: str, site_cfg: dict) -> list[Finding]:
    """Phone only. The burger must open the drawer, and a link must close it.

    Resolving WHICH burger is real matters more than the tap itself: in August
    2026 a hidden 0x0 WordPress core button matched first in document order,
    so the check tapped a control no human can see and reported a critical
    fault across 73 healthy pages. Untappable candidates are skipped here.
    """
    # Decided by width against the site's own breakpoint, not by a viewport
    # name. Above the breakpoint the burger is hidden on purpose, so running
    # this there would report "no tappable menu button" on every desktop page.
    if not expects_mobile_nav(viewport, site_cfg):
        return []
    toggle = site_cfg.get("mobile_nav_toggle_selector")
    panel = site_cfg.get("mobile_nav_panel_selector")
    if not toggle or not panel:
        return []
    try:
        found = await page.evaluate("""(sels) => {
            const [toggleSel, panelSel] = sels;
            const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && cs.display !== 'none'
                       && cs.visibility !== 'hidden' && cs.opacity !== '0';
            };
            // First TAPPABLE candidate, not first in document order.
            const btn = Array.from(document.querySelectorAll(toggleSel)).find(visible);
            const drawer = document.querySelector(panelSel);
            return {hasButton: !!btn, hasDrawer: !!drawer,
                    candidates: document.querySelectorAll(toggleSel).length};
        }""", [toggle, panel])
    except Exception:
        return []
    if not found or not found.get("hasButton") or not found.get("hasDrawer"):
        # No visible burger at all is a real finding only if the markup claims one.
        if found and found.get("candidates") and not found.get("hasButton"):
            return [_f("mobile_menu_dead", url, viewport,
                       "menu button exists in markup but none is tappable",
                       Evidence(selector=toggle,
                                numbers={"candidates": found.get("candidates", 0)}))]
        return []
    try:
        opened = await page.evaluate("""async (sels) => {
            const [toggleSel, panelSel] = sels;
            const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && cs.display !== 'none'
                       && cs.visibility !== 'hidden' && cs.opacity !== '0';
            };
            const btn = Array.from(document.querySelectorAll(toggleSel)).find(visible);
            const drawer = document.querySelector(panelSel);
            btn.click();
            await new Promise(r => setTimeout(r, 450));
            return visible(drawer);
        }""", [toggle, panel])
    except Exception:
        return []
    if not opened:
        return [_f("mobile_menu_dead", url, viewport,
                   "tapping the menu button did not open the drawer",
                   Evidence(selector=toggle, note=f"drawer: {panel}"))]
    return []
