"""
Local bug log writer. Replaces the prior Notion sink.

Appends one JSON object per finding to bug-log.jsonl in the repo root.
Versioned via git so the log is the audit trail; Telegram is the alert channel.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "bug-log.jsonl"


def log_finding(finding):
    entry = {
        "title": finding["title"],
        "severity": finding["severity"],
        "status": finding.get("status", "open"),
        "site": finding["site"],
        "in_charge": finding["in_charge"],
        "first_seen": finding.get("first_seen", datetime.now(timezone.utc).isoformat()),
        "last_seen": datetime.now(timezone.utc).isoformat(),
        "fixed_at": None,
        "url_count": len(finding["urls"]),
        "url_list": finding["urls"][:50],
        "check_id": finding.get("check_id", ""),
        "evidence": finding.get("evidence", "")[:1900],
        "screenshots": finding.get("screenshots", []),
        "MTTR_hours": None,
    }
    log_path = Path(os.environ.get("DES_BUG_LOG", LOG_PATH))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return str(log_path)


def close_finding(check_id, mttr_hours=None):
    """Append a closure record. Real reconciliation happens on next sweep cycle."""
    log_path = Path(os.environ.get("DES_BUG_LOG", LOG_PATH))
    entry = {
        "check_id": check_id,
        "status": "fixed",
        "fixed_at": datetime.now(timezone.utc).isoformat(),
        "MTTR_hours": mttr_hours,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
