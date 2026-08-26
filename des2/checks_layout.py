"""Layout defects, judged by measurement.

Ed's brief was "make sure the whole website does not have bad pages or bugs or
things to make the page look off". This module is the "look off" half, and it
works by measuring real geometry in a real browser rather than comparing
screenshots or asking a model for an opinion. Two reasons: a measurement can
name the element and the numbers, which is what the evidence rule demands, and
it does not drift every time Ed publishes something.

Every check here obeys the same three rules:
  1. One page.evaluate, returning plain data. Findings are built in Python.
  2. Invisible elements are never defects. A thing nobody can see cannot look
     wrong, and treating hidden elements as real is how the 73-page shadow
     button false positive happened.
  3. Results are capped. One broken template must not become a hundred pings.
"""
from __future__ import annotations

from des2.models import Evidence, Finding, owner_for

# Sub-pixel rounding is not a defect. Browsers routinely differ by a fraction.
SCROLL_TOLERANCE_PX = 2
# Text is only "cut off" when the hidden part is meaningful, not a stray pixel.
OVERFLOW_TOLERANCE_PX = 4
# Two boxes must share a real area before it counts as a collision, otherwise
# every touching border reads as an overlap.
OVERLAP_MIN_AREA_PX = 400
OVERLAP_MIN_SHARE = 0.25
# Apple and Google both put the comfortable minimum around here.
MIN_TAP_TARGET_PX = 44
MAX_PER_CHECK = 5

# Shared JS: what counts as visible. Kept in one string so no check can drift
# from the others on the single most important predicate in the module.
_VISIBLE_FN = """
const __visible = (el) => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden') return false;
  if (parseFloat(cs.opacity || '1') < 0.05) return false;
  return true;
};
const __sel = (el) => {
  if (!el) return 'unknown';
  if (el.id) return '#' + el.id;
  const cls = (el.className && typeof el.className === 'string')
      ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '';
  return (el.tagName || 'el').toLowerCase() + cls;
};
"""


def _f(check: str, url: str, viewport: str, summary: str, ev: Evidence) -> Finding:
    return Finding(check=check, kind="layout", url=url, viewport=viewport,
                   summary=summary, evidence=ev, owner=owner_for(check))


async def _eval(page, script, arg=None):
    """Evaluate defensively: a browser hiccup must never invent a defect."""
    try:
        return await (page.evaluate(script, arg) if arg is not None else page.evaluate(script))
    except Exception:
        return None


async def check_horizontal_scroll(page, url: str, viewport: str) -> list[Finding]:
    """The page scrolls sideways. Ed's least favourite bug, and a real one on phones."""
    data = await _eval(page, _VISIBLE_FN + """() => {
      const doc = document.documentElement;
      const overflow = doc.scrollWidth - doc.clientWidth;
      if (overflow <= 0) return {overflow: 0, offenders: []};
      const limit = doc.clientWidth;
      const offenders = [];
      for (const el of document.querySelectorAll('body *')) {
        if (!__visible(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.right > limit + 1 || r.left < -1) {
          offenders.push({sel: __sel(el), right: Math.round(r.right), left: Math.round(r.left),
                          width: Math.round(r.width)});
        }
        if (offenders.length >= 8) break;
      }
      return {overflow: Math.round(overflow), viewportWidth: limit, offenders};
    }""")
    if not data or data.get("overflow", 0) <= SCROLL_TOLERANCE_PX:
        return []
    worst = (data.get("offenders") or [{}])[0]
    return [_f("horizontal_scroll", url, viewport,
               f"page scrolls sideways by {data['overflow']}px",
               Evidence(selector=worst.get("sel"),
                        numbers={"overflow_px": data["overflow"],
                                 "viewport_px": data.get("viewportWidth", 0),
                                 "offenders": len(data.get("offenders") or [])}))]


async def check_text_overflow(page, url: str, viewport: str) -> list[Finding]:
    """Text actually cut off SIDEWAYS by a clipping container.

    Deliberately horizontal only. Run against the live site, vertical clipping
    turned out to be overwhelmingly intentional: line clamps on card summaries,
    collapsed accordions, carousel tracks. Reporting it would bury the real
    defect, which is a word or heading sliced off at the edge of its box. If a
    vertical case ever bites us, it comes back with evidence attached.

    Three exclusions, all learned from the first live run against the homepage:
      - accessibility-hidden text (visually-hidden, sr-only, clip-path insets),
        which is SUPPOSED to be clipped and reported a 2118px overflow
      - line-clamped text, where the clip is the design
      - containers rather than text runs, since a nav list with its own
        scrolling reads as a giant overflow while looking perfectly fine
    """
    rows = await _eval(page, _VISIBLE_FN + """() => {
      const out = [];
      const A11Y = /(visually-hidden|screen-reader|sr-only|visuallyhidden)/i;
      const sel = 'p,h1,h2,h3,h4,li,td,th,button,a,span,div.desc,.premium,.title';
      for (const el of document.querySelectorAll(sel)) {
        if (!__visible(el)) continue;
        const txt = (el.textContent || '').trim();
        if (!txt) continue;

        // Hidden-for-screen-readers text is clipped on purpose.
        if (A11Y.test(el.className || '') || (el.closest && el.closest('[class*="visually-hidden"],[class*="sr-only"]'))) continue;

        const cs = getComputedStyle(el);
        if (cs.clipPath && cs.clipPath !== 'none') continue;
        if (cs.position === 'absolute' && (el.getBoundingClientRect().width <= 1 || el.getBoundingClientRect().height <= 1)) continue;

        // Line clamping is a deliberate truncation, not a defect.
        if ((cs.webkitLineClamp && cs.webkitLineClamp !== 'none') ||
            (cs.lineClamp && cs.lineClamp !== 'none')) continue;

        // Judge text runs, not containers. A wrapper full of block children
        // that scrolls is a layout device, not a cut-off sentence.
        if (el.children.length > 2) continue;

        // Ellipsis is a designed truncation ("show a … when it is too long"),
        // not text being sliced off. The first live run flagged every nav item
        // on every page because of it.
        if (cs.textOverflow === 'ellipsis') continue;

        // NOTE: 'text-overflow: clip' is the CSS DEFAULT, so testing for it
        // marks every ordinary element as clipping. That single mistake made
        // the nav mega-menu items report a 227px overflow on every page of the
        // site. Only a real overflow:hidden can actually cut text off.
        const clipsX = cs.overflowX === 'hidden' || cs.overflow === 'hidden';
        if (!clipsX) continue;

        const dx = el.scrollWidth - el.clientWidth;
        // An absurd overflow is a layout device, not a cut-off sentence: the
        // COE hub's stretched card-overlay links report 9999px. Genuine
        // clipped text overruns by tens or low hundreds. Page-level sideways
        // scroll is check_horizontal_scroll's job, not this one.
        if (dx > 4 && dx <= 800) {
          out.push({sel: __sel(el), dx: Math.round(dx), dy: 0, text: txt.slice(0, 60)});
        }
        if (out.length >= 8) break;
      }
      return out;
    }""")
    out = []
    for r in (rows or [])[:MAX_PER_CHECK]:
        if max(r.get("dx", 0), r.get("dy", 0)) <= OVERFLOW_TOLERANCE_PX:
            continue
        out.append(_f("text_overflow", url, viewport,
                      "text is cut off by its container",
                      Evidence(selector=r.get("sel"),
                               numbers={"hidden_x_px": r.get("dx", 0),
                                        "hidden_y_px": r.get("dy", 0)},
                               note=r.get("text", ""))))
    return out


async def check_element_overlap(page, url: str, viewport: str) -> list[Finding]:
    """Text-bearing blocks colliding. Deliberate stacking is excluded."""
    rows = await _eval(page, _VISIBLE_FN + """() => {
      const cands = [];
      for (const el of document.querySelectorAll('h1,h2,h3,p,li,button,a.btn,.btn')) {
        if (!__visible(el)) continue;
        const cs = getComputedStyle(el);
        // Stacking is intentional design, not a collision.
        if (['fixed', 'absolute', 'sticky'].includes(cs.position)) continue;
        if (!el.textContent || !el.textContent.trim()) continue;
        cands.push({el, r: el.getBoundingClientRect()});
        if (cands.length >= 120) break;
      }
      const out = [];
      for (let i = 0; i < cands.length && out.length < 6; i++) {
        for (let j = i + 1; j < cands.length && out.length < 6; j++) {
          const a = cands[i], b = cands[j];
          if (a.el.contains(b.el) || b.el.contains(a.el)) continue;  // parent/child
          const x = Math.max(0, Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left));
          const y = Math.max(0, Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top));
          const area = x * y;
          if (area <= 0) continue;
          const smaller = Math.min(a.r.width * a.r.height, b.r.width * b.r.height) || 1;
          out.push({a: __sel(a.el), b: __sel(b.el), area: Math.round(area),
                    share: +(area / smaller).toFixed(2)});
        }
      }
      return out;
    }""")
    out = []
    for r in (rows or []):
        if r.get("area", 0) < OVERLAP_MIN_AREA_PX or r.get("share", 0) < OVERLAP_MIN_SHARE:
            continue
        out.append(_f("element_overlap", url, viewport,
                      f"{r.get('a')} overlaps {r.get('b')}",
                      Evidence(selector=r.get("a"),
                               numbers={"overlap_px2": r.get("area", 0),
                                        "share_of_smaller": r.get("share", 0)},
                               note=f"other element: {r.get('b')}")))
        if len(out) >= MAX_PER_CHECK:
            break
    return out


async def check_clipped_content(page, url: str, viewport: str) -> list[Finding]:
    """Headings, buttons or nav items cut off by an ancestor's bounds."""
    rows = await _eval(page, _VISIBLE_FN + """() => {
      const out = [];
      for (const el of document.querySelectorAll('h1,h2,h3,button,.btn,nav a')) {
        if (!__visible(el)) continue;
        const r = el.getBoundingClientRect();
        let p = el.parentElement, clipped = null;
        while (p && p !== document.body) {
          const pcs = getComputedStyle(p);
          if (pcs.overflow === 'hidden' || pcs.overflowX === 'hidden' || pcs.overflowY === 'hidden') {
            const pr = p.getBoundingClientRect();
            const cutX = Math.max(0, r.right - pr.right) + Math.max(0, pr.left - r.left);
            const cutY = Math.max(0, r.bottom - pr.bottom) + Math.max(0, pr.top - r.top);
            if (cutX > 4 || cutY > 4) { clipped = {parent: __sel(p), cutX: Math.round(cutX), cutY: Math.round(cutY)}; }
            break;
          }
          p = p.parentElement;
        }
        if (clipped) {
          out.push({sel: __sel(el), text: (el.textContent || '').trim().slice(0, 50), ...clipped});
        }
        if (out.length >= 8) break;
      }
      return out;
    }""")
    return [_f("clipped_content", url, viewport,
               "content is cut off by its parent",
               Evidence(selector=r.get("sel"),
                        numbers={"cut_x_px": r.get("cutX", 0), "cut_y_px": r.get("cutY", 0)},
                        note=f"clipped by {r.get('parent')}: {r.get('text', '')}"))
            for r in (rows or [])[:MAX_PER_CHECK]]


async def check_tap_targets(page, url: str, viewport: str) -> list[Finding]:
    """Phone only: things a thumb cannot reliably hit.

    Hidden and zero-size elements are excluded, so a decorative or collapsed
    control is never reported as a too-small button.
    """
    if viewport != "phone":
        return []
    rows = await _eval(page, _VISIBLE_FN + """(minPx) => {
      const out = [];
      for (const el of document.querySelectorAll('a,button,input,[role=button]')) {
        if (!__visible(el)) continue;
        const r = el.getBoundingClientRect();
        // Inline links inside a paragraph are not tap targets in the button sense.
        if (el.tagName === 'A' && el.closest('p,li')) continue;

        // Checkboxes and radios are tapped via their label, which is normally
        // far bigger than the 15px box itself. Judge the label, or skip.
        const t = (el.getAttribute('type') || '').toLowerCase();
        if (el.tagName === 'INPUT' && (t === 'checkbox' || t === 'radio')) {
          const lab = el.closest('label') ||
                      (el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null);
          if (lab) continue;
        }

        // BOTH dimensions must be small. The first live run flagged the site
        // logo at 280x30: wide, obvious, and trivially tappable. A thumb needs
        // enough area, not a square, so a generous strip is fine and a tiny
        // icon is not. A sliver under 20px in either direction still counts.
        const small = (r.width < minPx && r.height < minPx) ||
                      Math.min(r.width, r.height) < 20;
        if (small) {
          out.push({sel: __sel(el), w: Math.round(r.width), h: Math.round(r.height),
                    text: (el.textContent || '').trim().slice(0, 40)});
        }
        if (out.length >= 8) break;
      }
      return out;
    }""", MIN_TAP_TARGET_PX)
    return [_f("tap_target_small", url, viewport,
               f"tap target is {r.get('w')}x{r.get('h')}px, under {MIN_TAP_TARGET_PX}px",
               Evidence(selector=r.get("sel"),
                        numbers={"width_px": r.get("w", 0), "height_px": r.get("h", 0),
                                 "minimum_px": MIN_TAP_TARGET_PX},
                        note=r.get("text", "")))
            for r in (rows or [])[:MAX_PER_CHECK]]


ALL_LAYOUT_CHECKS = (
    check_horizontal_scroll,
    check_text_overflow,
    check_element_overlap,
    check_clipped_content,
    check_tap_targets,
)
