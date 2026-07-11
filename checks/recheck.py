"""
Recheck producers for CURATED bug-log findings (Des v2 re-check logic).

Background
----------
bug-log.jsonl carries a batch of "curated" findings from the 2026-07-03 manual
deep audit. Their check_ids (e.g. mobile_menu_no_open, footer_five_variants) have
no producer in the live harness, so reconcile() — which hard-gates on
reporters.bug_log.HARNESS_CHECK_IDS — can never auto-close them, even once the
underlying bug is fixed live. They froze at first_seen and needed a human to
close_finding() by hand.

This module supplies one producer per curated check_id that CAN be re-tested
programmatically. Each producer re-tests the SPECIFIC recorded bug (using the
stored record's url_list / evidence) and returns a finding dict when the bug
STILL reproduces, else None. src/run.py runs these after the normal crawl (same
gate as reconcile: full sweeps only) so:
  * still-broken   -> a finding flows through dedupe/route/log_finding, bumping
                      last_seen (and its id lands in current_check_ids, so
                      reconcile leaves it open).
  * fixed          -> no finding, id absent from current_check_ids, and because
                      its id is now in HARNESS_CHECK_IDS, reconcile auto-closes
                      it with a real MTTR.
  * crashed / could-not-verify -> a synthetic low-severity keep-open finding is
                      emitted so the id stays in current_check_ids and CANNOT be
                      false-closed this sweep (a crashed recheck must never read
                      as "bug absent").

Adding a curated id to HARNESS_CHECK_IDS is only safe BECAUSE a producer here
emits it on reproduction. Never add an id to HARNESS_CHECK_IDS without a
producer in REGISTRY below — that would cause an immediate false mass-close.

Two producer flavors (mirroring checks/content_rules.py + checks/visual.py):
  * HTML-based  (sync):  producer(record, site, ctx) -> dict | None
                         ctx exposes budget-aware fetch()/head_status() plus the
                         sweep's sitemap; the testable core is the pure helper
                         functions below (meta_desc_css_leak, footer_signature,
                         sunday_closing_times, vet_report_violation, ...), each
                         operating on an HTML string so they unit-test with
                         small fixtures.
  * Page-based  (async): producer(page, record, site) -> dict | None
                         inspects one already-loaded Playwright page (the
                         orchestrator handles viewport + navigation).

Deliberately NOT automatable — left OUT of REGISTRY and OUT of
HARNESS_CHECK_IDS, so they remain manual-close-only (bug_log.close_finding):
  * footer_map_black_box      — needs a human/visual judgement of a black map embed
  * carplate_black_box_masking — house-rule (mosaic vs black box) visual judgement
  * slow_lcp                  — perf metric; belongs in the perf tooling, not a pass/fail recheck
  * archive_count_mismatch    — semantic count reconciliation; too brittle to automate safely
"""
import re
from urllib.parse import urlparse, urljoin

try:
    import requests  # in requirements.txt (used by reporters/telegram.py)
except Exception:  # pragma: no cover - fallback if requests missing
    requests = None
import urllib.request
import urllib.error

from bs4 import BeautifulSoup

from checks import content_rules

UA = "Mozilla/5.0 (compatible; DesRecheck/1.0; +https://therightworkshop.com)"
FETCH_TIMEOUT = 20
HEAD_TIMEOUT = 10
DEAD_STATUSES = (404, 410)


class BudgetExhausted(Exception):
    """Raised by ctx.fetch()/page navigation when the per-site page-load cap is hit.
    Caught by the orchestrator, which then keeps the finding open (never closes it)."""


# ---------------------------------------------------------------------------
# Pure HTML helpers (unit-tested with fixtures — no network, no browser).
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)", re.I)

# Automatic-delivery phrasing for the vet-report contradiction. The fix removes
# the "sent automatically / after each block" side (keeping "on request"); so
# the presence of any of these means the contradiction is unresolved.
VET_BANNED_RE = [
    re.compile(r"sent automatically", re.I),
    re.compile(r"receives? a structured report(?! on request)", re.I),
    re.compile(r"report after (?:each|every) block", re.I),
]


def _norm_href(href):
    """Normalise a link to a comparable path key: drop scheme/host/query/fragment,
    lower-case, strip a trailing slash. Non-navigational links return ''."""
    if not href:
        return ""
    h = href.strip()
    if h.startswith(("mailto:", "tel:", "javascript:")) or h == "#" or h.startswith("#"):
        return ""
    path = urlparse(h).path or "/"
    return path.rstrip("/").lower() or "/"


def meta_desc_css_leak(html):
    """Return an evidence string if <meta name=description> looks like a leaked
    CSS/dev comment (contains '/*', '{' or '}', or starts with a comment), else None."""
    soup = BeautifulSoup(html, "html.parser")
    m = soup.find("meta", attrs={"name": "description"})
    content = (m.get("content") if m else None) or ""
    stripped = content.lstrip()
    if not content:
        return None
    if "/*" in content or "{" in content or "}" in content or stripped.startswith(("/*", "<!--", "*/")):
        return f"meta description looks like a CSS/dev comment: {content[:120]!r}"
    return None


def footer_signature(html):
    """Normalise a page's <footer> to a sorted tuple of distinct link paths.
    Two pages with the same footer produce the same signature; a missing footer
    is its own signature. Used to detect footer-variant drift across pages."""
    soup = BeautifulSoup(html, "html.parser")
    footer = soup.find("footer")
    if footer is None:
        return ("__no_footer__",)
    paths = set()
    for a in footer.find_all("a"):
        key = _norm_href(a.get("href"))
        if key:
            paths.add(key)
    return tuple(sorted(paths))


def sunday_closing_times(html_or_text):
    """Extract the distinct Sunday CLOSING times from a page.

    The spec's example regex /Sun[^<{]{0,30}?(time)/ captures the FIRST time
    after 'Sun', which on the live pages is the OPENING time (e.g. '10am' in
    'Sun, 10am to 2:30pm') and would false-flag every page as contradictory.
    We instead take the LAST time inside the Sunday window as the closing time
    ('10am to 2:30pm' -> 2:30pm; 'Sundays after 2:30pm ...' -> 2:30pm), so a
    site that says 2:30pm everywhere yields ONE distinct closing time and does
    not fire, while a stray '2pm' footer yields two and does.
    """
    text = content_rules.visible_text(html_or_text) if "<" in html_or_text else html_or_text
    out = set()
    for m in re.finditer(r"Sun[a-z]*", text, re.I):
        window = text[m.start(): m.start() + 45]
        times = _TIME_RE.findall(window)
        if times:
            out.add(times[-1].lower().replace(" ", ""))
    return out


def vet_report_violation(text):
    """Return the matched automatic-delivery phrase(s) if the vet-report
    contradiction is still present, else None. `text` should include visible
    prose AND JSON-LD (callers concatenate them)."""
    hits = [rx.pattern for rx in VET_BANNED_RE if rx.search(text)]
    return "; ".join(hits) if hits else None


def autop_script_wrap(html):
    """True if wpautop has wrapped a <script> in a <p> (the p_wrapped_script
    signature) — reuses checks/content_rules.py's own autop detection."""
    for name, rx in content_rules.AUTOP_SIGNATURES:
        if name == "p_wrapped_script" and rx.search(html):
            return True
    return False


def footer_has_endash(html):
    """True if the page's <footer> visible text contains an en/em dash."""
    soup = BeautifulSoup(html, "html.parser")
    footer = soup.find("footer")
    if footer is None:
        return False
    return bool(re.search(r"[–—]", footer.get_text(" ", strip=True)))


def header_marker_absent(html, marker):
    """True if the header scrim/polish marker (proof the deployed fix is present)
    is missing. marker falsy -> never fires (disabled)."""
    if not marker:
        return False
    return marker not in html


def _text_and_jsonld(html):
    """Visible prose plus any JSON-LD script bodies (visible_text strips scripts)."""
    text = content_rules.visible_text(html)
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text += " " + (s.get_text() or "")
    return text


def expand_phantom_targets(evidence, cap=10):
    """Expand the compact 404-target notation in a phantom_topic_tag_links record
    evidence string into concrete paths. Handles the pipe form
    '/topics/a|b|c/' -> ['/topics/a/', '/topics/b/', '/topics/c/']."""
    out = []
    for token in re.findall(r"/[A-Za-z0-9|\-/]+/?", evidence):
        parts = [p for p in token.split("/") if p != ""]
        if not parts:
            continue
        idx = next((i for i, p in enumerate(parts) if "|" in p), None)
        if idx is None:
            out.append("/" + "/".join(parts) + "/")
        else:
            for alt in parts[idx].split("|"):
                np = parts[:idx] + [alt] + parts[idx + 1:]
                out.append("/" + "/".join(np) + "/")
    # de-dupe, preserve order
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq[:cap]


# ---------------------------------------------------------------------------
# Runner context: budget-aware fetching + link liveness (shared HTML cache).
# ---------------------------------------------------------------------------
class RecheckCtx:
    """Shared per-site helper for producers: full-page GETs (budget-limited,
    cached) and HEAD link-liveness probes (unbudgeted). Budgets are SPLIT:
    plain HTTP GETs are milliseconds (http_budget, generous), browser
    navigations dominate sweep runtime (nav_budget, tight). One shared pool
    starved the AURA page producers on 2026-07-12 — 5 rechecks deferred after
    the HTML producers ate all 15 units on cheap GETs."""

    def __init__(self, site, sitemap_urls, http_budget=40, nav_budget=15):
        self.site = site
        self.base = (site.get("url") or "").rstrip("/")
        self.sitemap_urls = list(sitemap_urls or [])
        self.http_budget = http_budget
        self.nav_budget = nav_budget
        self._html_cache = {}

    def abs_url(self, url):
        if url.startswith("http"):
            return url
        return urljoin(self.base + "/", url.lstrip("/"))

    def spend_http(self):
        if self.http_budget <= 0:
            raise BudgetExhausted("per-site HTTP fetch budget exhausted")
        self.http_budget -= 1

    def spend_nav(self):
        if self.nav_budget <= 0:
            raise BudgetExhausted("per-site browser-navigation budget exhausted")
        self.nav_budget -= 1

    def fetch(self, url):
        """Full GET (follow redirects). Cached; a cache miss spends 1 load unit.
        Returns (status_code, html). Network errors raise (caller keeps open)."""
        u = self.abs_url(url)
        if u in self._html_cache:
            return self._html_cache[u]
        self.spend_http()
        if requests is not None:
            r = requests.get(u, headers={"User-Agent": UA}, timeout=FETCH_TIMEOUT, allow_redirects=True)
            result = (r.status_code, r.text)
        else:  # pragma: no cover
            req = urllib.request.Request(u, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                result = (resp.status, resp.read().decode("utf-8", "replace"))
        self._html_cache[u] = result
        return result

    def head_status(self, url):
        """Final status after redirects for a link-liveness check. HEAD, falling
        back to GET on method-not-allowed. Does NOT spend page-load budget.
        Returns an int, or None on a transient/network error (treated as alive)."""
        u = self.abs_url(url)
        try:
            if requests is not None:
                r = requests.head(u, headers={"User-Agent": UA}, timeout=HEAD_TIMEOUT, allow_redirects=True)
                if r.status_code in (403, 405, 501):
                    r = requests.get(u, headers={"User-Agent": UA}, timeout=HEAD_TIMEOUT,
                                     allow_redirects=True, stream=True)
                    code = r.status_code
                    r.close()
                    return code
                return r.status_code
            req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=HEAD_TIMEOUT) as resp:  # pragma: no cover
                return resp.status
        except urllib.error.HTTPError as e:  # pragma: no cover
            return e.code
        except Exception:
            return None

    def other_pages(self, exclude, n):
        """Up to n non-home sitemap pages not already in `exclude` (for padding)."""
        ex = {self.abs_url(u) for u in exclude}
        out = []
        for u in self.sitemap_urls:
            au = self.abs_url(u)
            path = urlparse(au).path.rstrip("/")
            if path in ("", "/"):
                continue
            if au in ex or au in out:
                continue
            out.append(au)
            if len(out) >= n:
                break
        return out


def _finding(record, evidence, urls=None):
    """Build a still-reproduces finding at the record's own severity."""
    f = {"check": record["check_id"], "severity": record.get("severity", "medium"), "evidence": evidence}
    if urls:
        f["urls"] = urls
    return f


# ===========================================================================
# HTML-based producers (sync): producer(record, site, ctx) -> dict | None
# ===========================================================================
def rc_autop_p_script_wrap(record, site, ctx):
    hits = []
    for u in record.get("url_list", [])[:5]:
        status, html = ctx.fetch(u)
        if status == 200 and autop_script_wrap(html):
            hits.append(u)
    if hits:
        return _finding(record, f"<p><script> wpautop wrap still present on {len(hits)} page(s)", hits)
    return None


def rc_meta_description_css_leak(record, site, ctx):
    hits, evid = [], []
    for u in record.get("url_list", [])[:5]:
        status, html = ctx.fetch(u)
        if status == 200:
            leak = meta_desc_css_leak(html)
            if leak:
                hits.append(u)
                evid.append(leak)
    if hits:
        return _finding(record, "; ".join(evid[:2]), hits)
    return None


def rc_coe_hub_dead_bidding_links(record, site, ctx):
    hub = record.get("url_list", [None])[0] or (ctx.base + "/coe-results/")
    status, html = ctx.fetch(hub)
    if status != 200:
        # can't read the hub -> can't confirm the bug is gone; keep open
        raise RuntimeError(f"COE hub returned HTTP {status}")
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    links = sorted({a.get("href") for a in main.find_all("a")
                    if a.get("href") and re.search(r"bidding", a.get("href"), re.I)})
    dead = [l for l in links if ctx.head_status(l) in DEAD_STATUSES]
    if dead:
        return _finding(record, f"{len(dead)} bidding link(s) still 404: {dead[:6]}", [hub])
    return None


def rc_phantom_topic_tag_links(record, site, ctx):
    targets = expand_phantom_targets(record.get("evidence", ""), cap=10)
    dead = [t for t in targets if ctx.head_status(t) in DEAD_STATUSES]
    if dead:
        return _finding(record, f"{len(dead)} phantom link target(s) still 404: {dead}", [ctx.base + "/"])
    return None


def rc_home_header_contrast(record, site, ctx):
    marker = site.get("header_scrim_marker") or "aura-chrome-polish-css"
    home = record.get("url_list", [None])[0] or (ctx.base + "/")
    status, html = ctx.fetch(home)
    if status == 200 and header_marker_absent(html, marker):
        return _finding(record, f"header scrim marker {marker!r} absent — the deployed contrast fix is missing", [home])
    return None


def rc_hub_links_to_unbuilt_conditions(record, site, ctx):
    hub = ctx.base + "/conditions/"
    status, html = ctx.fetch(hub)
    if status != 200:
        raise RuntimeError(f"/conditions/ hub returned HTTP {status}")
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    links = sorted({a.get("href") for a in main.find_all("a")
                    if a.get("href") and "/conditions/" in a.get("href")
                    and _norm_href(a.get("href")) not in ("/conditions",)})
    dead = [l for l in links if ctx.head_status(l) in DEAD_STATUSES]
    if dead:
        return _finding(record, f"{len(dead)} condition hub link(s) still 404: {dead[:8]}", [hub])
    return None


def rc_nav_visit_only_homepage(record, site, ctx):
    pages = list(record.get("url_list", []))
    pages += ctx.other_pages(exclude=pages, n=3)
    pages = pages[:5]
    missing = []
    for u in pages:
        if urlparse(ctx.abs_url(u)).path.rstrip("/") in ("", "/"):
            continue  # homepage-anchor variant is exempt
        status, html = ctx.fetch(u)
        if status != 200:
            continue
        soup = BeautifulSoup(html, "html.parser")
        navs = soup.select(site.get("nav_selector") or "header nav, nav")
        has_visit = False
        for nav in navs:
            for a in nav.find_all("a"):
                if a.get_text(strip=True).lower() == "visit" or "#visit" in (a.get("href") or ""):
                    has_visit = True
                    break
            if has_visit:
                break
        if not has_visit:
            missing.append(u)
    if missing:
        return _finding(record, f"'Visit' nav link missing on {len(missing)} non-home page(s): {missing}", missing)
    return None


def rc_footer_five_variants(record, site, ctx):
    pages = list(record.get("url_list", []))
    if len(pages) < 6:
        pages += ctx.other_pages(exclude=pages, n=6 - len(pages))
    pages = pages[:6]
    sigs = {}
    for u in pages:
        status, html = ctx.fetch(u)
        if status != 200:
            continue
        sigs.setdefault(footer_signature(html), []).append(u)
    if len(sigs) > 1:
        summary = "; ".join(f"variant#{i+1}: {pgs[0]}" for i, pgs in enumerate(sigs.values()))
        return _finding(record, f"{len(sigs)} distinct footer signatures across sampled pages — {summary}", pages)
    return None


def rc_opening_hours_contradictions(record, site, ctx):
    pages = list(record.get("url_list", []))
    home = ctx.base + "/"
    if home not in {ctx.abs_url(p) for p in pages}:
        pages.append(home)
    pages = pages[:6]
    times = set()
    for u in pages:
        status, html = ctx.fetch(u)
        if status == 200:
            times |= sunday_closing_times(html)
    if len(times) > 1:
        return _finding(record, f"Sunday closing time disagrees across pages: {sorted(times)}", pages)
    return None


def rc_vet_report_policy_contradiction(record, site, ctx):
    hits, evid = [], []
    for u in record.get("url_list", []):
        status, html = ctx.fetch(u)
        if status == 200:
            v = vet_report_violation(_text_and_jsonld(html))
            if v:
                hits.append(u)
                evid.append(v)
    if hits:
        return _finding(record, f"automatic-delivery vet-report phrasing still present ({'; '.join(sorted(set(evid)))})", hits)
    return None


def rc_en_dash_footer_hours(record, site, ctx):
    pages = list(record.get("url_list", []))
    if len(pages) < 3:
        pages += ctx.other_pages(exclude=pages, n=3 - len(pages))
    pages = pages[:3]
    hits = []
    for u in pages:
        status, html = ctx.fetch(u)
        if status == 200 and footer_has_endash(html):
            hits.append(u)
    if hits:
        return _finding(record, f"en/em dash still in footer hours on {len(hits)} page(s)", hits)
    return None


# ===========================================================================
# Page-based producers (async): producer(page, record, site) -> dict | None
# The orchestrator loads the page at the registry-declared viewport/url first.
# ===========================================================================
_HAMBURGER_JS = r"""
async (sels) => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (r.width > 0 && r.height > 0) && s.visibility !== 'hidden'
        && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  const [toggleSel, drawerSel] = sels || [];
  const genericToggle = '.nav-hamburger, .hamburger, .menu-toggle, .nav-toggle,'
      + ' button[aria-label*="menu" i], [class*="hamburger"], [class*="menu-btn"], #navHamburger, #trwNavHamburger';
  const genericDrawer = '.mobile-nav, [class*="mobile-nav"], [class*="drawer"], nav[class*="menu"], #mobileNav';
  let toggle = null;
  for (const sel of [toggleSel, genericToggle]) {
    if (!sel) continue;
    for (const el of document.querySelectorAll(sel)) { if (vis(el)) { toggle = el; break; } }
    if (toggle) break;
  }
  if (!toggle) return { ok: false, issue: 'no_visible_toggle' };
  let drawer = null;
  for (const sel of [drawerSel, genericDrawer]) {
    if (!sel) continue;
    drawer = document.querySelector(sel);
    if (drawer) break;
  }
  if (!drawer) return { ok: false, issue: 'no_drawer_element' };
  toggle.click();
  await new Promise(r => setTimeout(r, 200));
  const opened = vis(drawer) || drawer.classList.contains('open');
  if (!opened) return { ok: false, issue: 'drawer_did_not_open' };
  return { ok: true };
}
"""


async def rc_mobile_menu_no_open(page, record, site):
    res = await page.evaluate(_HAMBURGER_JS, [None, None])
    if res and not res.get("ok"):
        return _finding(record, f"hamburger tap opened no drawer ({res.get('issue')})")
    return None


async def rc_no_mobile_navigation(page, record, site):
    toggle_sel = site.get("mobile_nav_toggle_selector") or ".aura-mnav-toggle"
    drawer_sel = site.get("mobile_nav_drawer_selector") or ".aura-mnav-drawer"
    res = await page.evaluate(_HAMBURGER_JS, [toggle_sel, drawer_sel])
    if res and not res.get("ok"):
        return _finding(record, f"no working mobile navigation ({res.get('issue')})")
    return None


async def rc_overflow_phone(page, record, site):
    over = await page.evaluate(
        "() => document.documentElement.scrollWidth > (window.innerWidth + 1) ? "
        "{w: document.documentElement.scrollWidth, vw: window.innerWidth} : null")
    if over:
        return _finding(record, f"horizontal overflow: scrollWidth {over['w']} > viewport {over['vw']}")
    return None


async def rc_comparison_table_clipped_mobile(page, record, site):
    bad = await page.evaluate(r"""() => {
        const sel = 'table, .comparison, [class*="comparison"], [class*="compare"], [class*="table"]';
        for (const el of document.querySelectorAll(sel)) {
            if (el.scrollWidth > el.clientWidth + 2) {
                const ox = getComputedStyle(el).overflowX;
                if (ox !== 'auto' && ox !== 'scroll') {
                    return { cls: (el.className || el.tagName), sw: el.scrollWidth, cw: el.clientWidth, ox };
                }
            }
        }
        return null;
    }""")
    if bad:
        return _finding(record, f"comparison/table clipped with overflow-x:{bad['ox']} (scrollWidth {bad['sw']} > clientWidth {bad['cw']}) on {bad['cls']}")
    return None


async def rc_home_hero_left_clip_desktop(page, record, site):
    clip = await page.evaluate(r"""() => {
        const sel = '.hero h1, [class*="hero"] h1, header h1, .entry-title, h1';
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.left < 0) return { left: Math.round(r.left), txt: (el.textContent||'').trim().slice(0,40) };
        }
        return null;
    }""")
    if clip:
        return _finding(record, f"hero H1 clipped at left edge (left={clip['left']}px): {clip['txt']!r}")
    return None


async def rc_cookie_banner_covers_form_mobile(page, record, site):
    covered = await page.evaluate(r"""() => {
        const vw = window.innerWidth, vh = window.innerHeight, area = vw * vh;
        for (const el of document.querySelectorAll('div, section, aside, footer')) {
            const s = getComputedStyle(el);
            if (s.position !== 'fixed' && s.position !== 'sticky') continue;
            if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity||'1') <= 0.05) continue;
            if (!/cookie/i.test(el.textContent || '')) continue;
            const r = el.getBoundingClientRect();
            const w = Math.min(r.right, vw) - Math.max(r.left, 0);
            const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
            if (w > 0 && h > 0 && (w * h) / area > 0.20) {
                return { pct: Math.round((w * h) / area * 100) };
            }
        }
        return null;
    }""")
    if covered:
        return _finding(record, f"cookie banner covers ~{covered['pct']}% of the phone viewport on first paint")
    return None


# ===========================================================================
# Registry — the ONLY curated ids that recheck.py can auto-verify.
# Every id here MUST also be in reporters.bug_log.HARNESS_CHECK_IDS.
# flavor: "html" -> producer(record, site, ctx)
#         "page" -> async producer(page, record, site); orchestrator loads
#                   `urls` at `viewport` (cap N) first.
# ===========================================================================
REGISTRY = {
    # --- TRW ---
    "mobile_menu_no_open":            {"flavor": "page", "producer": rc_mobile_menu_no_open,            "site": "TRW",  "viewport": "phone_ios", "urls": "record", "cap": 3},
    "autop_p_script_wrap":            {"flavor": "html", "producer": rc_autop_p_script_wrap,            "site": "TRW"},
    "meta_description_css_leak":      {"flavor": "html", "producer": rc_meta_description_css_leak,      "site": "TRW"},
    "coe_hub_dead_bidding_links":     {"flavor": "html", "producer": rc_coe_hub_dead_bidding_links,     "site": "TRW"},
    "phantom_topic_tag_links":        {"flavor": "html", "producer": rc_phantom_topic_tag_links,        "site": "TRW"},
    "overflow_phone":                 {"flavor": "page", "producer": rc_overflow_phone,                 "site": "TRW",  "viewport": "phone_ios", "urls": "record", "cap": 4},
    "comparison_table_clipped_mobile":{"flavor": "page", "producer": rc_comparison_table_clipped_mobile,"site": "TRW",  "viewport": "phone_ios", "urls": "record", "cap": 2},
    "home_hero_left_clip_desktop":    {"flavor": "page", "producer": rc_home_hero_left_clip_desktop,    "site": "TRW",  "viewport": "desktop",   "urls": "homepage", "cap": 1},
    # --- AURA ---
    "no_mobile_navigation":           {"flavor": "page", "producer": rc_no_mobile_navigation,           "site": "AURA", "viewport": "phone_ios", "urls": "homepage", "cap": 1},
    "cookie_banner_covers_form_mobile":{"flavor": "page", "producer": rc_cookie_banner_covers_form_mobile,"site": "AURA","viewport": "phone_ios", "urls": "homepage", "cap": 1},
    "home_header_contrast":           {"flavor": "html", "producer": rc_home_header_contrast,           "site": "AURA"},
    "hub_links_to_unbuilt_conditions":{"flavor": "html", "producer": rc_hub_links_to_unbuilt_conditions,"site": "AURA"},
    "nav_visit_only_homepage":        {"flavor": "html", "producer": rc_nav_visit_only_homepage,        "site": "AURA"},
    "footer_five_variants":           {"flavor": "html", "producer": rc_footer_five_variants,           "site": "AURA"},
    "opening_hours_contradictions":   {"flavor": "html", "producer": rc_opening_hours_contradictions,   "site": "AURA"},
    "vet_report_policy_contradiction":{"flavor": "html", "producer": rc_vet_report_policy_contradiction,"site": "AURA"},
    "en_dash_footer_hours":           {"flavor": "html", "producer": rc_en_dash_footer_hours,           "site": "AURA"},
}


def _keep_open(record, reason):
    """Synthetic low-severity finding so a crashed / unverifiable recheck keeps
    its id in current_check_ids and CANNOT be false-closed this sweep."""
    urls = record.get("url_list") or []
    return {
        "check": record["check_id"],
        "severity": "low",
        "evidence": f"recheck could not verify ({reason}) — kept open, not re-tested this sweep",
        "url": urls[0] if urls else "",
        "viewport": "recheck",
    }


def _urls_for(entry, record, ctx):
    if entry.get("urls") == "homepage":
        return [ctx.base + "/"]
    return list(record.get("url_list", []))[: entry.get("cap", 3)]


async def run_site_rechecks(pw, site, open_records, sitemap_urls, http_budget=40, nav_budget=15):
    """Re-test each open curated record whose id is in REGISTRY. Returns a flat
    list of stage-1 finding dicts (check/severity/evidence/url[/viewport]) to be
    appended to the sweep's all_findings BEFORE dedupe. Never raises: every
    producer is wrapped so a crash or exhausted page-load budget emits a
    keep-open synthetic finding instead of silently letting reconcile close the
    bug. Page-based producers share one browser; browser navigations are capped
    by `nav_budget` (default 15) and plain HTTP GETs by `http_budget` (40)."""
    ctx = RecheckCtx(site, sitemap_urls, http_budget=http_budget, nav_budget=nav_budget)
    # Deterministic order: worst severity first so the most important bugs get
    # the page-load budget before any medium ones defer under a tight cap.
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    records = [r for r in open_records if r.get("check_id") in REGISTRY]
    records.sort(key=lambda r: (sev_rank.get(r.get("severity"), 9), r.get("check_id", "")))

    findings = []
    browser = None
    try:
        for record in records:
            entry = REGISTRY[record["check_id"]]
            try:
                if entry["flavor"] == "html":
                    f = entry["producer"](record, site, ctx)
                    if f:
                        for u in (f.pop("urls", None) or [record.get("url_list", [""])[0] if record.get("url_list") else ctx.base + "/"]):
                            findings.append({**f, "url": u, "viewport": "recheck"})
                else:  # page flavor
                    if browser is None:
                        browser = await pw.chromium.launch(headless=True)
                    subs = await _run_page_producer(browser, entry, record, site, ctx)
                    findings.extend(subs)
            except BudgetExhausted:
                findings.append(_keep_open(record, "page-load budget exhausted"))
            except Exception as e:  # noqa: BLE001 - a crashing recheck must never close a bug
                findings.append(_keep_open(record, f"recheck crashed: {e}"))
    finally:
        if browser is not None:
            await browser.close()
    return findings


async def _run_page_producer(browser, entry, record, site, ctx):
    """Load each target URL at the entry's viewport and run the page producer.
    Returns a list of stage-1 findings (one per still-failing URL)."""
    from src.devices import DEVICES
    vp = DEVICES[entry.get("viewport", "phone_ios")]
    urls = _urls_for(entry, record, ctx)
    subs = []
    for u in urls:
        ctx.spend_nav()  # browser navigations get their own (tight) budget
        ctx_args = {"viewport": {"width": vp["width"], "height": vp["height"]}}
        if vp.get("user_agent"):
            ctx_args["user_agent"] = vp["user_agent"]
        if vp.get("is_mobile"):
            ctx_args["is_mobile"] = True
            ctx_args["has_touch"] = True
            ctx_args["device_scale_factor"] = vp.get("device_scale_factor", 2)
        bctx = await browser.new_context(**ctx_args)
        page = await bctx.new_page()
        try:
            await page.goto(ctx.abs_url(u), wait_until="commit", timeout=30000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            f = await entry["producer"](page, record, site)
            if f:
                f["url"] = u
                f["viewport"] = entry.get("viewport", "phone_ios")
                subs.append(f)
        finally:
            await bctx.close()
    return subs
