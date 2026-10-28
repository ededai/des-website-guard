"""The sitemap probe: the one check that watches the guard's own map.

Pinned after 5 to 28 Oct 2026, when sitemap_index.xml returned the 404 page
for 23 days while every sweep stayed green. Discovery had degraded to
templates plus recent edits without a word, and Google never found October's
pages. These tests make sure that can only happen once.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import checks_break as cb
from des2 import run
from des2.models import owner_for

SM = "https://x.com/sitemap_index.xml"
INDEX = ('<?xml version="1.0" encoding="UTF-8"?><?xml-stylesheet type="text/xsl" href="//x.com/s.xsl"?>'
         '<sitemapindex><sitemap><loc>https://x.com/a.xml</loc></sitemap></sitemapindex>')
HTML404 = "<!doctype html>\n<html lang=\"en-US\"><head><title>Page not found - X</title></head></html>"


# ------------------------------------------------------------ the pure check
def test_sitemap_404_is_a_finding_that_names_the_status():
    out = cb.check_sitemap(404, HTML404, SM)
    assert len(out) == 1
    f = out[0]
    assert f.check == "sitemap_unreachable" and f.kind == "breakage"
    assert f.evidence.resource == SM and f.evidence.status == 404
    assert f.evidence.is_hard(), "must be able to prove itself"


def test_sitemap_html_with_200_is_still_a_finding():
    """A cached error page can answer 200. The body decides, not the status."""
    out = cb.check_sitemap(200, HTML404, SM)
    assert len(out) == 1 and out[0].evidence.status == 200
    assert "Page not found" in out[0].evidence.note


def test_healthy_sitemap_index_is_silent():
    assert cb.check_sitemap(200, INDEX, SM) == []


def test_healthy_flat_sitemap_is_silent():
    assert cb.check_sitemap(200, "<urlset><url><loc>https://x.com/</loc></url></urlset>", SM) == []


def test_transport_failure_is_not_the_sites_fault():
    """None means the guard could not reach the site; that is not a finding."""
    assert cb.check_sitemap(None, "", SM) == []


def test_no_sitemap_url_means_nothing_to_judge():
    assert cb.check_sitemap(404, HTML404, "") == []


def test_owner_is_codi():
    assert owner_for("sitemap_unreachable") == "codi"


# ------------------------------------------------------------ the probe
def _probe(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(run, "fetch_status", lambda url: next(answers))

    async def no_pause(scale=1.0):
        return None
    monkeypatch.setattr(run.fetch, "polite_pause", no_pause)
    return asyncio.run(run.probe_sitemap({"sitemap_url": SM}))


def test_probe_alerts_when_the_second_look_agrees(monkeypatch):
    found, probed = _probe(monkeypatch, [(404, HTML404), (404, HTML404)])
    assert probed and len(found) == 1
    assert found[0].reproduced and found[0].alertable()


def test_probe_holds_a_blip(monkeypatch):
    """One bad answer followed by a good one is logged, never sent."""
    found, probed = _probe(monkeypatch, [(404, HTML404), (200, INDEX)])
    assert probed and len(found) == 1
    assert found[0].reproduced is False and not found[0].alertable()


def test_probe_is_silent_on_a_healthy_sitemap(monkeypatch):
    assert _probe(monkeypatch, [(200, INDEX)]) == ([], True)


def test_probe_reports_not_probed_when_unreachable(monkeypatch):
    assert _probe(monkeypatch, [(None, "")]) == ([], False)


def test_probe_without_sitemap_url_does_nothing():
    assert asyncio.run(run.probe_sitemap({})) == ([], False)
