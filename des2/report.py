"""Reporting: the bug log, the routing, and the three kinds of message.

Des is report-only (SPEC-V2 section 6). It never writes to either site. Its
whole value is being believed, so everything here is built around not crying
wolf and not going quiet when it should speak.

Three messages exist, and they are deliberately different:
  ALERT        something is broken, proven, and named.
  COULD NOT RUN the guard itself failed. NOT the same as "site is wrong", and
               conflating the two cost a morning in Aug 2026.
  HEARTBEAT    weekly all-clear, so that silence becomes meaningful. When
               billing froze every workflow for four days, silence read exactly
               like all clear.

Re-alert policy: a finding alerts when it is NEW or when it REOPENS. A finding
already open and unchanged stays in the log and does not ping again, because a
guard that repeats yesterday's news daily is one you learn to ignore.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

from des2.models import Finding

SGT = timezone(timedelta(hours=8))
BUG_LOG = "bug-log-v2.jsonl"
OWNER_LABEL = {"bryan": "Bryan", "cole": "Cole", "codi": "Codi", "dom": "Dom"}


# ---------------------------------------------------------------- bug log
def load_log(path: str = BUG_LOG) -> dict[str, dict]:
    """key -> record. A corrupt line is skipped, never fatal."""
    out: dict[str, dict] = {}
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("key"):
                out[rec["key"]] = rec
    return out


def save_log(records: dict[str, dict], path: str = BUG_LOG) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        for rec in records.values():
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    os.replace(tmp, path)


def reconcile(findings: Iterable[Finding], log: dict[str, dict],
              now: Optional[datetime] = None,
              swept_urls: Optional[set[str]] = None) -> tuple[dict[str, dict], list[Finding]]:
    """Fold this sweep into the log. Returns (new_log, findings_worth_alerting).

    Statuses: open (currently failing), fixed (was failing, now clean on a page
    we actually swept). A fixed finding that fails again becomes reopened, and
    reopening DOES alert, because a regression coming back matters.

    Only pages actually swept may be auto-closed. Closing a finding because we
    did not look at its page is how a guard lies.
    """
    now = now or datetime.now(SGT)
    stamp = now.isoformat()
    log = dict(log)
    seen: set[str] = set()
    worth_alerting: list[Finding] = []

    for f in findings:
        k = f.key()
        seen.add(k)
        rec = log.get(k)
        if rec is None:
            log[k] = {"key": k, "check": f.check, "kind": f.kind, "url": f.url,
                      "viewport": f.viewport, "owner": f.owner,
                      "summary": f.summary, "evidence": f.evidence.__dict__,
                      "status": "open", "first_seen": stamp, "last_seen": stamp}
            worth_alerting.append(f)
        elif rec.get("status") == "fixed":
            rec.update({"status": "reopened", "last_seen": stamp,
                        "reopened_at": stamp, "summary": f.summary,
                        "evidence": f.evidence.__dict__})
            worth_alerting.append(f)
        else:
            rec.update({"status": "open", "last_seen": stamp,
                        "summary": f.summary, "evidence": f.evidence.__dict__})

    if swept_urls:
        for k, rec in log.items():
            if (k not in seen and rec.get("status") in ("open", "reopened")
                    and rec.get("url") in swept_urls):
                rec.update({"status": "fixed", "fixed_at": stamp})
    return log, worth_alerting


# ---------------------------------------------------------------- messages
def format_alert(f: Finding) -> str:
    ev = f.evidence
    bits = [f"🔴 Des: {f.summary}", f"Page: {f.url} ({f.viewport})"]
    if ev.resource:
        bits.append(f"Resource: {ev.resource}" + (f" [HTTP {ev.status}]" if ev.status else ""))
    elif ev.status:
        bits.append(f"HTTP {ev.status}")
    if ev.selector:
        bits.append(f"Element: {ev.selector}")
    if ev.numbers:
        bits.append("Measured: " + ", ".join(f"{k}={v}" for k, v in sorted(ev.numbers.items())))
    if ev.note:
        bits.append(ev.note[:200])
    bits.append(f"In-charge: {OWNER_LABEL.get(f.owner, f.owner)}")
    return "\n".join(bits)


def format_digest(findings: list[Finding], site: str) -> str:
    head = f"🔧 Des digest: {site} — {len(findings)} finding(s)"
    lines = [head]
    for f in findings[:15]:
        lines.append(f"• [{f.check}] {f.url} ({OWNER_LABEL.get(f.owner, f.owner)})")
    if len(findings) > 15:
        lines.append(f"(+{len(findings) - 15} more in the log)")
    return "\n".join(lines)


def redact(text: str) -> str:
    """Never let a bot token reach a log or a report."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return text.replace(token, "***") if token else text


def send_telegram(text: str) -> bool:
    """Fail LOUD and return a boolean. A swallowed send is a silent guard."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print("[warn] Telegram env missing; alert not sent")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text,
                                "disable_web_page_preview": True}, timeout=20)
        ok = r.status_code == 200 and (r.json() or {}).get("ok") is True
        if not ok:
            print(f"[error] Telegram alert FAILED: {r.status_code} {redact(r.text)[:200]}")
        return ok
    except Exception as e:
        print(f"[error] Telegram alert FAILED: {redact(str(e))[:200]}")
        return False


def heartbeat_due(now: Optional[datetime] = None) -> bool:
    """Mondays only. A daily all-clear trains you to ignore it."""
    now = now or datetime.now(SGT)
    return now.weekday() == 0


def heartbeat_text(pages: int, site: str) -> str:
    return (f"✅ Des: {site} checked, {pages} pages, nothing broken. "
            "(Weekly heartbeat. If a Monday passes with no message, the guard is not running.)")


def could_not_run_text(reason: str, run_url: str = "") -> str:
    return (f"⚠️ Des: the sweep COULD NOT RUN ({reason}). "
            f"The site was NOT checked. This is a guard failure, not a site fault. {run_url}").strip()
