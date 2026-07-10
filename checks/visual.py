"""
Visual / chrome / functional checks via Playwright. Each returns a finding
dict or None.
"""
from urllib.parse import urlparse

MAROON_RGB_PATTERNS = [
    "rgb(122, 31, 31)", "rgb(112, 26, 26)", "#7a1f1f", "#701a1a",
]


async def check_chrome_consistency(page, site=None):
    """Presence checks for nav/footer plus a site-configured required nav link.
    Selectors and the required link come from sites/<site>.yaml so this no
    longer hardcodes TRW chrome (it used to demand a COE link on every site)."""
    site = site or {}
    nav_sel = site.get("nav_selector") or "nav.nav, .trw-injected-nav, header nav, nav:not([class*='bread']):not(.bc)"
    footer_sel = site.get("footer_selector") or "footer, .trw-injected-footer"
    nav = await page.query_selector(nav_sel)
    footer = await page.query_selector(footer_sel)
    if not nav:
        return {"check": "missing_nav", "severity": "critical", "evidence": "no <nav> on page"}
    if not footer:
        return {"check": "missing_footer", "severity": "critical", "evidence": "no <footer> on page"}
    required = site.get("required_nav_link")  # e.g. "coe-results" for TRW
    if required:
        hit = await page.query_selector(f"{nav_sel.split(',')[0]} a[href*='{required}'], nav a[href*='{required}'], .nav-links a[href*='{required}']")
        if not hit:
            return {"check": "missing_required_nav_link", "severity": "high", "evidence": f"nav link containing '{required}' not found"}
    return None


async def check_maroon_leak(page):
    bad = await page.evaluate(f"""() => {{
        const patterns = {MAROON_RGB_PATTERNS!r};
        const offenders = [];
        for (const el of document.querySelectorAll('button, a.btn, .btn, [role=button]')) {{
            const cs = getComputedStyle(el);
            for (const p of patterns) {{
                if (cs.backgroundColor && cs.backgroundColor.includes(p)) offenders.push(el.outerHTML.slice(0, 120));
                if (cs.color && cs.color.includes(p)) offenders.push(el.outerHTML.slice(0, 120));
            }}
        }}
        return offenders.slice(0, 3);
    }}""")
    if bad:
        return {"check": "maroon_leak", "severity": "high", "evidence": f"maroon background/text on {len(bad)} elements: {bad}"}
    return None


async def check_broken_images(page):
    # Trigger lazy-load: scroll the page top-to-bottom in chunks so
    # IntersectionObserver / loading="lazy" images start fetching, then wait
    # for the network to settle before deciding what is actually broken.
    await page.evaluate("""async () => {
        const step = Math.max(window.innerHeight, 600);
        for (let y = 0; y <= document.body.scrollHeight; y += step) {
            window.scrollTo(0, y);
            await new Promise(r => setTimeout(r, 120));
        }
        window.scrollTo(0, 0);
    }""")
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    broken = await page.evaluate("""() => {
        const out = [];
        for (const img of document.images) {
            // Only flag images the browser actually attempted to load and failed.
            // complete=true + naturalWidth=0 = real failure (404, decode error).
            if (img.complete && img.naturalWidth === 0 && img.src) {
                out.push({ src: img.src, alt: img.alt });
            }
        }
        return out.slice(0, 5);
    }""")
    if broken:
        return {"check": "broken_images", "severity": "high", "evidence": f"{len(broken)} broken images: {broken[:3]}"}
    return None


async def check_console_errors(page, captured_errors):
    if captured_errors:
        return {"check": "console_errors", "severity": "high", "evidence": f"{len(captured_errors)} JS console errors", "details": captured_errors[:5]}
    return None


async def check_mobile_menu(page):
    """Mobile-only check. Tap the hamburger and confirm:
      - drawer becomes visible (display != none, white-ish bg, .open class)
      - tapping any link inside auto-closes the drawer (universal rule)
      - no maroon/black-on-black colour leak on links

    Logged in two TRW post-mortems:
      - 2026-05-02 — 9 service pages had broken IIFE onclick referencing
        the old `trwMobileNav` id. Hamburger tap did nothing.
      - 2026-05-02 — /brands-we-service/ drawer had no id, the older
        `getElementById('mobileNav')` lookup returned null. Hamburger ran but
        toggled nothing visible.
    """
    bad = await page.evaluate("""async () => {
        const isMaroonish = (rgb) => {
            const m = rgb.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
            if (!m) return false;
            const [r,g,b] = [+m[1], +m[2], +m[3]];
            if (Math.abs(r-239)<8 && Math.abs(g-89)<8 && Math.abs(b-39)<8) return false;
            if (Math.abs(r-217)<8 && Math.abs(g-78)<8 && Math.abs(b-32)<8) return false;
            return r >= 100 && g < 60 && b < 60 && r > g+30 && r > b+30;
        };
        const btn = document.getElementById('navHamburger')
                 || document.getElementById('trwNavHamburger')
                 || document.querySelector('.nav-hamburger, .hamburger, .menu-toggle, .nav-toggle, button[aria-label*="menu" i], [class*="hamburger"], [class*="menu-btn"]');
        if (!btn) return { issue: 'no_hamburger' };
        // Capture pre-click state
        const nav = document.getElementById('mobileNav')
                 || document.querySelector('.mobile-nav, [class*="mobile-nav"], [class*="drawer"], nav[class*="menu"]');
        if (!nav) return { issue: 'no_drawer' };
        btn.click();
        await new Promise(r => setTimeout(r, 120));
        const cs = getComputedStyle(nav);
        const opened = nav.classList.contains('open') && cs.display !== 'none';
        if (!opened) return { issue: 'menu_did_not_open', display: cs.display };
        // Drawer should have a visible bg (not transparent)
        const bgM = cs.backgroundColor.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)(?:,\\s*([\\d.]+))?/);
        if (bgM) {
            const a = bgM[4] === undefined ? 1 : +bgM[4];
            const lightish = +bgM[1] >= 200 && +bgM[2] >= 200 && +bgM[3] >= 200;
            if (a < 0.5 || (!lightish && a >= 0.5)) {
                return { issue: 'drawer_invisible', bg: cs.backgroundColor };
            }
        }
        const maroon = [];
        for (const a of nav.querySelectorAll('a')) {
            const c = getComputedStyle(a);
            if (isMaroonish(c.color)) maroon.push({ state: 'normal', color: c.color, txt: a.textContent.trim().slice(0,30) });
            try { a.focus(); const c2 = getComputedStyle(a); if (isMaroonish(c2.color)) maroon.push({ state: 'focus', color: c2.color, txt: a.textContent.trim().slice(0,30) }); a.blur(); } catch(_) {}
        }
        // Auto-close on link tap (intercept navigation)
        const link = nav.querySelector('a[href]');
        let closedAfterTap = null;
        if (link) {
            const orig = link.getAttribute('href');
            link.setAttribute('href', 'javascript:void(0)');
            link.click();
            closedAfterTap = !nav.classList.contains('open');
            link.setAttribute('href', orig);
        }
        if (!closedAfterTap) return { issue: 'menu_no_autoclose' };
        if (maroon.length) return { issue: 'menu_maroon_leak', samples: maroon.slice(0, 3) };
        return null;
    }""")
    if bad:
        sev = "critical" if bad.get("issue") in ("menu_did_not_open", "no_hamburger", "no_drawer") else "high"
        return {"check": "mobile_menu", "severity": sev, "evidence": bad}
    return None


async def check_buttons_clickable(page):
    """Light-touch — make sure each button has a meaningful destination
    (anchor href or onclick or data-action). Skip actually triggering
    side effects like booking submits.

    Only anchors a user can actually see count as dead: invisible DOM
    (unconfigured theme placeholders, drawers closed at this viewport) is
    reported separately in evidence but does not fire the finding. Jetpack
    injects hrefless markup by design (sharedaddy `sd-link-color`, the
    subscribe-modal "Continue reading") — exempt, not fixable in content."""
    out = await page.evaluate("""() => {
        const nodes = document.querySelectorAll('a, button, [role=button]');
        let dead = 0, invisibleDead = 0;
        const samples = [];
        for (const n of nodes) {
            const tag = n.tagName;
            const href = n.getAttribute('href');
            const text = (n.innerText || '').trim().slice(0, 40);
            if (tag === 'A' && (!href || href === '#')) {
                if (n.classList.contains('sd-link-color')) continue;
                if (n.closest('.jetpack-subscribe-modal, .sharedaddy, .jp-relatedposts')) continue;
                const r = n.getBoundingClientRect();
                const s = getComputedStyle(n);
                const visible = r.width > 0 || r.height > 0
                    ? (s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.01)
                    : false;
                if (!visible) { invisibleDead++; continue; }
                dead++; samples.push(text || '(empty link)');
            }
            if (tag === 'BUTTON') { /* might be JS-bound, allow */ }
        }
        return { dead, invisibleDead, samples: samples.slice(0, 5) };
    }""")
    if out and out.get("dead", 0) > 2:
        extra = f" ({out['invisibleDead']} invisible skipped)" if out.get("invisibleDead") else ""
        return {"check": "dead_buttons", "severity": "medium", "evidence": f"{out['dead']} dead anchors: {out['samples']}{extra}"}
    return None
