"""
Cross-run persisted HIGH-severity alert queue (D-04).

HIGH findings are never sent immediately. They're appended here and drained
by flush_due() on the next sweep (any site, any tier) that runs inside the
08:00-22:00 SGT business-hours window, so Ed is not woken by a 03:00 alert.

Why this works without a scheduler: the daily cron is 00:00 UTC = 08:00 SGT
(inside the window, so the backlog flushes every morning), the weekly cron is
22:00 UTC Sunday = 06:00 SGT Monday (outside the window, correctly holds),
and the deep cron is 15:00 UTC = 23:00 SGT (outside the window, correctly
holds). Any sweep that happens to run inside 08:00-22:00 SGT flushes the
backlog for its own site as a side effect of that run.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE_PATH = ROOT / "alert-queue.jsonl"

SGT = timezone(timedelta(hours=8))


def _path():
    return Path(os.environ.get("DES_ALERT_QUEUE", QUEUE_PATH))


def _load():
    p = _path()
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def _save(entries):
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _sgt_now():
    return datetime.now(SGT)


def in_window(now=None):
    now = now or _sgt_now()
    return 8 <= now.hour < 22


def enqueue(site, severity, check_id, text, screenshot=None):
    """Append one queued alert record. `site` must match the sites/*.yaml
    `name` value; merge_bug_log.py keys off it during the sweep.yml commit
    step, the same way it already does for bug-log.jsonl."""
    entries = _load()
    entries.append({
        "site": site,
        "severity": severity,
        "check_id": check_id,
        "text": text,
        "screenshot": screenshot,
        "queued_at": _sgt_now().isoformat(),
    })
    _save(entries)


def flush_due(site, send_fn, send_photo_fn=None, now=None):
    """Send every queued record for `site` if we're inside the 08:00-22:00
    SGT window; otherwise leave the file untouched and return 0. A record is
    dropped ONLY when its send returns truthy. A failed send stays queued
    (and the caller's own UNDELIVERED tracking, via send_fn/send_photo_fn,
    fails the run). Every other site's records are rewritten back unchanged."""
    if not in_window(now):
        return 0
    entries = _load()
    survivors = []
    sent = 0
    for e in entries:
        if e.get("site") != site:
            survivors.append(e)
            continue
        shot = e.get("screenshot")
        if shot and send_photo_fn and os.path.exists(shot):
            ok = bool(send_photo_fn(shot, e.get("text", "")))
        else:
            ok = bool(send_fn(e.get("text", "")))
        if ok:
            sent += 1
        else:
            survivors.append(e)
    _save(survivors)
    return sent
