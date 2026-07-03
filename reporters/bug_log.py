"""
Local bug log with a real lifecycle. Replaces the prior append-only writer.

bug-log.jsonl holds one JSON object per unique (site, check_id) issue.
- New issue            -> status "open", first_seen stamped once
- Seen again           -> same entry updated in place (last_seen, urls, evidence)
- Absent from a sweep  -> reconcile() marks it "fixed", computes MTTR
- Fires after a fix    -> status "reopened", severity bumped one tier

Versioned via git so the log is the audit trail; Telegram is the alert channel.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "bug-log.jsonl"

SEV_BUMP = {"low": "medium", "medium": "high", "high": "critical", "critical": "critical"}


def _path():
    return Path(os.environ.get("DES_BUG_LOG", LOG_PATH))


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


def _now():
    return datetime.now(timezone.utc).isoformat()


def log_finding(finding):
    entries = _load()
    key = (finding["site"], finding.get("check_id", ""))
    now = _now()
    # latest entry for this issue, if any
    idx = None
    for i in range(len(entries) - 1, -1, -1):
        if (entries[i].get("site"), entries[i].get("check_id", "")) == key:
            idx = i
            break
    if idx is not None and entries[idx].get("status") in ("open", "reopened"):
        e = entries[idx]
        e["last_seen"] = now
        e["severity"] = finding["severity"]
        e["url_count"] = len(finding["urls"])
        e["url_list"] = finding["urls"][:50]
        e["evidence"] = str(finding.get("evidence", ""))[:1900]
        _save(entries)
        return str(_path())
    status, severity, first_seen = "open", finding["severity"], now
    if idx is not None and entries[idx].get("status") == "fixed":
        # Regression: reopen and bump severity one tier (per the skill spec).
        status = "reopened"
        severity = SEV_BUMP.get(finding["severity"], finding["severity"])
    entries.append({
        "title": finding["title"],
        "severity": severity,
        "status": status,
        "site": finding["site"],
        "in_charge": finding["in_charge"],
        "first_seen": first_seen,
        "last_seen": now,
        "fixed_at": None,
        "url_count": len(finding["urls"]),
        "url_list": finding["urls"][:50],
        "check_id": finding.get("check_id", ""),
        "evidence": str(finding.get("evidence", ""))[:1900],
        "screenshots": finding.get("screenshots", []),
        "MTTR_hours": None,
    })
    _save(entries)
    return str(_path())


def reconcile(site, current_check_ids):
    """Close every open issue for `site` whose check no longer fires.
    Call ONLY after a full-sitemap sweep (a --limit run would mass-close
    issues on pages it never visited)."""
    entries = _load()
    now = datetime.now(timezone.utc)
    closed = []
    for e in entries:
        if e.get("site") != site or e.get("status") not in ("open", "reopened"):
            continue
        if e.get("check_id") in current_check_ids:
            continue
        e["status"] = "fixed"
        e["fixed_at"] = now.isoformat()
        try:
            first = datetime.fromisoformat(e["first_seen"])
            e["MTTR_hours"] = round((now - first).total_seconds() / 3600, 1)
        except (KeyError, ValueError):
            pass
        closed.append(e.get("check_id"))
    if closed:
        _save(entries)
    return closed


def close_finding(check_id, mttr_hours=None):
    """Manual closure of a single issue (any site)."""
    entries = _load()
    for e in reversed(entries):
        if e.get("check_id") == check_id and e.get("status") in ("open", "reopened"):
            e["status"] = "fixed"
            e["fixed_at"] = _now()
            e["MTTR_hours"] = mttr_hours
            _save(entries)
            return True
    return False
