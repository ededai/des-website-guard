"""Opening pages, and turning one into facts.

Two jobs. Drive the browser politely, and extract the handful of counts the
loss-only baseline needs.

Listeners are attached BEFORE navigation and cleared on retry. Both matter: a
listener added after `goto` misses the errors it exists to catch, and a
challenge page's own console noise leaking into the retried clean load is how
v1 manufactured phantom findings in August 2026.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from des2.checks_break import CONSOLE_NOISE, is_console_noise, is_resource_msg
from des2.models import VIEWPORTS

NAV_TIMEOUT_MS = 45000
SETTLE_MS = 2000
POLITE_PAUSE_S = 0.4      # same host, many pages: do not hammer it


@dataclass
class Visit:
    url: str
    viewport: str
    status: Optional[int] = None
    html: str = ""
    console_errors: list[str] = field(default_factory=list)
    net_failures: list[tuple[str, int]] = field(default_factory=list)
    error: str = ""


async def new_context(browser, viewport: str):
    """One context per viewport, reused across pages. Cheaper than per page."""
    vp = VIEWPORTS[viewport]
    args: dict[str, Any] = {"viewport": {"width": vp["width"], "height": vp["height"]}}
    if vp.get("is_mobile"):
        args.update(is_mobile=True, has_touch=True,
                    device_scale_factor=vp.get("device_scale_factor", 2))
    return await browser.new_context(**args)


async def visit(context, url: str, viewport: str) -> tuple[Any, Visit]:
    """Open a page and collect what the checks will need. Returns (page, Visit).

    The caller owns the page and must close it. Kept open deliberately: the DOM
    checks need it alive.
    """
    v = Visit(url=url, viewport=viewport)
    page = await context.new_page()

    page.on("pageerror", lambda exc: v.console_errors.append(str(exc)))
    page.on("console",
            lambda m: v.console_errors.append(f"[{m.type}] {m.text}")
            if m.type == "error" else None)
    page.on("response",
            lambda r: v.net_failures.append((r.url, r.status)) if r.status >= 400 else None)

    try:
        resp = await page.goto(url, wait_until="commit", timeout=NAV_TIMEOUT_MS)
        v.status = resp.status if resp else None
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        await page.wait_for_timeout(SETTLE_MS)
        v.html = await page.content()
    except Exception as e:
        v.error = str(e)[:200]
    return page, v


async def page_facts(page, site_cfg: dict) -> dict:
    """The counts the baseline compares. Counts, not content.

    Text length rather than text itself, so rewording a page is invisible and
    a page losing its body is loud.
    """
    nav_sel = site_cfg.get("nav_selector") or "nav"
    foot_sel = site_cfg.get("footer_selector") or "footer"
    base = site_cfg.get("base_url") or ""
    try:
        return await page.evaluate("""(cfg) => {
          const [navSel, footSel, base] = cfg;
          const q = (s) => { try { return document.querySelector(s); } catch (e) { return null; } };
          const imgs = Array.from(document.images);
          const meta = (n) => {
            const el = document.querySelector(`meta[name="${n}"]`);
            return !!(el && (el.getAttribute('content') || '').trim());
          };
          const body = document.body ? (document.body.innerText || '') : '';
          const internal = Array.from(document.querySelectorAll('a[href]'))
            .filter(a => { const h = a.getAttribute('href') || '';
                           return h.startsWith('/') || (base && h.startsWith(base)); });
          const bylineEl = document.querySelector('.byline, .author, [rel=author], .post-author');
          const addr = (document.body ? document.body.innerText : '');
          return {
            has_nav: !!q(navSel),
            has_footer: !!q(footSel),
            h1_count: document.querySelectorAll('h1').length,
            text_len: body.trim().length,
            images_total: imgs.length,
            images_ok: imgs.filter(i => !(i.complete && i.naturalWidth === 0)).length,
            internal_links: internal.length,
            sections: document.querySelectorAll('section, article, .trw-section').length,
            standards: {
              meta_description: meta('description'),
              title: !!(document.title || '').trim(),
              canonical: !!document.querySelector('link[rel=canonical]'),
              h1: document.querySelectorAll('h1').length > 0,
              byline: !!bylineEl,
              // Ed's standing rule: the unit number must always be present,
              // because the building is multi-tenant and customers cannot find
              // the workshop without it.
              address_unit: /#0\\d-\\d\\d/.test(addr),
              alt_text: imgs.length === 0 || imgs.every(i => (i.getAttribute('alt') || '').trim() !== ''),
            },
          };
        }""", [nav_sel, foot_sel, base])
    except Exception:
        return {}


async def polite_pause(scale: float = 1.0) -> None:
    await asyncio.sleep(POLITE_PAUSE_S * scale)
