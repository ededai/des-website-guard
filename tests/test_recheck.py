"""
Unit tests for checks/recheck.py pure-HTML producers + the registry/HARNESS
invariant. No network, no browser. Runs under pytest OR as a plain script:

    ./.venv/bin/python -m pytest tests/test_recheck.py      # if pytest present
    ./.venv/bin/python tests/test_recheck.py                # plain assert runner
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from checks import recheck
from reporters import bug_log


# --- meta_desc_css_leak -----------------------------------------------------
def test_meta_leak_comment():
    html = '<html><head><meta name="description" content="/* hero styles */"></head></html>'
    assert recheck.meta_desc_css_leak(html) is not None

def test_meta_leak_braces():
    html = '<html><head><meta name="description" content=".hero { padding: 0 }"></head></html>'
    assert recheck.meta_desc_css_leak(html) is not None

def test_meta_leak_clean():
    html = '<html><head><meta name="description" content="Engine repair guides for Singapore drivers."></head></html>'
    assert recheck.meta_desc_css_leak(html) is None

def test_meta_leak_missing():
    assert recheck.meta_desc_css_leak("<html><head></head></html>") is None


# --- footer_signature -------------------------------------------------------
def test_footer_signature_identical():
    a = '<footer><a href="/services/">S</a><a href="/about/">A</a></footer>'
    b = '<footer><a href="https://x.com/about/">A</a><a href="/services/">S</a></footer>'
    assert recheck.footer_signature(a) == recheck.footer_signature(b)

def test_footer_signature_variant():
    a = '<footer><a href="/services/">S</a><a href="/about/">A</a></footer>'
    c = '<footer><a href="/services/">S</a></footer>'  # missing /about/
    assert recheck.footer_signature(a) != recheck.footer_signature(c)

def test_footer_signature_missing():
    assert recheck.footer_signature("<div>no footer</div>") == ("__no_footer__",)


# --- sunday_closing_times (closing = last time in the Sun window) -----------
def test_sunday_single_closing():
    # opening 10am must NOT count; closing is 2:30pm
    assert recheck.sunday_closing_times("<p>Sun, 10am to 2:30pm* by appointment</p>") == {"2:30pm"}

def test_sunday_after_phrasing():
    assert recheck.sunday_closing_times("<p>Sundays after 2:30pm are by appointment only.</p>") == {"2:30pm"}

def test_sunday_contradiction_union():
    times = recheck.sunday_closing_times("<p>Sun 10am to 2pm</p>") | recheck.sunday_closing_times("<p>Sun 10am to 2:30pm</p>")
    assert times == {"2pm", "2:30pm"}
    assert len(times) > 1  # would fire

def test_sunday_no_time():
    assert recheck.sunday_closing_times("<p>Sunday parking is along the street.</p>") == set()


# --- vet_report_violation (banned-phrase regexes) ---------------------------
def test_vet_sent_automatically():
    assert recheck.vet_report_violation("Reports are sent automatically after each session.")

def test_vet_report_after_block():
    assert recheck.vet_report_violation("You get a report after each block of sessions.")

def test_vet_structured_report_not_on_request():
    assert recheck.vet_report_violation("Your pet receives a structured report following rehab.")

def test_vet_on_request_ok():
    assert recheck.vet_report_violation("A structured report is available on request.") is None

def test_vet_structured_report_on_request_ok():
    # negative lookahead: "structured report on request" must NOT match
    assert recheck.vet_report_violation("Owner receives a structured report on request only.") is None


# --- autop_script_wrap ------------------------------------------------------
def test_autop_positive():
    assert recheck.autop_script_wrap('<p><script>var a=1;</script></p>') is True

def test_autop_negative():
    assert recheck.autop_script_wrap('<div><script>var a=1;</script></div>') is False


# --- footer_has_endash ------------------------------------------------------
def test_footer_endash_positive():
    assert recheck.footer_has_endash('<footer>Tue–Sat 10am–6pm</footer>') is True

def test_footer_endash_plain_hyphen():
    assert recheck.footer_has_endash('<footer>Tue-Sat 10am-6pm</footer>') is False

def test_footer_endash_no_footer():
    assert recheck.footer_has_endash('<div>Tue–Sat</div>') is False


# --- header_marker_absent ---------------------------------------------------
def test_header_marker_present():
    assert recheck.header_marker_absent("<style>.aura-chrome-polish-css{}</style>", "aura-chrome-polish-css") is False

def test_header_marker_missing():
    assert recheck.header_marker_absent("<html>nope</html>", "aura-chrome-polish-css") is True

def test_header_marker_disabled():
    assert recheck.header_marker_absent("<html>x</html>", None) is False


# --- expand_phantom_targets -------------------------------------------------
def test_expand_phantom_pipe():
    ev = "404 targets: /topics/a|b|c/, /about-us/, /services/x/. 20 linking pages total."
    got = recheck.expand_phantom_targets(ev)
    assert "/topics/a/" in got and "/topics/b/" in got and "/topics/c/" in got
    assert "/about-us/" in got and "/services/x/" in got

def test_expand_phantom_cap():
    ev = "/topics/" + "|".join(f"t{i}" for i in range(20)) + "/"
    assert len(recheck.expand_phantom_targets(ev, cap=10)) == 10


# --- registry / HARNESS invariant (the false-mass-close guard) --------------
def test_registry_subset_of_harness():
    # Every recheck id MUST be a harness id (a producer emits it), else reconcile
    # can never close it. A harness id without a producer would false mass-close.
    missing = set(recheck.REGISTRY) - bug_log.HARNESS_CHECK_IDS
    assert not missing, f"registry ids not in HARNESS_CHECK_IDS: {missing}"

def test_non_automatable_excluded():
    for cid in ("footer_map_black_box", "carplate_black_box_masking", "slow_lcp", "archive_count_mismatch"):
        assert cid not in recheck.REGISTRY
        assert cid not in bug_log.HARNESS_CHECK_IDS


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        fn()
        passed += 1
        print(f"  ok  {name}")
    print(f"\n{passed}/{len(fns)} tests passed")
