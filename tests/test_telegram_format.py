"""
Unit tests for reporters/telegram.py — chunking + HTML formatters. No
network: send()/send_photo() network paths are exercised only via their
missing-credentials branch (deterministic, no requests.post call).

    ./.venv/bin/python -m pytest tests/test_telegram_format.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporters import telegram as t


TRW_FINDING = {
    "title": "Mobile Menu",
    "check_id": "mobile_menu",
    "severity": "critical",
    "site": "TRW",
    "in_charge": "Bryan",
    "summary": "menu did not autoclose",
    "urls": ["https://therightworkshop.com/a/", "https://therightworkshop.com/b/"],
    "details": [],
}

AURA_FINDING = dict(TRW_FINDING, site="AURA", in_charge="Codi")


# --- _chunks -----------------------------------------------------------

def test_chunks_never_exceeds_limit_and_preserves_lines():
    lines = [f"line {i} of the alert body with some padding text" for i in range(200)]
    text = "\n".join(lines)
    chunks = t._chunks(text, 4096)
    assert all(len(c) <= 4096 for c in chunks)
    # Strip continuation prefixes and rejoin — every source line must survive.
    rebuilt = []
    for i, c in enumerate(chunks):
        if i > 0:
            c = c.split("\n", 1)[1]
        rebuilt.append(c)
    assert "\n".join(rebuilt) == text


def test_chunks_12000_char_body_returns_three_chunks():
    body = "x" * 12000
    chunks = t._chunks(body, 4096)
    assert len(chunks) == 3
    n = len(chunks)
    assert chunks[1].startswith(f"[DES] (cont. 2/{n})")
    assert chunks[2].startswith(f"[DES] (cont. 3/{n})")


def test_chunks_short_text_returns_single_chunk_no_prefix():
    chunks = t._chunks("short alert", 4096)
    assert chunks == ["short alert"]


# --- format_critical / format_high --------------------------------------

def test_format_critical_trw_header():
    out = t.format_critical(TRW_FINDING)
    assert out.startswith("<b>🔧 [DES] CRITICAL")


def test_format_critical_aura_header():
    out = t.format_critical(AURA_FINDING)
    assert out.startswith("<b>🐾 [DES]")


def test_format_critical_escapes_script_tag():
    f = dict(TRW_FINDING, title="<script>alert(1)</script>")
    out = t.format_critical(f)
    assert "&lt;script&gt;" in out
    assert "<script>" not in out


def test_format_critical_with_report_url():
    out = t.format_critical(TRW_FINDING, report_url="https://x/y")
    assert '<a href="https://x/y">' in out


def test_format_high_contains_reroute_line():
    out = t.format_high(TRW_FINDING)
    assert "Codi reroutes to Bryan now." in out
    assert out.startswith("<b>🔧 [DES] HIGH")


def test_format_digest_bold_header_and_bullets():
    out = t.format_digest([TRW_FINDING], "medium", "TRW", "daily", report_url="https://x/y")
    assert out.startswith("<b>🔧 [DES] MEDIUM DIGEST")
    assert "•" in out
    assert '<a href="https://x/y">' in out


def test_format_digest_empty_returns_none():
    assert t.format_digest([], "medium", "TRW", "daily") is None


# --- send() missing-credentials branch (no network) ---------------------

def test_send_missing_credentials_records_undelivered(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    t.UNDELIVERED.clear()
    ok = t.send("test alert")
    assert ok is False
    assert "test alert" in t.UNDELIVERED
    t.UNDELIVERED.clear()


def test_send_photo_missing_file_falls_back_to_send(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    t.UNDELIVERED.clear()
    ok = t.send_photo("/nonexistent/path.jpg", "caption text")
    assert ok is False
    assert "caption text" in t.UNDELIVERED
    t.UNDELIVERED.clear()
