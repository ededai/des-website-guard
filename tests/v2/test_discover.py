"""URL selection: small on purpose, and honest about what it left out.

The daily set is the cost-control decision of the whole guard, so these tests
pin the two things that make it trustworthy: template pages are ALWAYS present
(site-wide chrome breaks every page without editing any of them), and
truncation is reported rather than silent.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2.config import load_site
from des2.discover import all_urls, changed_urls, daily_set, template_urls

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 25, 9, 0, tzinfo=SGT)

CFG = {
    "name": "TEST",
    "base_url": "https://x.com",
    "sitemap_url": "https://x.com/sitemap_index.xml",
    "wp_api_base": "https://x.com/wp-json/wp/v2",
    "templates": [{"name": "home", "url": "https://x.com/"},
                  {"name": "svc", "url": "https://x.com/services/a/"}],
    "skip_patterns": [r"/feed/?$", r"/page/\d+/?$"],
}


def _wp(pages):
    """Fake WP: maps a substring of the query to a JSON payload."""
    def fetch(url):
        for needle, payload in pages.items():
            if needle in url:
                return json.dumps(payload)
        return json.dumps([])
    return fetch


# ---------------------------------------------------------------- changed
def test_changed_urls_queries_modified_after_window():
    seen = {}
    def fetch(url):
        seen["url"] = url
        return json.dumps([{"link": "https://x.com/edited/"}])
    out = changed_urls(CFG, since_hours=48, now=NOW, fetch=fetch)
    assert "modified_after=2026-08-23T01" in seen["url"], seen["url"]
    assert "_fields=link" in seen["url"], "must ask for a tiny payload"
    assert "https://x.com/edited/" in out


def _page_of(url):
    """Parse the real `page` param. Substring matching is a trap here:
    'page=1' also appears inside 'per_page=100'."""
    import urllib.parse as up
    return int(up.parse_qs(up.urlparse(url).query).get("page", ["1"])[0])


def test_changed_urls_follows_pagination():
    def fetch(url):
        if "/posts" in url and _page_of(url) == 1:
            return json.dumps([{"link": f"https://x.com/p{i}/"} for i in range(100)])
        if "/posts" in url and _page_of(url) == 2:
            return json.dumps([{"link": "https://x.com/last/"}])
        return json.dumps([])
    out = changed_urls(CFG, now=NOW, fetch=fetch)
    assert "https://x.com/last/" in out and len(out) == 101


def test_changed_urls_returns_empty_on_api_failure():
    """Degrade, never die: a sweep of fewer pages still has value."""
    def fetch(url):
        raise RuntimeError("wp is down")
    assert changed_urls(CFG, now=NOW, fetch=fetch) == []


def test_changed_urls_survives_garbage_payload():
    assert changed_urls(CFG, now=NOW, fetch=lambda u: "<html>not json</html>") == []


def test_changed_urls_applies_skip_patterns():
    fetch = _wp({"posts": [{"link": "https://x.com/good/"},
                           {"link": "https://x.com/good/feed/"},
                           {"link": "https://x.com/good/page/2/"}]})
    assert changed_urls(CFG, now=NOW, fetch=fetch) == ["https://x.com/good/"]


def test_no_wp_api_means_no_changed_pages():
    cfg = dict(CFG, wp_api_base=None)
    assert changed_urls(cfg, now=NOW, fetch=lambda u: "boom") == []


# ---------------------------------------------------------------- sitemap
def test_all_urls_follows_a_sitemap_index():
    def fetch(url):
        if "sitemap_index" in url:
            return ("<sitemapindex><sitemap><loc>https://x.com/s1.xml</loc></sitemap>"
                    "<sitemap><loc>https://x.com/s2.xml</loc></sitemap></sitemapindex>")
        if "s1" in url:
            return "<urlset><url><loc>https://x.com/a/</loc></url></urlset>"
        return "<urlset><url><loc>https://x.com/b/</loc></url></urlset>"
    assert all_urls(CFG, fetch=fetch) == ["https://x.com/a/", "https://x.com/b/"]


def test_all_urls_handles_a_flat_sitemap():
    def fetch(url):
        return ("<urlset><url><loc>https://x.com/a/</loc></url>"
                "<url><loc>https://x.com/a/feed/</loc></url></urlset>")
    assert all_urls(CFG, fetch=fetch) == ["https://x.com/a/"]


def test_all_urls_dedupes():
    def fetch(url):
        return ("<urlset><url><loc>https://x.com/a/</loc></url>"
                "<url><loc>https://x.com/a/</loc></url></urlset>")
    assert all_urls(CFG, fetch=fetch) == ["https://x.com/a/"]


def test_all_urls_empty_when_sitemap_unreachable():
    def fetch(url):
        raise RuntimeError("404")
    assert all_urls(CFG, fetch=fetch) == []


def test_one_broken_child_sitemap_does_not_lose_the_rest():
    def fetch(url):
        if "sitemap_index" in url:
            return ("<sitemapindex><sitemap><loc>https://x.com/dead.xml</loc></sitemap>"
                    "<sitemap><loc>https://x.com/ok.xml</loc></sitemap></sitemapindex>")
        if "dead" in url:
            raise RuntimeError("gone")
        return "<urlset><url><loc>https://x.com/ok/</loc></url></urlset>"
    assert all_urls(CFG, fetch=fetch) == ["https://x.com/ok/"]


# ---------------------------------------------------------------- daily set
def test_daily_set_puts_templates_first():
    """If a run is cut short it must already have covered the page types."""
    fetch = _wp({"posts": [{"link": "https://x.com/edited/"}]})
    urls, dropped = daily_set(CFG, now=NOW, fetch=fetch)
    assert urls[0] == "https://x.com/" and urls[1] == "https://x.com/services/a/"
    assert "https://x.com/edited/" in urls and dropped == 0


def test_daily_set_dedupes_template_that_was_also_edited():
    fetch = _wp({"posts": [{"link": "https://x.com/"}]})
    urls, _ = daily_set(CFG, now=NOW, fetch=fetch)
    assert urls.count("https://x.com/") == 1


def test_daily_set_reports_what_it_dropped():
    """Silent truncation would let the guard claim more coverage than it had."""
    fetch = _wp({"posts": [{"link": f"https://x.com/p{i}/"} for i in range(50)]})
    urls, dropped = daily_set(CFG, cap=10, now=NOW, fetch=fetch)
    assert len(urls) == 10 and dropped == 42
    assert urls[0] == "https://x.com/", "templates survive the cap"


def test_daily_set_still_works_when_wordpress_is_down():
    def fetch(url):
        raise RuntimeError("down")
    urls, dropped = daily_set(CFG, now=NOW, fetch=fetch)
    assert urls == template_urls(CFG) and dropped == 0


# ---------------------------------------------------------------- real config
def test_real_trw_config_is_valid_and_complete():
    cfg = load_site("trw")
    assert cfg["name"] == "TRW"
    assert len(cfg["templates"]) == 8, "one page per template type"
    assert all(t["url"].startswith("https://therightworkshop.com") for t in cfg["templates"])
    # Both chrome generations must be accepted, or a selector rename reads as a
    # broken menu across the whole site (2026-05 incident).
    burger = cfg["mobile_nav_toggle_selector"]
    assert "trwBurger" in burger and "nav-hamburger" in burger
    panel = cfg["mobile_nav_panel_selector"]
    assert "trwMmenu" in panel and "mobile-nav" in panel


def test_real_trw_templates_are_unique():
    cfg = load_site("trw")
    urls = [t["url"] for t in cfg["templates"]]
    assert len(set(urls)) == len(urls)
