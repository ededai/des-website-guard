"""Which URLs Des looks at, and why that set is small on purpose.

The daily sweep is bounded BY DESIGN, not by the size of the sitemap: TRW is
about 165 URLs and AURA about 73, and checking all of them every day is the
burn pattern that helped push the account into a spending-limit block.

Two sources, and both are needed:
  CHANGED pages, because regressions follow changes. WordPress can tell us
  exactly what moved, for the price of one small query.
  TEMPLATE pages, one per page type, because site-wide chrome is injected by a
  snippet. It can break every page at once while WordPress reports that
  nothing was edited, so a changed-pages-only sweep would see a healthy site.

Everything here degrades instead of dying. A failed API call returns an empty
list, never an exception: a sweep that checks fewer pages is useful, a sweep
that crashes is not.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

SGT = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) TRW-Des/2.0"}
WP_PER_PAGE = 100
WP_MAX_PAGES = 10          # 1000 edits in the window means something else is wrong


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _skip(url: str, patterns: list[str]) -> bool:
    for p in patterns or []:
        try:
            if re.search(p, url):
                return True
        except re.error:
            continue      # a bad pattern must not take the sweep down
    return False


def changed_urls(site_cfg: dict, since_hours: int = 48,
                 now: Optional[datetime] = None,
                 fetch: Callable[[str], str] = _fetch) -> list[str]:
    """Pages and posts edited recently, straight from WordPress."""
    api = site_cfg.get("wp_api_base")
    if not api:
        return []
    now = now or datetime.now(SGT)
    since = (now - timedelta(hours=since_hours)).astimezone(timezone.utc)
    stamp = since.strftime("%Y-%m-%dT%H:%M:%S")
    out: list[str] = []
    for kind in ("posts", "pages"):
        for page in range(1, WP_MAX_PAGES + 1):
            q = urllib.parse.urlencode({
                "modified_after": stamp, "per_page": WP_PER_PAGE,
                "page": page, "_fields": "link", "status": "publish",
            })
            try:
                raw = fetch(f"{api.rstrip('/')}/{kind}?{q}")
                items = json.loads(raw)
            except Exception:
                break     # degrade: this kind contributes nothing this run
            if not isinstance(items, list) or not items:
                break
            out.extend(i["link"] for i in items if isinstance(i, dict) and i.get("link"))
            if len(items) < WP_PER_PAGE:
                break
    pats = site_cfg.get("skip_patterns") or []
    return [u for u in out if not _skip(u, pats)]


def template_urls(site_cfg: dict) -> list[str]:
    """One representative page per template. Never skip these."""
    return [t["url"] for t in (site_cfg.get("templates") or [])
            if isinstance(t, dict) and t.get("url")]


def all_urls(site_cfg: dict, fetch: Callable[[str], str] = _fetch) -> list[str]:
    """Full sitemap for the weekly sweep, following a sitemap index."""
    root = site_cfg.get("sitemap_url")
    if not root:
        return []
    try:
        xml = fetch(root)
    except Exception:
        return []
    children = re.findall(r"<sitemap>.*?<loc>(.*?)</loc>.*?</sitemap>", xml, re.S)
    locs: list[str] = []
    if children:
        for child in children:
            try:
                locs.extend(re.findall(r"<url>.*?<loc>(.*?)</loc>.*?</url>",
                                       fetch(child.strip()), re.S))
            except Exception:
                continue
    else:
        locs = re.findall(r"<url>.*?<loc>(.*?)</loc>.*?</url>", xml, re.S)
    pats = site_cfg.get("skip_patterns") or []
    seen, out = set(), []
    for u in (l.strip() for l in locs):
        if u and u not in seen and not _skip(u, pats):
            seen.add(u)
            out.append(u)
    return out


# A runaway guard, not a budget. If a sitemap ever returns tens of thousands
# of URLs something is wrong with the sitemap, not with our appetite.
SAFETY_CAP = 2000


def daily_set(site_cfg: dict, since_hours: int = 48, cap: int = SAFETY_CAP,
              now: Optional[datetime] = None,
              fetch: Callable[[str], str] = _fetch) -> tuple[list[str], int]:
    """(urls, dropped). THE WHOLE SITE, ordered by value.

    Corrected 2026-08-27. This used to check only recently-edited pages plus a
    template sample, capped at 40, to save GitHub Actions minutes. That was the
    wrong constraint borrowed from the wrong repo: Cole is private and metered,
    this one is public, where minutes are free and unmetered. Bounding coverage
    bought nothing and cost the ability to notice a quiet break on a page
    nobody had edited.

    Ordering still matters, because a run can be cut short: template pages
    first (they reveal site-wide chrome breakage), then recently edited pages
    (regressions follow changes), then everything else.
    """
    templates = template_urls(site_cfg)
    changed = changed_urls(site_cfg, since_hours, now, fetch)
    everything = all_urls(site_cfg, fetch)
    seen, uniq = set(), []
    for u in templates + changed + everything:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    if cap is not None and len(uniq) > cap:
        return uniq[:cap], len(uniq) - cap
    return uniq, 0
