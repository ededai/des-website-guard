"""
Visual / chrome / functional checks via Playwright. Each returns a finding
dict or None.
"""
from urllib.parse import urlparse

MAROON_RGB_PATTERNS = [
    "rgb(122, 31, 31)", "rgb(112, 26, 26)", "#7a1f1f", "#701a1a",
]


async def check_chrome_consistency(page, baseline_header_html, baseline_footer_html):
    nav = await page.query_selector("nav.nav, .trw-injected-nav")
    footer = await page.query_selector("footer, .trw-injected-footer")
    if not nav:
        return {"check": "missing_nav", "severity": "critical", "evidence": "no <nav> on page"}
    if not footer:
        return {"check": "missing_footer", "severity": "critical", "evidence": "no <footer> on page"}
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


async def check_buttons_clickable(page):
    """Light-touch — make sure each button has a meaningful destination
    (anchor href or onclick or data-action). Skip actually triggering
    side effects like booking submits."""
    out = await page.evaluate("""() => {
        const nodes = document.querySelectorAll('a, button, [role=button]');
        let dead = 0;
        const samples = [];
        for (const n of nodes) {
            const tag = n.tagName;
            const href = n.getAttribute('href');
            const onclick = n.getAttribute('onclick');
            const role = n.getAttribute('role');
            const text = (n.innerText || '').trim().slice(0, 40);
            if (tag === 'A' && (!href || href === '#')) { dead++; samples.push(text || '(empty link)'); }
            if (tag === 'BUTTON' && !onclick && !n.type && !n.form) { /* might be JS-bound, allow */ }
        }
        return { dead, samples: samples.slice(0, 5) };
    }""")
    if out and out.get("dead", 0) > 2:
        return {"check": "dead_buttons", "severity": "medium", "evidence": f"{out['dead']} dead anchors: {out['samples']}"}
    return None
