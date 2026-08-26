"""Gates decide whether findings may be believed at all."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import gates
from des2.models import Evidence, Finding

URL = "https://therightworkshop.com/a/"


def _f(check, url, vp="desktop"):
    return Finding(check=check, kind="breakage", url=url, viewport=vp,
                   summary="x", evidence=Evidence(selector="body"))


# ------------------------------------------------------------- crawl health
def test_ok_page_passes():
    verdict, _ = gates.crawl_health(200, "<html><body>real page</body></html>")
    assert verdict == "ok"


def test_challenge_status_is_blocked_not_a_site_fault():
    """403 under a sweep is usually us being rate limited, not the site failing."""
    verdict, ev = gates.crawl_health(403, "<html>checking your browser</html>")
    assert verdict == "blocked" and ev


def test_server_error_is_its_own_verdict():
    assert gates.crawl_health(500, "<html></html>")[0] == "server_error"


def test_dead_page_is_its_own_verdict():
    assert gates.crawl_health(404, "<html></html>")[0] == "dead"


def test_blocked_page_yields_one_calm_finding():
    f = gates.blocked_finding(URL, "desktop", "HTTP 403 (bot challenge)")
    assert f.check == "crawl_blocked" and f.evidence.note


# ------------------------------------------------------------- mass finding
def test_check_firing_across_the_site_collapses_to_one_doubt():
    """159 pages reporting 'no drawer' is evidence about the CHECK."""
    findings = [_f("mobile_menu_dead", f"https://x.com/p{i}/") for i in range(9)]
    out = gates.mass_finding_gate(findings, pages_swept=10)
    assert len(out) == 1
    assert out[0].check == "suspected_checker_defect"
    assert out[0].evidence.numbers["pages_hit"] == 9


def test_a_genuine_handful_survives_intact():
    findings = [_f("resource_404", f"https://x.com/p{i}/") for i in range(2)]
    out = gates.mass_finding_gate(findings, pages_swept=10)
    assert len(out) == 2 and all(f.check == "resource_404" for f in out)


def test_gate_stays_out_of_the_way_on_tiny_sweeps():
    """With 3 pages swept, 'most of them' means nothing."""
    findings = [_f("missing_nav", f"https://x.com/p{i}/") for i in range(3)]
    out = gates.mass_finding_gate(findings, pages_swept=3)
    assert len(out) == 3


def test_one_check_collapsing_does_not_hide_another():
    mass = [_f("mobile_menu_dead", f"https://x.com/p{i}/") for i in range(9)]
    real = [_f("resource_404", "https://x.com/p1/")]
    out = gates.mass_finding_gate(mass + real, pages_swept=10)
    checks = {f.check for f in out}
    assert "suspected_checker_defect" in checks and "resource_404" in checks


# ------------------------------------------------------------- sweep abort
def test_mostly_blocked_sweep_aborts():
    should, reason = gates.sweep_abort(blocked_pages=5, total_pages=20)
    assert should is True and "cannot be trusted" in reason


def test_a_couple_of_blocks_do_not_abort():
    assert gates.sweep_abort(blocked_pages=1, total_pages=20)[0] is False


def test_small_sweeps_never_abort():
    assert gates.sweep_abort(blocked_pages=3, total_pages=5)[0] is False


def test_zero_pages_is_not_a_division_error():
    assert gates.sweep_abort(0, 0)[0] is False
