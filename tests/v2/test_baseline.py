"""The loss-only model is the heart of Des v2, so it is tested hardest.

Two guarantees matter more than any single check:
  1. It NEVER flags Ed's own publishing. He ships constantly; a guard that
     nags about new content gets switched off, and a switched-off guard is
     worth nothing.
  2. It DOES catch things quietly going missing, with numbers attached so the
     finding can prove itself.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2.baseline import (accept, capture, diff, load_baseline, save_baseline,
                           sanitise_url)
from des2.models import Fingerprint

URL = "https://therightworkshop.com/brands/peugeot/"
VP = "desktop"


def _healthy(**over):
    d = dict(has_nav=True, has_footer=True, h1_count=1, text_len=4200,
             images_total=8, images_ok=8, internal_links=30, sections=6,
             standards={"meta_description": True, "title": True, "canonical": True,
                        "h1": True, "byline": True, "address_unit": True})
    d.update(over)
    return capture(d)


# ----------------------------------------------------------- the anti-nag rule
def test_first_sight_produces_nothing():
    """No baseline means nothing to compare. Silence, not speculation."""
    assert diff(None, _healthy(), URL, VP) == []


def test_publishing_more_content_is_silent():
    """The guarantee that keeps Des switched on."""
    before = _healthy()
    after = _healthy(text_len=9000, internal_links=55, sections=11,
                     images_total=14, images_ok=14)
    assert diff(before, after, URL, VP) == []


def test_unchanged_page_is_silent():
    assert diff(_healthy(), _healthy(), URL, VP) == []


def test_standard_absent_all_along_stays_silent():
    """Never had a byline is backlog for Bryan, not an alarm for Ed."""
    no_byline = {"meta_description": True, "byline": False}
    assert diff(capture(dict(has_nav=True, standards=no_byline)),
                capture(dict(has_nav=True, standards=no_byline)), URL, VP) == []


# ----------------------------------------------------------- real regressions
def test_nav_disappearing_is_caught():
    out = diff(_healthy(), _healthy(has_nav=False), URL, VP)
    assert [f.check for f in out] == ["missing_nav"]
    assert out[0].kind == "breakage" and out[0].evidence.is_hard()


def test_footer_disappearing_is_caught():
    out = diff(_healthy(), _healthy(has_footer=False), URL, VP)
    assert [f.check for f in out] == ["missing_footer"]


def test_last_h1_disappearing_is_caught():
    out = diff(_healthy(), _healthy(h1_count=0), URL, VP)
    assert any(f.check == "lost_h1" for f in out)


def test_body_collapse_reports_the_drop():
    out = diff(_healthy(), _healthy(text_len=800), URL, VP)
    hit = [f for f in out if f.check == "lost_body_content"]
    assert hit, "an 81% body collapse must be caught"
    assert hit[0].evidence.numbers, "must carry the numbers that prove it"


def test_images_breaking_is_caught():
    """Same images intended, fewer loading: that is breakage, not editing."""
    out = diff(_healthy(), _healthy(images_ok=3), URL, VP)
    assert any(f.check == "broken_images" for f in out)


def test_images_removed_on_purpose_is_not_breakage():
    out = diff(_healthy(), _healthy(images_total=0, images_ok=0), URL, VP)
    assert not any(f.check == "broken_images" for f in out)


def test_standard_lost_fires_and_routes_to_bryan():
    before = _healthy()
    after = _healthy(standards={"meta_description": False, "title": True,
                                "canonical": True, "h1": True, "byline": True,
                                "address_unit": True})
    out = diff(before, after, URL, VP)
    hit = [f for f in out if f.check == "lost_meta_description"]
    assert hit and hit[0].kind == "standard_lost" and hit[0].owner == "bryan"


def test_tiny_pages_do_not_trigger_percentage_noise():
    """A 30-char stub going to 10 is 67% and means nothing."""
    small = capture(dict(has_nav=True, text_len=30, sections=1, internal_links=1))
    smaller = capture(dict(has_nav=True, text_len=10, sections=0, internal_links=0))
    assert diff(small, smaller, URL, VP) == []


# ----------------------------------------------------------- contract & storage
def test_every_finding_can_prove_itself():
    """No finding may exist that could not survive the evidence gate."""
    out = diff(_healthy(), _healthy(has_nav=False, has_footer=False, h1_count=0,
                                    text_len=100, images_ok=0), URL, VP)
    assert out, "sanity: this should produce findings"
    for f in out:
        assert f.evidence.is_hard(), f"{f.check} cannot prove itself"
        assert f.reproduced is False, "only the verify pass may set reproduced"


def test_baseline_roundtrip(tmp_path):
    fp = _healthy()
    save_baseline(URL, VP, fp, root=tmp_path)
    assert load_baseline(URL, VP, root=tmp_path) == fp


def test_missing_baseline_returns_none(tmp_path):
    assert load_baseline(URL, VP, root=tmp_path) is None


def test_corrupt_baseline_returns_none_instead_of_raising(tmp_path):
    """A guard that crashes on its own state file is worse than one that rebuilds it."""
    save_baseline(URL, VP, _healthy(), root=tmp_path)
    p = next(tmp_path.glob("*.json"))
    p.write_text("{ not json at all")
    assert load_baseline(URL, VP, root=tmp_path) is None


def test_baseline_from_older_version_missing_keys(tmp_path):
    save_baseline(URL, VP, _healthy(), root=tmp_path)
    p = next(tmp_path.glob("*.json"))
    p.write_text(json.dumps({"has_nav": True}))
    fp = load_baseline(URL, VP, root=tmp_path)
    assert fp is not None and fp.has_nav is True


def test_accept_rebaselines(tmp_path):
    save_baseline(URL, VP, _healthy(), root=tmp_path)
    accept(URL, VP, _healthy(text_len=9000), root=tmp_path)
    assert load_baseline(URL, VP, root=tmp_path).text_len == 9000


def test_url_sanitising_cannot_escape_the_baseline_directory(tmp_path):
    """The real property is containment, not the absence of dots.

    A filename may contain ".." harmlessly; traversal needs a separator. So
    assert what actually matters: whatever URL arrives, the file it resolves to
    stays inside the baseline root.
    """
    nasty = ["https://x.com/a/../../etc/passwd", "https://x.com/../../../root/.ssh/id_rsa",
             "https://x.com/" + "a" * 400, "https://x.com/%2e%2e%2f%2e%2e%2f"]
    root = tmp_path.resolve()
    for u in nasty:
        stem = sanitise_url(u)
        assert os.sep not in stem and "/" not in stem
        resolved = (root / f"{stem}__desktop.json").resolve()
        assert str(resolved).startswith(str(root) + os.sep), f"{u} escaped the root"


def test_url_sanitising_is_stable_and_distinct():
    assert sanitise_url("https://x.com/a/") != sanitise_url("https://x.com/b/")
    assert sanitise_url(URL) == sanitise_url(URL), "must be stable across calls"
    assert len(sanitise_url("https://x.com/" + "a" * 400)) < 200, "must stay a legal filename"


def test_viewports_do_not_share_a_baseline(tmp_path):
    save_baseline(URL, "desktop", _healthy(text_len=4200), root=tmp_path)
    save_baseline(URL, "phone", _healthy(text_len=1200), root=tmp_path)
    assert load_baseline(URL, "desktop", root=tmp_path).text_len == 4200
    assert load_baseline(URL, "phone", root=tmp_path).text_len == 1200
