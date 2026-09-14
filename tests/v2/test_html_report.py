"""des2/html_report.py: the visual record that stopped landing (T-3,
deep-sweep-2026-09-13.md). Just needs to write a self-contained file, name it
right, and prune itself -- everything else is presentation.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2 import html_report as hr
from des2.models import Evidence, Finding

SGT = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 14, 6, 30, tzinfo=SGT)


def _f(check="resource_404", kind="breakage", owner="codi", reproduced=True):
    return Finding(check=check, kind=kind, url="https://therightworkshop.com/about/",
                   viewport="desktop", summary=f"{check} happened",
                   evidence=Evidence(resource="https://x/y.jpg", status=404,
                                     numbers={"n": 1.0}, note="detail"),
                   owner=owner, reproduced=reproduced)


def test_report_path_is_named_by_site_timestamp_and_tier():
    p = hr.report_path("trw", "daily", NOW)
    assert p.name == "20260914-063000-daily.html"
    assert p.parent.name == "trw"


def test_build_writes_a_self_contained_html_file(tmp_path):
    out = tmp_path / "trw" / "20260914-063000-daily.html"
    findings = [_f(), _f(check="js_error", kind="breakage", owner="codi")]
    result = hr.build("trw", "daily", findings, urls_swept=165, open_count=3,
                      escalations=[{"check": "resource_404", "url": "https://x/y",
                                    "escalation_level": 1}],
                      started=NOW, out_path=out)
    assert result == out
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "<!doctype html>" in text.lower()
    assert "therightworkshop.com/about/" in text
    assert "resource_404" in text and "js_error" in text
    assert "165" in text  # urls swept
    assert "Open in the bug log: 3" in text
    assert "escalation level 1" in text
    # self-contained: no external assets pulled in by the page itself
    assert "<script src=" not in text
    assert "<link rel=\"stylesheet\"" not in text
    assert "—" not in text  # no em dashes anywhere in the template


def test_build_with_no_findings_says_so_plainly(tmp_path):
    out = tmp_path / "trw" / "x.html"
    hr.build("trw", "daily", [], urls_swept=10, started=NOW, out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "No findings this sweep." in text
    assert "None." in text  # escalations list


def test_html_is_escaped_against_injection(tmp_path):
    out = tmp_path / "trw" / "x.html"
    f = Finding(check="js_error", kind="breakage",
               url="https://x/</script><script>alert(1)</script>",
               viewport="desktop", summary="<img onerror=alert(1)>",
               evidence=Evidence(note="<b>bold</b>"), owner="codi", reproduced=True)
    hr.build("trw", "daily", [f], urls_swept=1, started=NOW, out_path=out)
    text = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "<img onerror=alert(1)>" not in text


def test_prune_keeps_only_the_newest_ten(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "REPORTS_DIR", tmp_path)
    site_dir = tmp_path / "trw"
    site_dir.mkdir()
    names = [f"202609{day:02d}-063000-daily.html" for day in range(1, 15)]  # 14 files
    for n in names:
        (site_dir / n).write_text("x")
    deleted = hr.prune("trw", keep=10)
    remaining = sorted(p.name for p in site_dir.glob("*.html"))
    assert len(remaining) == 10
    assert len(deleted) == 4
    # the newest 10 (by filename, which sorts chronologically) survive
    assert remaining == sorted(names)[-10:]


def test_prune_is_a_noop_under_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "REPORTS_DIR", tmp_path)
    site_dir = tmp_path / "trw"
    site_dir.mkdir()
    (site_dir / "20260914-063000-daily.html").write_text("x")
    assert hr.prune("trw", keep=10) == []


def test_prune_missing_site_dir_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "REPORTS_DIR", tmp_path)
    assert hr.prune("nosuchsite") == []
