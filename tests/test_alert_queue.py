"""
Unit tests for reporters/alert_queue.py — cross-run persisted HIGH queue with
the 08:00 SGT flush window. Uses DES_ALERT_QUEUE pointing at tmp_path and a
fake send_fn that records calls — no network.

    ./.venv/bin/python -m pytest tests/test_alert_queue.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporters import alert_queue as q

SGT = timezone(timedelta(hours=8))


def _queue_path(tmp_path, monkeypatch):
    p = tmp_path / "alert-queue.jsonl"
    monkeypatch.setenv("DES_ALERT_QUEUE", str(p))
    return p


def test_in_window_boundaries():
    assert q.in_window(datetime(2026, 8, 2, 3, 0, tzinfo=SGT)) is False
    assert q.in_window(datetime(2026, 8, 2, 8, 0, tzinfo=SGT)) is True
    assert q.in_window(datetime(2026, 8, 2, 21, 59, tzinfo=SGT)) is True
    assert q.in_window(datetime(2026, 8, 2, 22, 0, tzinfo=SGT)) is False


def test_enqueue_writes_one_record_with_expected_fields(tmp_path, monkeypatch):
    _queue_path(tmp_path, monkeypatch)
    q.enqueue("TRW", "high", "console_errors", "[DES] HIGH text", screenshot=None)
    entries = q._load()
    assert len(entries) == 1
    rec = entries[0]
    for key in ("site", "severity", "check_id", "text", "screenshot", "queued_at"):
        assert key in rec
    assert rec["site"] == "TRW"
    assert rec["severity"] == "high"
    assert rec["check_id"] == "console_errors"


def test_flush_due_at_0300_sgt_sends_nothing_and_leaves_file_untouched(tmp_path, monkeypatch):
    p = _queue_path(tmp_path, monkeypatch)
    q.enqueue("TRW", "high", "console_errors", "text")
    before = p.read_bytes()
    sent_calls = []
    n = q.flush_due("TRW", lambda text: sent_calls.append(text) or True,
                     now=datetime(2026, 8, 2, 3, 0, tzinfo=SGT))
    assert n == 0
    assert sent_calls == []
    assert p.read_bytes() == before


def test_flush_due_sends_at_0800_and_2159_sgt(tmp_path, monkeypatch):
    p = _queue_path(tmp_path, monkeypatch)
    q.enqueue("TRW", "high", "console_errors", "text-a")
    sent = []
    n = q.flush_due("TRW", lambda text: sent.append(text) or True,
                     now=datetime(2026, 8, 2, 8, 0, tzinfo=SGT))
    assert n == 1
    assert sent == ["text-a"]
    assert q._load() == []

    q.enqueue("TRW", "high", "console_errors", "text-b")
    sent2 = []
    n2 = q.flush_due("TRW", lambda text: sent2.append(text) or True,
                      now=datetime(2026, 8, 2, 21, 59, tzinfo=SGT))
    assert n2 == 1
    assert sent2 == ["text-b"]


def test_flush_due_never_touches_another_sites_records(tmp_path, monkeypatch):
    _queue_path(tmp_path, monkeypatch)
    q.enqueue("AURA", "high", "mobile_menu", "aura-text")
    q.enqueue("TRW", "high", "console_errors", "trw-text")
    sent = []
    n = q.flush_due("TRW", lambda text: sent.append(text) or True,
                     now=datetime(2026, 8, 2, 8, 0, tzinfo=SGT))
    assert n == 1
    assert sent == ["trw-text"]
    remaining = q._load()
    assert len(remaining) == 1
    assert remaining[0]["site"] == "AURA"
    assert remaining[0]["text"] == "aura-text"


def test_flush_due_keeps_record_when_send_fails(tmp_path, monkeypatch):
    _queue_path(tmp_path, monkeypatch)
    q.enqueue("TRW", "high", "console_errors", "text")
    n = q.flush_due("TRW", lambda text: False,
                     now=datetime(2026, 8, 2, 8, 0, tzinfo=SGT))
    assert n == 0
    remaining = q._load()
    assert len(remaining) == 1
    assert remaining[0]["text"] == "text"


def test_flush_due_uses_send_photo_fn_when_screenshot_exists(tmp_path, monkeypatch):
    _queue_path(tmp_path, monkeypatch)
    shot = tmp_path / "shot.jpg"
    shot.write_bytes(b"fake-jpeg")
    q.enqueue("TRW", "high", "mobile_menu", "text-with-shot", screenshot=str(shot))
    photo_calls = []
    text_calls = []
    n = q.flush_due(
        "TRW",
        lambda text: text_calls.append(text) or True,
        send_photo_fn=lambda path, caption: photo_calls.append((path, caption)) or True,
        now=datetime(2026, 8, 2, 8, 0, tzinfo=SGT),
    )
    assert n == 1
    assert photo_calls == [(str(shot), "text-with-shot")]
    assert text_calls == []
