"""
Deep-audit capture: renders every URL at desktop+phone (+overflow probes at
1280/768/412), extracts chrome signatures, breadcrumb geometry, SEO meta,
em-dashes, autop signatures, broken images, console errors, tap targets,
LCP/CLS, link inventories, visible text, and segmented screenshots.

Usage: python audit_capture.py <site> <urls_file> <out_dir>
"""
import asyncio, json, re, sys, traceback
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

SITE = sys.argv[1]
URLS = [u.strip() for u in Path(sys.argv[2]).read_text().splitlines() if u.strip()]
OUT = Path(sys.argv[3])
CONCURRENCY = 5
IS_TRW = SITE == "trw"

(OUT / "shots").mkdir(parents=True, exist_ok=True)
(OUT / "text").mkdir(exist_ok=True)
(OUT / "html").mkdir(exist_ok=True)
METRICS = OUT / "metrics.jsonl"

done_slugs = set()
if METRICS.exists():
    for line in METRICS.read_text().splitlines():
        try:
            done_slugs.add(json.loads(line)["slug"])
        except Exception:
            pass

CONSOLE_NOISE = ["Ignored @property rule", "Content Security Policy", "Tracking Prevention"]

EXTRACT_JS = r"""
(cfg) => {
  const out = {};
  const doc = document;
  const T = (s) => (s || '').replace(/\s+/g, ' ').trim();
  out.title = doc.title || null;
  const md = doc.querySelector('meta[name="description"]');
  out.metaDescription = md ? md.getAttribute('content') : null;
  const can = doc.querySelector('link[rel="canonical"]');
  out.canonical = can ? can.href : null;
  const rob = doc.querySelector('meta[name="robots"]');
  out.robotsMeta = rob ? rob.getAttribute('content') : null;
  out.h1s = [...doc.querySelectorAll('h1')].map(h => T(h.innerText)).filter(Boolean);
  out.h2s = [...doc.querySelectorAll('h2')].map(h => T(h.innerText)).filter(Boolean).slice(0, 40);

  // ---- nav signature
  const nav = doc.querySelector('nav.nav, .trw-injected-nav') || doc.querySelector('header nav') || doc.querySelector('nav:not(.bc):not([class*="bread"])');
  out.navPresent = !!nav;
  if (nav) {
    out.navSig = [...nav.querySelectorAll('a')].map(a => ({ t: T(a.innerText), h: (a.getAttribute('href') || '').replace(/^https?:\/\/[^/]+/, '') })).filter(x => x.t || x.h);
    const cs = getComputedStyle(nav);
    out.navBg = cs.backgroundColor;
    out.navPos = cs.position;
  }
  // ---- footer signature
  const footer = doc.querySelector('footer, .trw-injected-footer, [class*="footer"]');
  out.footerPresent = !!footer;
  if (footer) {
    out.footerSig = [...footer.querySelectorAll('a')].map(a => ({ t: T(a.innerText), h: (a.getAttribute('href') || '').replace(/^https?:\/\/[^/]+/, '') })).filter(x => x.t || x.h).slice(0, 80);
    out.footerHeadings = [...footer.querySelectorAll('h2,h3,h4,strong,[class*="col-title"],[class*="heading"]')].map(e => T(e.innerText)).filter(Boolean).slice(0, 20);
    out.footerText = T(footer.innerText).slice(0, 900);
  }
  // ---- fingerprints / markers
  const htmlStr = doc.documentElement.outerHTML;
  out.markers = {};
  for (const m of cfg.markers) out.markers[m] = htmlStr.includes(m);
  // ---- breadcrumb
  const bc = doc.querySelector('nav.bc, .bc, [class*="breadcrumb"]');
  out.bcPresent = !!bc;
  if (bc) {
    const r = bc.getBoundingClientRect();
    out.bcRect = { x: Math.round(r.x), w: Math.round(r.width), y: Math.round(r.y) };
    out.bcLinks = [...bc.querySelectorAll('a')].map(a => ({ t: T(a.innerText), h: (a.getAttribute('href') || '').replace(/^https?:\/\/[^/]+/, '') }));
    out.bcText = T(bc.innerText).slice(0, 300);
    out.bcClass = bc.className;
  }
  // ---- images
  const imgs = [...doc.images];
  out.imgTotal = imgs.length;
  out.imgBroken = imgs.filter(i => i.complete && i.naturalWidth === 0 && i.src).map(i => ({ src: i.src.slice(0, 200), alt: i.alt })).slice(0, 10);
  const nonDecorative = imgs.filter(i => (i.width > 40 && i.height > 40) || (!i.width && !i.height));
  out.imgMissingAlt = nonDecorative.filter(i => !i.alt || !i.alt.trim()).map(i => (i.currentSrc || i.src || '').split('/').pop().slice(0, 80)).slice(0, 10);
  out.imgMissingAltCount = nonDecorative.filter(i => !i.alt || !i.alt.trim()).length;
  if (cfg.photon) {
    const nonPhoton = imgs.filter(i => i.src && i.src.startsWith('http') && !/i[0-3]\.wp\.com|\?.*(resize|ssl)=/.test(i.src) && !i.src.startsWith('data:'));
    out.imgNonPhoton = nonPhoton.map(i => i.src.slice(0, 160)).slice(0, 8);
    out.imgNonPhotonCount = nonPhoton.length;
  }
  // ---- JSON-LD
  out.jsonLd = [];
  for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const p = JSON.parse(s.textContent);
      const types = [].concat(p).flatMap(x => x['@graph'] ? x['@graph'].map(g => g['@type']) : [x['@type']]);
      out.jsonLd.push({ ok: true, types: types.flat().filter(Boolean).slice(0, 10) });
    } catch (e) { out.jsonLd.push({ ok: false, err: String(e).slice(0, 120) }); }
  }
  // ---- em-dash scan (visible text, minus JS placeholders)
  const clone = doc.body.cloneNode(true);
  for (const sel of ['script', 'style', 'noscript', '[data-range-text]', '[data-date-pill]', '[data-round-count]']) {
    clone.querySelectorAll(sel).forEach(e => e.remove());
  }
  const visText = clone.innerText || '';
  const emMatches = [];
  const emRe = /[—–]|(?<![-!<])--(?![->])/g;
  let m;
  while ((m = emRe.exec(visText)) && emMatches.length < 8) {
    emMatches.push(T(visText.slice(Math.max(0, m.index - 40), m.index + 40)));
  }
  out.emDash = emMatches;
  // alt + JSON-LD em-dash
  out.emDashAlt = imgs.filter(i => /[—–]|--/.test(i.alt || '')).map(i => i.alt.slice(0, 100)).slice(0, 5);
  // ---- autop signatures
  out.autopSigs = [];
  for (const [name, re_] of [
    ['a-card-empty-p', /<a class="[a-z-]*-card"[^>]*><\/p>/],
    ['p-close-a', /<p>\s*<\/a>/],
    ['p-div-section', /<\/p>\s*<\/div>\s*<\/section>/],
    ['p-comment', /<p><!--/],
    ['p-script', /<p><script>/],
  ]) { if (re_.test(htmlStr)) out.autopSigs.push(name); }
  // ---- TRW content rules
  if (cfg.trw) {
    out.byline = htmlStr.includes('Reviewed by The Right Workshop team');
    out.kakiBukit = htmlStr.includes('Kaki Bukit');
    out.unitNo = htmlStr.includes('#02-61');
  }
  // ---- overflow
  const iw = window.innerWidth;
  out.docWidth = Math.max(doc.documentElement.scrollWidth, doc.body ? doc.body.scrollWidth : 0);
  out.overflowX = out.docWidth > iw + 2;
  out.overflowOffenders = [];
  if (out.overflowX) {
    let n = 0;
    for (const el of doc.querySelectorAll('body *')) {
      if (++n > 6000 || out.overflowOffenders.length >= 8) break;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.right > iw + 8 || r.left < -8)) {
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none') continue;
        if (['HTML', 'BODY'].includes(el.tagName)) continue;
        // skip offscreen-by-design (transforms/sliders)
        if (cs.position === 'fixed' && r.right <= 0) continue;
        out.overflowOffenders.push({ sel: el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.') : ''), l: Math.round(r.left), r: Math.round(r.right) });
      }
    }
  }
  // ---- anchors
  out.missingAnchors = [];
  for (const a of doc.querySelectorAll('a[href^="#"]')) {
    const id = a.getAttribute('href').slice(1);
    if (id && !doc.getElementById(id) && !doc.querySelector(`[name="${CSS.escape(id)}"]`)) out.missingAnchors.push(id);
  }
  out.missingAnchors = [...new Set(out.missingAnchors)].slice(0, 10);
  // ---- links
  const origin = location.origin;
  const internal = new Set(), external = new Set(), deadCta = [];
  for (const a of doc.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript')) {
      const t = T(a.innerText);
      if ((href === '#' || href === '' || href === 'javascript:void(0)') && t) deadCta.push(t.slice(0, 60));
      continue;
    }
    try {
      const u = new URL(href, location.href);
      if (u.origin === origin) internal.add(u.pathname + (u.search || '')); else external.add(u.origin + u.pathname);
    } catch (e) {}
  }
  out.internalLinks = [...internal].slice(0, 300);
  out.externalLinks = [...external].slice(0, 60);
  out.deadCtas = [...new Set(deadCta)].slice(0, 10);
  // ---- text for content review
  out.visibleText = visText.slice(0, 40000);
  return out;
}
"""

PERF_JS = r"""
() => new Promise((resolve) => {
  const out = { lcp: null, cls: null };
  try {
    let cls = 0;
    new PerformanceObserver((l) => { for (const e of l.getEntries()) { if (!e.hadRecentInput) cls += e.value; } }).observe({ type: 'layout-shift', buffered: true });
    let lcp = null;
    new PerformanceObserver((l) => { const es = l.getEntries(); if (es.length) lcp = es[es.length - 1].startTime; }).observe({ type: 'largest-contentful-paint', buffered: true });
    setTimeout(() => resolve({ lcp: lcp ? Math.round(lcp) : null, cls: Math.round(cls * 1000) / 1000 }), 700);
  } catch (e) { resolve(out); }
})
"""

MOBILE_JS = r"""
() => {
  const out = {};
  const T = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const iw = window.innerWidth, ih = window.innerHeight;
  // tap targets: visible interactive elements smaller than 40x40 CSS px
  const small = [];
  for (const el of document.querySelectorAll('a, button, [role=button], input[type=submit]')) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const txt = T(el.innerText);
    if (!txt) continue;
    if ((r.width < 36 || r.height < 32) && small.length < 12) small.push({ t: txt.slice(0, 40), w: Math.round(r.width), h: Math.round(r.height) });
  }
  out.smallTapTargets = small;
  // tiny fonts
  let tiny = 0, checked = 0;
  for (const el of document.querySelectorAll('p, li, span, a, td, div')) {
    if (checked++ > 2500) break;
    if (!el.childNodes.length || !T(el.innerText)) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs && fs < 11) tiny++;
  }
  out.tinyFontCount = tiny;
  // intrusive fixed overlays
  out.bigFixed = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if ((cs.position === 'fixed' || cs.position === 'sticky') && cs.display !== 'none') {
      const r = el.getBoundingClientRect();
      if (r.width * r.height > iw * ih * 0.3 && r.top < ih && r.bottom > 0 && cs.visibility !== 'hidden') {
        const op = parseFloat(cs.opacity);
        if (op > 0.05) out.bigFixed.push(el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\s+/).slice(0, 2).join('.'));
      }
    }
    if (out.bigFixed.length >= 5) break;
  }
  return out;
}
"""

HAMBURGER_SELS = [".hamburger", ".menu-toggle", ".nav-toggle", "button[aria-label*='menu' i]",
                  "[class*='hamburger']", ".mobile-menu-toggle", "[class*='menu-btn']", "[class*='menu-icon']",
                  "nav button", "header button"]


def slugify(url):
    p = urlparse(url).path.strip("/")
    return p.replace("/", "__") if p else "home"


async def lazy_scroll(page):
    try:
        await page.evaluate("""async () => {
            const h = document.body.scrollHeight;
            for (let y = 0; y < h; y += 700) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); }
            window.scrollTo(0, 0);
        }""")
        await page.wait_for_timeout(1200)
    except Exception:
        pass


async def segments(page, prefix, shotdir, vp_h, max_mids=4):
    shots = []
    try:
        total = await page.evaluate("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)")
        positions = [0]
        y = vp_h
        while y < total - vp_h and len(positions) < 1 + max_mids:
            positions.append(y)
            y += int(vp_h * 1.4)
        for i, pos in enumerate(positions):
            await page.evaluate(f"window.scrollTo(0, {pos})")
            await page.wait_for_timeout(220)
            f = shotdir / f"{prefix}{i}.jpg"
            await page.screenshot(path=str(f), type="jpeg", quality=55)
            shots.append(f.name)
        # footer segment
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(300)
        f = shotdir / f"{prefix}F.jpg"
        await page.screenshot(path=str(f), type="jpeg", quality=55)
        shots.append(f.name)
    except Exception as e:
        shots.append(f"ERR:{e}")
    return shots


def mk_listeners(page, store):
    page.on("pageerror", lambda exc: store["js_errors"].append(str(exc)[:300]) if len(store["js_errors"]) < 15 else None)

    def on_console(msg):
        try:
            if msg.type == "error":
                t = msg.text
                if any(n in t for n in CONSOLE_NOISE):
                    return
                if "Failed to load resource" in t:
                    if len(store["resource_errors"]) < 15:
                        store["resource_errors"].append(t[:250])
                elif len(store["console_errors"]) < 15:
                    store["console_errors"].append(t[:300])
        except Exception:
            pass
    page.on("console", on_console)

    def on_response(resp):
        try:
            if resp.status >= 400 and len(store["failed_requests"]) < 25:
                store["failed_requests"].append({"u": resp.url[:220], "s": resp.status})
        except Exception:
            pass
    page.on("response", on_response)


async def capture(browser, url, sem):
    async with sem:
        slug = slugify(url)
        entry = {"url": url, "slug": slug, "site": SITE}
        shotdir = OUT / "shots" / slug
        shotdir.mkdir(exist_ok=True)
        cfg = {
            "markers": ["NAV-WHITE-PATCH-2026-04-29", "MOBILE-NAV-FIX-2026-04-25",
                        "footer-social-btn", "footer-brand-logo", "footer-nav-col", "footer-brand-tag"] if IS_TRW else [],
            "photon": IS_TRW, "trw": IS_TRW,
        }
        # ---------- desktop ----------
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        store = {"js_errors": [], "console_errors": [], "resource_errors": [], "failed_requests": []}
        mk_listeners(page, store)
        try:
            resp = await page.goto(url, wait_until="commit", timeout=40000)
            entry["status"] = resp.status if resp else None
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            await lazy_scroll(page)
            entry["perf"] = await page.evaluate(PERF_JS)
            data = await page.evaluate(EXTRACT_JS, cfg)
            vis_text = data.pop("visibleText", "")
            (OUT / "text" / f"{slug}.txt").write_text(f"URL: {url}\nTITLE: {data.get('title')}\nMETA: {data.get('metaDescription')}\n---\n{vis_text}")
            html = await page.content()
            (OUT / "html" / f"{slug}.html").write_text(html)
            entry["desktop"] = data
            entry["desktopShots"] = await segments(page, "d", shotdir, 900)
        except Exception as e:
            entry["desktopError"] = f"{type(e).__name__}: {e}"
        # quick overflow probes
        entry["probes"] = {}
        for w, h in [(1280, 800), (768, 1024)]:
            try:
                await page.set_viewport_size({"width": w, "height": h})
                await page.wait_for_timeout(500)
                ov = await page.evaluate("() => { const dw = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth); return { dw, iw: window.innerWidth, of: dw > window.innerWidth + 2 }; }")
                entry["probes"][str(w)] = ov
                if ov.get("of"):
                    await page.evaluate("window.scrollTo(0,0)")
                    await page.screenshot(path=str(shotdir / f"ovf-{w}.jpg"), type="jpeg", quality=55)
            except Exception as e:
                entry["probes"][str(w)] = {"err": str(e)[:120]}
        entry["desktopConsole"] = store
        await ctx.close()
        # ---------- phone ----------
        ctx = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
        page = await ctx.new_page()
        pstore = {"js_errors": [], "console_errors": [], "resource_errors": [], "failed_requests": []}
        mk_listeners(page, pstore)
        try:
            await page.goto(url, wait_until="commit", timeout=40000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            await lazy_scroll(page)
            pdata = await page.evaluate("""() => { const dw = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth); return { docWidth: dw, iw: window.innerWidth, overflowX: dw > window.innerWidth + 2 }; }""")
            if pdata.get("overflowX"):
                pdata["offenders"] = await page.evaluate("""() => { const iw = window.innerWidth, out = []; let n = 0;
                    for (const el of document.querySelectorAll('body *')) { if (++n > 6000 || out.length >= 8) break;
                      const r = el.getBoundingClientRect(); if (r.width > 0 && (r.right > iw + 8 || r.left < -8)) {
                        const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') continue;
                        if (cs.position === 'fixed' && r.right <= 0) continue;
                        out.push(el.tagName.toLowerCase() + '.' + String(el.className).trim().split(/\\s+/).slice(0,2).join('.') + ' r=' + Math.round(r.right)); } }
                    return out; }""")
            pdata.update(await page.evaluate(MOBILE_JS))
            entry["phone"] = pdata
            entry["phoneShots"] = await segments(page, "p", shotdir, 844)
            # hamburger test
            ham = {"found": False}
            for sel in HAMBURGER_SELS:
                try:
                    el = page.locator(sel).first
                    if await el.count() and await el.is_visible():
                        ham["found"] = True
                        ham["sel"] = sel
                        await page.evaluate("window.scrollTo(0,0)")
                        await el.click(timeout=3000)
                        await page.wait_for_timeout(800)
                        ham["afterClick"] = await page.evaluate("""() => {
                            const iw = window.innerWidth, ih = window.innerHeight; let drawer = null;
                            for (const el of document.querySelectorAll('nav, [class*="menu"], [class*="drawer"], [class*="nav"]')) {
                              const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
                              if (cs.display !== 'none' && cs.visibility !== 'hidden' && r.width * r.height > iw * ih * 0.2) {
                                const links = [...el.querySelectorAll('a')].filter(a => { const ar = a.getBoundingClientRect(); return ar.width > 0 && ar.height > 0; });
                                if (links.length >= 3) { drawer = { links: links.length, bg: cs.backgroundColor, cls: String(el.className).slice(0, 80) }; break; } } }
                            return drawer; }""")
                        await page.screenshot(path=str(shotdir / "menu.jpg"), type="jpeg", quality=60)
                        break
                except Exception as e:
                    ham["err"] = str(e)[:150]
            entry["hamburger"] = ham
        except Exception as e:
            entry["phoneError"] = f"{type(e).__name__}: {e}"
        entry["phoneConsole"] = {k: v for k, v in pstore.items() if v}
        await ctx.close()
        with open(METRICS, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"done {slug}", flush=True)


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        todo = [u for u in URLS if slugify(u) not in done_slugs]
        print(f"{SITE}: {len(todo)} urls to capture ({len(done_slugs)} already done)", flush=True)
        await asyncio.gather(*[capture(browser, u, sem) for u in todo])
        await browser.close()
    print("ALL DONE", flush=True)


asyncio.run(main())
