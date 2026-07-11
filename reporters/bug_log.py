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


# Check ids the harness itself emits. reconcile() only auto-closes these —
# manually logged findings (audit campaigns, ad-hoc entries) use other ids and
# must be closed by a human/close_finding, or a clean sweep would mass-close
# issues the harness never re-checks.
HARNESS_CHECK_IDS = {
    "load_failed", "http_5xx", "http_4xx_dead_page", "http_4xx_access_blocked",
    "sweep_page_crash", "missing_nav", "missing_footer", "missing_required_nav_link",
    "maroon_leak", "broken_images", "console_errors", "mobile_menu", "dead_buttons",
    "em_dash", "autop_injection", "missing_byline", "missing_unit_number",
    "footer_drift", "missing_breadcrumb", "missing_meta_description",
    "long_meta_description", "missing_title", "long_title", "missing_canonical",
    "missing_h1", "multiple_h1", "missing_alt", "missing_markers",
    # Curated deep-audit ids, now auto-verified each full sweep by a producer in
    # checks/recheck.py (see checks.recheck.REGISTRY). Safe to reconcile ONLY
    # because that producer re-tests the specific bug and emits this id when it
    # still reproduces (and a keep-open synthetic if it crashes). Adding any of
    # these WITHOUT a recheck producer would cause a false mass-close.
    "mobile_menu_no_open", "autop_p_script_wrap", "meta_description_css_leak",
    "coe_hub_dead_bidding_links", "phantom_topic_tag_links", "overflow_phone",
    "comparison_table_clipped_mobile", "home_hero_left_clip_desktop",
    "no_mobile_navigation", "cookie_banner_covers_form_mobile", "home_header_contrast",
    "hub_links_to_unbuilt_conditions", "nav_visit_only_homepage", "footer_five_variants",
    "opening_hours_contradictions", "vet_report_policy_contradiction", "en_dash_footer_hours",
}


def open_records(site, check_ids=None):
    """Return the open/reopened records for `site`. If `check_ids` is given, only
    those whose check_id is in it (used by the curated recheck stage to fetch the
    records it can re-verify). Read-only; does not mutate the log."""
    out = []
    for e in _load():
        if e.get("site") != site or e.get("status") not in ("open", "reopened"):
            continue
        if check_ids is not None and e.get("check_id") not in check_ids:
            continue
        out.append(e)
    return out


def reconcile(site, current_check_ids):
    """Close every open HARNESS-emitted issue for `site` whose check no longer
    fires. Call ONLY after a full-sitemap sweep (a --limit run would mass-close
    issues on pages it never visited)."""
    entries = _load()
    now = datetime.now(timezone.utc)
    closed = []
    for e in entries:
        if e.get("site") != site or e.get("status") not in ("open", "reopened"):
            continue
        if e.get("check_id") not in HARNESS_CHECK_IDS:
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
