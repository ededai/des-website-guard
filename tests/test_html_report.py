"""
Unit tests for reporters/html_report.py — self-contained per-sweep HTML
report + what-changed diff + retention prune. No network, no browser.

    ./.venv/bin/python -m pytest tests/test_html_report.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporters import html_report as hr


def test_report_rel_path_format():
    ts = datetime(2026, 8, 2, 3, 15, 0)
    assert hr.report_rel_path("TRW", "critical", ts) == "reports/trw/20260802-031500-critical.html"


def test_blob_url_format():
    rel = "reports/trw/20260802-031500-critical.html"
    assert hr.blob_url(rel) == "https://github.com/ededai/des-website-guard/blob/main/" + rel


def _isolate(tmp_path, monkeypatch):
    """Point ROOT / REPORTS_DIR at a scratch directory so tests never touch
    the real repo's reports/ or bug-log.jsonl."""
    monkeypatch.setattr(hr, "ROOT", tmp_path)
    monkeypatch.setattr(hr, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setenv("DES_BUG_LOG", str(tmp_path / "bug-log.jsonl"))


def test_build_embeds_base64_and_no_relative_image_path(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"\xff\xd8\xff\xe0FAKEJPEGDATA")
    finding = {
        "title": "Mobile Menu", "check_id": "mobile_menu", "severity": "high",
        "urls": ["https://example.com/a/"], "evidence": "issue: menu_no_autoclose",
        "details": [], "screenshots": [str(shot)],
    }
    sweep_started = datetime.now(timezone.utc) - timedelta(minutes=5)
    out_rel = hr.report_rel_path("TRW", "critical", sweep_started)
    path = hr.build("TRW", "critical", [finding], sweep_started, out_rel)
    content = path.read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in content
    assert '<img src="reports/' not in content


def test_build_escapes_script_tag_in_title(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    finding = {
        "title": "<script>alert(1)</script>", "check_id": "x", "severity": "low",
        "urls": [], "evidence": "", "details": [], "screenshots": [],
    }
    sweep_started = datetime.now(timezone.utc)
    out_rel = hr.report_rel_path("TRW", "critical", sweep_started)
    path = hr.build("TRW", "critical", [finding], sweep_started, out_rel)
    content = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;" in content
    assert "<script>alert(1)</script>" not in content


def test_build_what_changed_new_fixed_still_open(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sweep_started = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)
    inside = (sweep_started + timedelta(minutes=10)).isoformat()
    older = (sweep_started - timedelta(days=5)).isoformat()

    bug_log_path = tmp_path / "bug-log.jsonl"
    records = [
        {"title": "New Bug", "check_id": "new_bug", "site": "TRW", "status": "open",
         "first_seen": inside, "last_seen": inside, "fixed_at": None, "MTTR_hours": None},
        {"title": "Fixed Bug", "check_id": "fixed_bug", "site": "TRW", "status": "fixed",
         "first_seen": older, "last_seen": inside, "fixed_at": inside, "MTTR_hours": 12.5},
        {"title": "Old Bug", "check_id": "old_bug", "site": "TRW", "status": "open",
         "first_seen": older, "last_seen": inside, "fixed_at": None, "MTTR_hours": None},
    ]
    with bug_log_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    out_rel = hr.report_rel_path("TRW", "critical", sweep_started)
    path = hr.build("TRW", "critical", [], sweep_started, out_rel)
    content = path.read_text(encoding="utf-8")
    assert "NEW" in content and "New Bug" in content
    assert "FIXED" in content and "Fixed Bug" in content and "12.5" in content
    assert "STILL OPEN" in content and "Old Bug" in content


def test_prune_deletes_three_oldest_of_six(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    site_dir = tmp_path / "reports" / "trw"
    site_dir.mkdir(parents=True)
    names = [f"2026080{n}-000000-critical.html" for n in range(1, 7)]
    for name in names:
        (site_dir / name).write_text("<html></html>", encoding="utf-8")

    deleted = hr.prune("trw", keep=3)
    assert len(deleted) == 3
    deleted_names = sorted(Path(d).name for d in deleted)
    assert deleted_names == sorted(names[:3])
    remaining = sorted(p.name for p in site_dir.glob("*.html"))
    assert remaining == sorted(names[3:])
