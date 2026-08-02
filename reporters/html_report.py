"""
Self-contained per-sweep HTML report (D-02). One file per sweep, embedding
every finding screenshot as base64 so it opens offline (file://) with zero
external network requests. Linked from every Telegram alert via blob_url().

Race safety: each sweep writes a UNIQUE timestamped filename under its own
site directory, so trw and aura never contend for a path. The report is
untracked when sweep.yml's commit step runs `git reset --hard`, and
reset --hard does not delete untracked files, so it survives. No merge logic
is needed for reports — only for the tracked JSONL files.
"""
import base64
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reporters import bug_log, evidence

REPO_SLUG = os.environ.get("DES_REPO_SLUG", "ededai/des-website-guard")
REPORTS_DIR = ROOT / "reports"
KEEP_PER_SITE = 10

SEV_ORDER = ["critical", "high", "medium", "low"]
SEV_COLOR = {"critical": "#7a1f1f", "high": "#b8590a", "medium": "#9a7d0a", "low": "#5a6b7a"}


def report_rel_path(site, tier, ts):
    return f"reports/{str(site).lower()}/{ts:%Y%m%d-%H%M%S}-{tier}.html"


def blob_url(rel_path):
    return f"https://github.com/{REPO_SLUG}/blob/main/{rel_path}"


def _b64_img(path):
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _parse_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _what_changed(site, sweep_started):
    """Classify every bug-log record for `site` relative to sweep_started:
    NEW (first seen this sweep), FIXED (closed this sweep, with MTTR),
    REGRESSED (reopened this sweep), STILL OPEN (older open/reopened)."""
    new, fixed, still_open, regressed = [], [], [], []
    for e in bug_log._load():
        if e.get("site") != site:
            continue
        status = e.get("status")
        fs = _parse_iso(e.get("first_seen"))
        fa = _parse_iso(e.get("fixed_at"))
        if status == "fixed" and fa and fa >= sweep_started:
            fixed.append(e)
        elif status == "reopened" and fs and fs >= sweep_started:
            regressed.append(e)
        elif status in ("open", "reopened") and fs and fs >= sweep_started:
            new.append(e)
        elif status in ("open", "reopened") and fs and fs < sweep_started:
            still_open.append(e)
    return new, fixed, still_open, regressed


def _days_open(first_seen, now=None):
    now = now or datetime.now(timezone.utc)
    fs = _parse_iso(first_seen)
    if not fs:
        return "?"
    return round((now - fs).total_seconds() / 86400, 1)


def _finding_card(f):
    esc = evidence.escape_html
    sev = f.get("severity", "low")
    color = SEV_COLOR.get(sev, "#5a6b7a")
    title = esc(f.get("title", ""))
    urls = f.get("urls", [])
    url_items = "".join(f"<li>{esc(u)}</li>" for u in urls)
    ev = esc(evidence.humanize(f.get("evidence", "")))
    details = f.get("details") or []
    detail_html = ""
    if details:
        detail_items = "".join(f"<li>{esc(evidence.humanize(d))}</li>" for d in details)
        detail_html = f"<div class='details'><strong>Details:</strong><ul>{detail_items}</ul></div>"
    img_html = ""
    for s in (f.get("screenshots") or []):
        b64 = _b64_img(s)
        if b64:
            img_html += f'<img src="{b64}" alt="finding screenshot">'
    return (
        f'<div class="card" style="border-left:6px solid {color}">'
        f'<span class="badge" style="background:{color}">{sev.upper()}</span>'
        f'<h3>{title}</h3>'
        f'<div class="urls"><strong>Affected URLs ({len(urls)}):</strong><ul>{url_items}</ul></div>'
        f'<div class="evidence"><strong>Evidence:</strong> {ev}</div>'
        f'{detail_html}{img_html}'
        f'</div>'
    )


def build(site, tier, findings, sweep_started, out_rel, waived=(), aborted=False):
    esc = evidence.escape_html
    out_path = ROOT / out_rel
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(sweep_started, datetime) and sweep_started.tzinfo is None:
        sweep_started = sweep_started.replace(tzinfo=timezone.utc)
    sweep_end = datetime.now(timezone.utc)

    new, fixed, still_open, regressed = _what_changed(site, sweep_started)

    counts = {sev: 0 for sev in SEV_ORDER}
    for f in findings:
        s = f.get("severity", "low")
        counts[s] = counts.get(s, 0) + 1

    changed_rows = []
    for e in new:
        changed_rows.append(f"<li class='new'>NEW &mdash; {esc(e.get('title',''))} ({esc(e.get('check_id',''))})</li>")
    for e in fixed:
        mttr = e.get("MTTR_hours")
        changed_rows.append(f"<li class='fixed'>FIXED &mdash; {esc(e.get('title',''))} (MTTR {esc(mttr)}h)</li>")
    for e in regressed:
        changed_rows.append(f"<li class='regressed'>REGRESSED &mdash; {esc(e.get('title',''))}</li>")
    for e in still_open:
        changed_rows.append(
            f"<li class='open'>STILL OPEN &mdash; {esc(e.get('title',''))} "
            f"({_days_open(e.get('first_seen'), sweep_end)}d open)</li>"
        )

    findings_sorted = sorted(
        findings,
        key=lambda f: SEV_ORDER.index(f["severity"]) if f.get("severity") in SEV_ORDER else 9,
    )
    cards = "".join(_finding_card(f) for f in findings_sorted)

    waived_list = list(waived)
    waived_items = "".join(f"<li>{esc(f.get('title',''))}</li>" for f in waived_list)

    html_doc = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>[DES] {esc(site)} {esc(tier)} report</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background:#faf7f2; color:#2a2a2a; margin:0; padding:24px; }}
h1, h2 {{ margin-top:0; }}
.card {{ background:#fff; padding:16px; margin:12px 0; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,.12); }}
.badge {{ display:inline-block; color:#fff; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold; }}
img {{ max-width:100%; margin-top:8px; border-radius:4px; display:block; }}
ul {{ margin:4px 0; padding-left:20px; }}
.new {{ color:#7a1f1f; }} .fixed {{ color:#1f6b2f; }} .open {{ color:#9a7d0a; }} .regressed {{ color:#a30000; }}
details summary {{ cursor:pointer; margin-top:16px; }}
</style></head>
<body>
<h1>{esc(site)} &mdash; {esc(tier)} sweep report{" (ABORTED)" if aborted else ""}</h1>
<p>Started: {sweep_started.isoformat()} | Ended: {sweep_end.isoformat()}</p>
<p>Critical: {counts.get('critical',0)} | High: {counts.get('high',0)} | Medium: {counts.get('medium',0)} | Low: {counts.get('low',0)} | Waived: {len(waived_list)}</p>
<h2>What changed since the last sweep</h2>
<ul>{"".join(changed_rows) or "<li>No change.</li>"}</ul>
<h2>Findings</h2>
{cards or "<p>No findings this sweep.</p>"}
<details><summary>Waived findings ({len(waived_list)})</summary><ul>{waived_items}</ul></details>
</body></html>
"""
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def prune(site, keep=KEEP_PER_SITE):
    """Delete all but the newest `keep` reports for `site` (sorted by
    filename — the fixed-width timestamp format sorts chronologically).
    Returns the deleted paths, relative to repo root."""
    d = REPORTS_DIR / str(site).lower()
    if not d.exists():
        return []
    files = sorted(d.glob("*.html"))
    if len(files) <= keep:
        return []
    to_delete = files[: len(files) - keep]
    deleted = []
    for f in to_delete:
        rel = str(f.relative_to(ROOT))
        f.unlink()
        deleted.append(rel)
    return deleted


def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--site", required=True)
    ap.add_argument("--keep", type=int, default=KEEP_PER_SITE)
    a = ap.parse_args()
    if a.prune:
        deleted = prune(a.site, keep=a.keep)
        for d in deleted:
            print(f"DES: pruned {d}")
        print(f"DES: pruned {len(deleted)} old report(s) for {a.site}")


if __name__ == "__main__":
    _cli()
