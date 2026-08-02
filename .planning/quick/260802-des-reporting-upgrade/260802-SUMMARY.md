---
phase: quick/260802-des-reporting-upgrade
plan: 01
subsystem: des-website-guard reporting pipeline
tags: [telegram, html-report, screenshot, evidence-sanitizer, alert-queue]
dependency-graph:
  requires: []
  provides:
    - reporters/evidence.py (humanize, escape_html)
    - reporters/telegram.py (HTML send, sendPhoto, 4096 splitting, emoji headers)
    - reporters/alert_queue.py (cross-run HIGH queue, 08:00 SGT flush)
    - reporters/screenshot.py (reveal-safe capped JPEG capture)
    - reporters/html_report.py (self-contained per-sweep HTML report)
  affects:
    - src/run.py (wiring)
    - .github/workflows/sweep.yml (commit step, alert-queue merge, report prune)
tech-stack:
  added: []
  patterns:
    - pipeline-boundary sanitization (evidence.humanize/escape_html before any
      finding text reaches Telegram, bug-log, or the HTML report)
    - record-ownership JSONL merge reused for a second tracked file (alert-queue.jsonl)
key-files:
  created:
    - reporters/evidence.py
    - reporters/alert_queue.py
    - reporters/screenshot.py
    - reporters/html_report.py
    - tests/test_evidence.py
    - tests/test_telegram_format.py
    - tests/test_alert_queue.py
    - tests/test_html_report.py
  modified:
    - reporters/telegram.py
    - checks/visual.py
    - src/run.py
    - .github/workflows/sweep.yml
    - .gitignore
    - README.md
    - DEFERRED.md
    - requirements.txt
decisions:
  - "Kept the em dash in Telegram's own header format strings (format_critical/
    format_high/format_digest) since that exact format is plan-mandated and
    pre-existing in the original telegram.py; the no-em-dash rule was applied
    to README, DEFERRED, docstrings, new comments, and the generated report
    HTML copy instead."
  - "TRW's currently-live top pages had zero critical/high findings during
    verification, so the screenshot+base64-embed path was confirmed with a
    scratchpad-only diagnostic (real screenshot, isolated report dir) rather
    than a fabricated finding in the tracked bug-log/reports."
metrics:
  duration: "~90 minutes"
  completed: "2026-08-02"
---

# Quick Task 260802: Des Reporting Upgrade Summary

Screenshot-backed critical/high Telegram alerts, one self-contained per-sweep HTML report linked
from every alert, a cross-run HIGH queue that survives between Actions runs and flushes at 08:00
SGT, HTML-formatted per-site Telegram messages, and a systemic evidence sanitizer that makes a
raw Python dict repr impossible to ship to Telegram, the bug log, or the report.

## What was built

**Task 1 (commit `26796b4`)**: `reporters/evidence.py` (`humanize()` / `escape_html()`), a full
rewrite of `reporters/telegram.py` (HTML-mode `send()` with 4096-char chunking that preserves the
`[DES]` prefix on every chunk, `send_photo()` with a 1024-char caption truncated on a line boundary
and a text fallback, per-site emoji headers via `_emoji()`), `reporters/alert_queue.py` (cross-run
persisted HIGH queue, `flush_due()` gated on 08:00-22:00 SGT), and `checks/visual.py::check_mobile_menu`
now returns humanized string evidence instead of a raw dict. 29 new tests, all network-free.

**Task 2 (commit `8a5657e`)**: `reporters/screenshot.py` (`capture_alert_shot()` forces reveal-CSS
opacity before capture, viewport-sized JPEG q60, capped at `MAX_SHOTS_PER_SWEEP=12`, never raises)
and `reporters/html_report.py` (`build()` writes one self-contained base64-inlined HTML report per
sweep with a what-changed section sourced from `bug_log`, `prune()` keeps the newest 10 per site).
`.des-shots/` added to `.gitignore`. 6 new tests.

**Task 3 (commit `eb7f429`)**: wired everything into `src/run.py` (screenshot capture before
`ctx.close()`, humanized evidence in `dedupe()`, critical routes through `send_photo` with a report
link, high is queued instead of sent immediately, the HIGH queue is flushed after digests, the
report is built last including in dry-run), extended `sweep.yml`'s commit step to merge
`alert-queue.jsonl` by record ownership alongside `bug-log.jsonl` and prune old reports in the same
commit, rewrote `README.md` and `DEFERRED.md` to describe the pipeline that actually exists (all
Notion references removed, including `notion-client` from `requirements.txt`), and removed em
dashes from all newly authored prose per the writing hard rule.

## Verification performed

- `python -m pytest tests/ -q`: **63 passed**, including the pre-existing `tests/test_recheck.py`
  (57 before this plan + 6 new `test_html_report.py`... actually 29 + 6 + pre-existing = 63 total).
- `git diff --stat 4f8e6e3 -- src/crawl_guard.py`: **empty** — crawl-guard behavior untouched, confirmed.
- Live dry-run per the plan's exact command (`--site=trw --tier=critical --dry-run --limit 3 --delay 2`):
  exit 0, printed `DES: scanning 3 URLs...`, `DES: report .../reports/trw/20260802-041750-critical.html`,
  `DES: report url https://github.com/ededai/des-website-guard/blob/main/reports/trw/...`, no traceback.
  Result: 0 findings (TRW's top 3 priority pages are currently clean).
- Evidence-leak gate on that log: `NO-REPR-LEAK` (no `{'` or `': '` pattern anywhere).
- Supplementary broader dry-run (`--tier=weekly --limit 15`, mobile viewports included so
  `check_mobile_menu` ran too): all 15 pages hit a transient 403 on first request, backed off 5s per
  `crawl_guard`'s design, and every retry succeeded — 0 findings, confirming the backoff/retry path
  and that TRW's top 15 pages are genuinely clean right now (not a crawl_guard regression).
- Report self-containment gate on both real reports: no `src="reports/` or `.des-shots` relative
  path, no `cdn|fonts|unpkg` external reference. Byte count was 1.6KB (below the plan's >5000 sample
  threshold) **because there were zero findings to embed**, not a defect.
- `in_window()` boundary test: 03:00 SGT → False, 08:00 SGT → True, 23:00 SGT → False (all match).
- Since production TRW currently has no live critical/high finding to naturally trigger a
  screenshot, a scratchpad-only diagnostic (`verify_report_pipeline.py`, outside the repo, isolated
  `ROOT`/`REPORTS_DIR`/`SHOT_DIR` so nothing touched the tracked `reports/` or `bug-log.jsonl`)
  captured a REAL screenshot of therightworkshop.com's mobile homepage via
  `screenshot.capture_alert_shot()`, built a report with it via `html_report.build()`, then rendered
  that generated report in a second headless page and screenshotted it for visual review. Confirmed
  visually: the real rendered TRW homepage appears (reveal-CSS force worked — no blank cream page),
  the `<script>` title renders as literal escaped text (not executed), evidence renders as clean
  prose with no dict braces/quotes, and the what-changed section correctly pulled real STILL OPEN
  records from the live `bug-log.jsonl`.
- **Not run**: the live Telegram smoke send (plan step 8). Per coordinator instruction, this is
  deferred until after the verifier passes; still no `git push`.

## Deviations from Plan

### Auto-fixed issues

**1. [Rule 2 - stale docs] Updated `src/run.py`'s own module docstring severity-routing block**
- **Found during:** Task 3
- **Issue:** The top-of-file docstring still said "high: immediate Telegram + bug log", which the
  plan's own wiring change made false (high is now queued, not immediate).
- **Fix:** Updated the docstring to describe the actual critical/high/medium/low routing and
  mentioned the per-sweep HTML report.
- **Files modified:** `src/run.py`
- **Commit:** `eb7f429`

**2. [Rule 2 - hard writing rule] Removed em dashes from all newly authored prose**
- **Found during:** mid-Task-3, after the coordinator supplied README/DEFERRED/requirements/
  gitignore content and reiterated the no-em-dash rule.
- **Issue:** My initial README.md, DEFERRED.md, and several new docstrings/comments in
  `reporters/*.py` and `tests/*.py` used em dashes, matching this codebase's pre-existing style but
  violating Ed's hard writing rule for prose I author.
- **Fix:** Rewrote every em dash I introduced using colons, semicolons, periods, or restructured
  sentences. Left the Telegram alert format strings (`format_critical`/`format_high`/`format_digest`
  headers) using the em dash, since that exact format is plan-mandated and was already the pattern
  in the pre-existing `telegram.py`, and Telegram message copy is not one of the four scopes the
  rule named (README, DEFERRED, docstrings, report HTML copy). Left every pre-existing (unmodified)
  em dash in `src/run.py`, `checks/visual.py`, and `.github/workflows/sweep.yml` untouched (verified
  via `git diff | grep '^+' | grep -c em-dash` = 0 new occurrences in each).
- **Files modified:** README.md, DEFERRED.md, reporters/evidence.py, reporters/telegram.py,
  reporters/alert_queue.py, reporters/screenshot.py, reporters/html_report.py, tests/test_evidence.py,
  tests/test_telegram_format.py, tests/test_alert_queue.py, tests/test_html_report.py, src/run.py
  (new docstrings/comments only), .github/workflows/sweep.yml (rewritten comment block only).
- **Commit:** `eb7f429`

### Read-tool environment hiccup (not a deviation, noted for the record)

Early in the session, Read calls on README.md/DEFERRED.md/requirements.txt/.gitignore were denied
by the environment ("user doesn't want to take this action right now"). The coordinator confirmed
this was a background-agent auto-deny artifact and supplied exact file contents inline. Read worked
normally for the rest of the session (including full reads of those same four files once actually
attempted again), so no workaround was needed beyond the coordinator's supplied content for the
first Write attempt.

## Known Stubs

None. Every reporter module is fully wired into `src/run.py` and exercised by either the unit suite
or a live dry-run.

## Threat Flags

None beyond the plan's own threat register (T-DES-01 through T-DES-08), which this plan's tests and
implementation directly address (see behavior tests for escaped `<script>` in Telegram and the HTML
report, and the UNDELIVERED/status-code-only logging in `telegram.py`).

## Self-Check

- FOUND: reporters/evidence.py
- FOUND: reporters/telegram.py
- FOUND: reporters/alert_queue.py
- FOUND: reporters/screenshot.py
- FOUND: reporters/html_report.py
- FOUND: tests/test_evidence.py
- FOUND: tests/test_telegram_format.py
- FOUND: tests/test_alert_queue.py
- FOUND: tests/test_html_report.py
- FOUND commit 26796b4
- FOUND commit 8a5657e
- FOUND commit eb7f429
- 63/63 tests passing at time of writing

## Self-Check: PASSED
