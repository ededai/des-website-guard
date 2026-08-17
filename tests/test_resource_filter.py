"""Tests for the 2026-08-17 console-vs-network split.

Post-mortem: for five days Des reported "Console Errors (7 URLs)" on TRW from
the console line "[error] Failed to load resource: the server responded with a
status of 404 ()". A 10-agent investigation found NO broken resource — the
line was a misread of third-party beacon noise (the GA4 /g/collect POST logs
net::ERR_ABORTED on page teardown while actually returning 204), and Chrome's
console text carries no URL, so the finding could never be verified — which is
why it was "fixed" once and reopened as flaky.

The fix: resource failures are judged only from the network log, first-party
only, always with the failing URL; console capture keeps genuine JS errors.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.run import _is_resource_msg, first_party_failures  # noqa: E402
from checks.visual import check_resource_failures  # noqa: E402

PAGE = "https://therightworkshop.com/brands/peugeot/"


def test_resource_console_lines_are_filtered_from_js_capture():
    assert _is_resource_msg("Failed to load resource: the server responded with a status of 404 ()")
    assert _is_resource_msg("Failed to load resource: net::ERR_ABORTED")
    assert _is_resource_msg("net::ERR_NAME_NOT_RESOLVED")


def test_genuine_js_errors_still_pass_the_filter():
    assert not _is_resource_msg("Uncaught TypeError: Cannot read properties of null (reading 'addEventListener')")
    assert not _is_resource_msg("Uncaught ReferenceError: trwMobileNav is not defined")


def test_first_party_failures_keeps_site_and_photon_drops_beacons():
    failures = [
        ("https://therightworkshop.com/wp-content/uploads/gone.jpg", 404),
        ("https://i0.wp.com/therightworkshop.com/img.jpg?resize=600", 404),
        ("https://www.google-analytics.com/g/collect?v=2", 404),
        ("https://maps.googleapis.com/maps/vt?pb=abc", 404),
        ("https://pixel.wp.com/g.gif?blog=1", 404),
        ("https://cdn.therightworkshop.com/asset.js", 500),
    ]
    kept = first_party_failures(failures, PAGE)
    assert kept == [
        ("https://therightworkshop.com/wp-content/uploads/gone.jpg", 404),
        ("https://i0.wp.com/therightworkshop.com/img.jpg?resize=600", 404),
        ("https://cdn.therightworkshop.com/asset.js", 500),
    ]


def test_first_party_failures_dedupes_and_handles_empty():
    dup = ("https://therightworkshop.com/x.css", 404)
    assert first_party_failures([dup, dup], PAGE) == [dup]
    assert first_party_failures([], PAGE) == []


def test_check_resource_failures_reports_url_and_status():
    f = check_resource_failures([("https://therightworkshop.com/x.css", 404)])
    assert f["check"] == "resource_404" and f["severity"] == "high"
    assert f["details"] == ["404 https://therightworkshop.com/x.css"]


def test_check_resource_failures_none_when_clean():
    assert check_resource_failures([]) is None


if __name__ == "__main__":
    test_resource_console_lines_are_filtered_from_js_capture()
    test_genuine_js_errors_still_pass_the_filter()
    test_first_party_failures_keeps_site_and_photon_drops_beacons()
    test_first_party_failures_dedupes_and_handles_empty()
    test_check_resource_failures_reports_url_and_status()
    test_check_resource_failures_none_when_clean()
    print("all resource-filter tests passed")
