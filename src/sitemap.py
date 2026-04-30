import re
from urllib.request import urlopen, Request
from urllib.parse import urljoin

import requests


def fetch(url, timeout=20):
    req = Request(url, headers={"User-Agent": "DesWebsiteGuard/1.0 (+https://therightworkshop.com)"})
    return urlopen(req, timeout=timeout).read().decode("utf-8", errors="ignore")


def discover_urls(sitemap_url):
    """Walk a sitemap (incl. sitemap-index) and return the de-duped list of page URLs."""
    seen, queue, out = set(), [sitemap_url], []
    while queue:
        s = queue.pop(0)
        if s in seen:
            continue
        seen.add(s)
        try:
            xml = fetch(s)
        except Exception:
            continue
        # nested sitemaps
        for m in re.finditer(r"<sitemap>\s*<loc>([^<]+)</loc>", xml, re.I):
            queue.append(m.group(1).strip())
        # actual urls
        for m in re.finditer(r"<url>\s*<loc>([^<]+)</loc>", xml, re.I):
            out.append(m.group(1).strip())
    return sorted(set(out))


def filter_skip(urls, skip_patterns):
    out = []
    for u in urls:
        if any(p in u for p in skip_patterns):
            continue
        out.append(u)
    return out
