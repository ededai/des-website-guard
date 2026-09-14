"""Self-contained per-sweep HTML report for Des v2.

Restores the visual record every Telegram alert implicitly points at, which
v2 stopped producing: no report landed in reports/trw/ after 2026-08-18
because v2's persist step only ever committed baselines/ and the bug log
(T-3, deep-sweep-2026-09-13.md). This is v2's own writer. It does not import
from the v1 `reporters` package, which is disabled -- it reuses the same
styling ideas (inline CSS, severity-colored cards, no external assets) so the
report still opens offline over file://.

One file per sweep: reports/<site>/<YYYYmmdd-HHMMSS>-<tier>.html.
"""
from __future__ import annotations

import html as _html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from des2.models import Finding

SGT = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"
KEEP_PER_SITE = 10

OWNER_LABEL = {"bryan": "Bryan", "cole": "Cole", "codi": "Codi", "dom": "Dom"}
# breakage: something is provably wrong. layout: visual regression. standard_lost:
# a page had something and lost it. Colors only; the label text is the kind itself.
KIND_COLOR = {"breakage": "#7a1f1f", "layout": "#9a7d0a", "standard_lost": "#1f4f7a"}


def _esc(s) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


def report_path(site: str, tier: str, ts: Optional[datetime] = None) -> Path:
    ts = ts or datetime.now(SGT)
    return REPORTS_DIR / str(site).lower() / f"{ts:%Y%m%d-%H%M%S}-{tier}.html"


def _finding_card(f: Finding) -> str:
    color = KIND_COLOR.get(f.kind, "#5a6b7a")
    owner = OWNER_LABEL.get(f.owner, f.owner)
    ev = f.evidence
    bits = []
    if ev.resource:
        bits.append(f"resource: {_esc(ev.resource)}")
    if ev.status:
        bits.append(f"HTTP {_esc(ev.status)}")
    if ev.selector:
        bits.append(f"element: {_esc(ev.selector)}")
    if ev.numbers:
        bits.append("measured: " + ", ".join(f"{_esc(k)}={_esc(v)}"
                                              for k, v in sorted(ev.numbers.items())))
    if ev.note:
        bits.append(_esc(ev.note[:300]))
    ev_html = " | ".join(bits) or "no further evidence recorded"
    reproduced = "confirmed on a clean recheck" if f.reproduced else "not reconfirmed"
    return (
        f'<div class="card" style="border-left:6px solid {color}">'
        f'<span class="badge" style="background:{color}">{_esc(f.kind)}</span>'
        f'<h3>{_esc(f.check)}</h3>'
        f'<p class="summary">{_esc(f.summary)}</p>'
        f'<p class="meta">Page: {_esc(f.url)} ({_esc(f.viewport)})</p>'
        f'<p class="meta">Evidence: {ev_html}</p>'
        f'<p class="meta">In charge: {_esc(owner)}. {reproduced}.</p>'
        f'</div>'
    )


def build(site: str, tier: str, findings: Iterable[Finding], urls_swept: int,
          open_count: int = 0, escalations: Optional[list[dict]] = None,
          started: Optional[datetime] = None, out_path: Optional[Path] = None) -> Path:
    """Write one self-contained report for a finished sweep and return its path.

    findings: the findings THIS sweep can prove (verify.partition()'s
    alertable half), so the report and the Telegram alerts always agree.
    open_count: the bug log's running total, so a quiet sweep never reads as
    "nothing is wrong" while a backlog sits open.
    escalations: the aged-open records report.escalations() returned this run.
    """
    findings = list(findings)
    started = started or datetime.now(SGT)
    if started.tzinfo is None:
        started = started.replace(tzinfo=SGT)
    escalations = list(escalations or [])
    out_path = out_path or report_path(site, tier, started)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cards = "".join(_finding_card(f) for f in findings) or "<p>No findings this sweep.</p>"
    esc_rows = "".join(
        f"<li>{_esc(e.get('check', ''))} on {_esc(e.get('url', ''))} "
        f"(escalation level {_esc(e.get('escalation_level', ''))})</li>"
        for e in escalations
    ) or "<li>None.</li>"

    doc = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Des v2: {_esc(site)} {_esc(tier)} sweep</title>
<style>
body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; background:#faf7f2; color:#2a2a2a; margin:0; padding:24px; }}
h1, h2 {{ margin-top:0; }}
.card {{ background:#fff; padding:16px; margin:12px 0; border-radius:8px; box-shadow:0 1px 4px rgba(0,0,0,.12); }}
.badge {{ display:inline-block; color:#fff; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold; text-transform:uppercase; }}
.meta {{ color:#555; font-size:13px; margin:4px 0; }}
.summary {{ font-weight:600; margin:8px 0 4px; }}
ul {{ margin:4px 0; padding-left:20px; }}
</style></head>
<body>
<h1>Des v2: {_esc(site)} {_esc(tier)} sweep</h1>
<p>Run time: {_esc(started.astimezone(SGT).isoformat())} (SGT)</p>
<p>URLs swept: {_esc(urls_swept)} | Findings this sweep: {_esc(len(findings))} | Open in the bug log: {_esc(open_count)}</p>
<h2>Findings</h2>
{cards}
<h2>Escalations this run</h2>
<ul>{esc_rows}</ul>
</body></html>
"""
    out_path.write_text(doc, encoding="utf-8")
    return out_path


def prune(site: str, keep: int = KEEP_PER_SITE) -> list[str]:
    """Delete all but the newest `keep` reports for `site`.

    Sorted by filename, which sorts chronologically because the timestamp is
    fixed-width. Mirrors reporters/html_report.py's prune() without importing
    it. Returns the deleted paths, relative to the repo root.
    """
    d = REPORTS_DIR / str(site).lower()
    if not d.exists():
        return []
    files = sorted(d.glob("*.html"))
    if len(files) <= keep:
        return []
    to_delete = files[: len(files) - keep]
    deleted = []
    for f in to_delete:
        try:
            rel = str(f.relative_to(REPORTS_DIR.parent))
        except ValueError:
            rel = str(f)
        f.unlink()
        deleted.append(rel)
    return deleted
