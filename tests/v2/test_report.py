import json, os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from des2.models import Evidence, Finding
from des2 import report

SGT = timezone(timedelta(hours=8))
MON = datetime(2026, 8, 24, 9, 0, tzinfo=SGT)
TUE = datetime(2026, 8, 25, 9, 0, tzinfo=SGT)


def _f(check="resource_404", url="https://x/a", res="https://x/i.jpg"):
    return Finding(check=check, kind="breakage", url=url, viewport="desktop",
                   summary=f"{check} on {url}", owner="codi",
                   evidence=Evidence(resource=res, status=404), reproduced=True)


def test_new_finding_alerts_and_is_logged():
    log, alerts = reconcile_helper([_f()], {})
    assert len(alerts) == 1
    assert list(log.values())[0]["status"] == "open"


def reconcile_helper(findings, log, swept=None):
    return report.reconcile(findings, log, now=TUE, swept_urls=swept)


def test_same_finding_next_day_does_not_nag():
    log, _ = reconcile_helper([_f()], {})
    log2, alerts = reconcile_helper([_f()], log)
    assert alerts == [], "an already-open finding must not ping again"
    assert list(log2.values())[0]["status"] == "open"


def test_clean_sweep_marks_fixed_only_for_pages_actually_swept():
    log, _ = reconcile_helper([_f()], {})
    log2, _ = reconcile_helper([], log, swept={"https://x/a"})
    assert list(log2.values())[0]["status"] == "fixed"
    # A page we did NOT sweep must never be auto-closed.
    log3, _ = reconcile_helper([_f(url="https://x/b", res="https://x/z.jpg")], {})
    log4, _ = reconcile_helper([], log3, swept={"https://x/other"})
    assert list(log4.values())[0]["status"] == "open"


def test_regression_returning_reopens_and_alerts():
    log, _ = reconcile_helper([_f()], {})
    log, _ = reconcile_helper([], log, swept={"https://x/a"})
    log, alerts = reconcile_helper([_f()], log)
    assert len(alerts) == 1, "a regression coming back must alert"
    assert list(log.values())[0]["status"] == "reopened"


def test_alert_message_names_the_evidence():
    txt = report.format_alert(_f())
    assert "https://x/i.jpg" in txt and "404" in txt and "Codi" in txt


def test_layout_finding_reports_measured_numbers():
    f = Finding(check="horizontal_scroll", kind="layout", url="https://x/a",
                viewport="phone", summary="page scrolls sideways", owner="codi",
                evidence=Evidence(selector="div.hero", numbers={"overflow_px": 37.5}),
                reproduced=True)
    txt = report.format_alert(f)
    assert "div.hero" in txt and "overflow_px=37.5" in txt


def test_log_survives_corrupt_lines(tmp_path):
    p = tmp_path / "log.jsonl"
    p.write_text('{"key":"a","status":"open"}\nnot json\n\n{"key":"b","status":"fixed"}\n')
    log = report.load_log(str(p))
    assert set(log) == {"a", "b"}


def test_log_roundtrip(tmp_path):
    p = str(tmp_path / "log.jsonl")
    log, _ = reconcile_helper([_f()], {})
    report.save_log(log, p)
    assert report.load_log(p) == log


def test_redact_hides_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET123")
    assert "SECRET123" not in report.redact("url with SECRET123 inside")


def test_send_telegram_false_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert report.send_telegram("hi") is False
    assert "Telegram env missing" in capsys.readouterr().out


def test_heartbeat_only_on_monday():
    assert report.heartbeat_due(MON) is True
    assert report.heartbeat_due(TUE) is False


def test_could_not_run_is_distinct_from_site_wrong():
    txt = report.could_not_run_text("import error", "http://run")
    assert "COULD NOT RUN" in txt and "NOT checked" in txt and "not a site fault" in txt
