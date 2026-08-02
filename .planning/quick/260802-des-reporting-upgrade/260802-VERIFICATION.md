---
phase: quick/260802-des-reporting-upgrade
verified: 2026-08-02T04:45:00Z
status: gaps_found
score: 11/12 must-haves verified (6/6 truths, 5/6 artifacts, 5/5 key links)
overrides_applied: 0
re_verification:
  previous_status: none
  previous_score: n/a
gaps:
  - truth: "A HIGH finding raised at 03:00 SGT is still delivered at 08:00 SGT even though the Actions runner that found it is long gone"
    status: partial
    reason: "The must-have artifact alert-queue.jsonl does not exist and is not tracked in git. The queue mechanism itself is verified working, but the committed bootstrap state the plan required is absent, which leaves a narrow record-loss race in sweep.yml's merge step."
    artifacts:
      - path: "alert-queue.jsonl"
        issue: "File does not exist; `git ls-files alert-queue.jsonl` returns nothing. Plan must_haves lists it as a required artifact providing 'Committed queue state surviving between Actions runs'."
    missing:
      - "Create and commit an empty alert-queue.jsonl so the tracked file is always present at checkout"
      - "With the file always present, `[ -f alert-queue.jsonl ]` in sweep.yml is always true, so the job copy always exists and merge_bug_log.py can never drop this site's own unsent records when --job is missing"
  - truth: "Every sweep leaves one self-contained HTML report in reports/<site>/ that opens offline with screenshots visible"
    status: partial
    reason: "reports/ is untracked in the repo. Verified empirically that `git add -A reports/` exits 128 with `fatal: pathspec 'reports/' did not match any files` when the directory does not exist. sweep.yml runs that command unconditionally inside the retry loop under `if: always()`, so a sweep that dies before html_report.build fails the commit step and loses that run's bug-log and alert-queue changes."
    artifacts:
      - path: ".github/workflows/sweep.yml"
        issue: "Line 124 `git add -A reports/` has no existence guard; the early-exit guard on line 100 does not protect it because bug-log.jsonl changes alone are enough to reach it"
    missing:
      - "Commit reports/.gitkeep (or seed reports/trw/ and reports/aura/) so the directory always exists at checkout"
      - "Or guard the add: `[ -d reports ] && git add -A reports/`"
human_verification:
  - test: "Run plan Task 4 step 8: source the bot credentials and send a format_critical smoke message, then open the TRW Telegram chat"
    expected: "Bold wrench-emoji [DES] CRITICAL header, a working 'Full sweep report' link, &lt;script&gt; rendered as literal escaped text, message under 4096 chars"
    why_human: "Requires an actual Telegram API send and reading the rendered result on a phone. The verifier is explicitly forbidden from sending Telegram messages, and Telegram's HTML parser behaviour cannot be simulated locally."
  - test: "Open a generated HTML report that contains at least one real critical/high finding and look at it"
    expected: "What-changed block at the top, screenshots showing the actual rendered page (not a blank cream rectangle), no raw HTML tags or dict braces in evidence text"
    why_human: "Visual sign-off. TRW's live pages are currently clean, so no natural finding-bearing report exists in reports/. The verifier confirmed the underlying capture+embed path with a real live screenshot in an isolated scratchpad, but a production report with findings has not been eyeballed."
  - test: "Observe the first real Actions run's 'Commit bug log, alert queue and report' step"
    expected: "Step succeeds, commits bug-log.jsonl + alert-queue.jsonl + reports/<site>/<ts>-<tier>.html in one commit"
    why_human: "Requires a live GitHub Actions run against the pushed commits; cannot be exercised locally."
---

# Quick Task 260802: Des Reporting Upgrade Verification Report

**Task Goal:** Upgrade Des's reporting pipeline so a finding is actionable from the phone: screenshot evidence on critical alerts, one self-contained HTML report per sweep linked from Telegram, HTML-formatted per-site messages, a HIGH queue that survives between Actions runs and flushes at 08:00 SGT, and a systemic end to the evidence-dict repr leak.

**Verified:** 2026-08-02T04:45:00Z
**Status:** gaps_found
**Re-verification:** No, initial verification
**Commits reviewed:** 26796b4, 8a5657e, eb7f429 (local, unpushed)

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A CRITICAL finding arrives in Telegram as a photo of the failing page with a readable caption | VERIFIED (code path); live render pending human | `src/run.py:347-351` routes critical to `telegram.send_photo(shot, format_critical(finding, report_url))` when a shot exists. `telegram.py:123-149` POSTs multipart `sendPhoto` with `files={"photo": fh}`, caption capped at `MAX_CAPTION=1024` on a line boundary, remainder sent as a follow-up. Live capture verified: `screenshot.capture_alert_shot` against therightworkshop.com produced a real 780x1688 JPEG (81,010 bytes, 29,844 distinct colours, top-colour share 0.421) that I opened and visually confirmed shows the rendered TRW mobile homepage (logo, hamburger, hero, booking form, WhatsApp FAB), not a blank reveal-CSS failure. Missing-file fallback verified: `send_photo("/nonexistent.jpg", caption)` falls through to `send(caption)` and records UNDELIVERED only from the fallback. |
| 2 | Every sweep leaves one self-contained HTML report in reports/<site>/ that opens offline with screenshots visible | VERIFIED | Live dry-run `--site=trw --tier=critical --dry-run --limit 2 --delay 2` exited 0 and wrote `reports/trw/20260802-043702-critical.html` plus the blob URL, no traceback. Isolated build with the real JPEG above produced a 109,688-byte file whose embedded base64 decodes byte-identically to the source JPEG (81,010 bytes), with zero `src="reports/`, zero `src=".des-shots`, and zero `cdn|fonts|unpkg` references. Zero-findings build (1,158 bytes) renders "No findings this sweep." rather than breaking. |
| 3 | Every Telegram message starts with a bold site emoji header and the [DES] prefix, renders as HTML, and never exceeds 4096 chars | VERIFIED | `_chunks` exercised at 4095/4096/4097/5000/12000 chars and on an 80x200 multiline body and a 2000-line body: max chunk length never exceeded 4096; 12000-char body returned exactly 3 chunks; source lines round-tripped byte-identically after stripping the continuation prefix; every chunk after the first starts with `[DES] (cont. i/n)`. `format_critical` on a TRW finding starts with `<b>\U0001F527 [DES] CRITICAL`, AURA with `<b>\U0001F43E [DES]`, unknown site with `<b>\U0001F310`. A `<script>` title renders as `&lt;script&gt;` with no raw tag. `report_url` renders as `<a href="https://x/y">`. |
| 4 | A HIGH finding raised at 03:00 SGT is still delivered at 08:00 SGT even though the Actions runner that found it is long gone | VERIFIED (mechanism); see gap on the committed bootstrap file | `in_window` boundaries: 03:00 F, 07:59 F, 08:00 T, 21:59 T, 22:00 F, 23:00 F. `flush_due` at 03:00 sent 0 and left the file byte-identical (`open(qp,'rb').read()==before` True). At 08:00 it sent both TRW records and left the AURA record untouched and in place. A `send_fn` returning False retained that record. A record whose screenshot file is gone routes to `send_fn` (text) rather than `send_photo_fn`; a record whose file exists routes to `send_photo_fn`. Cadence traced against sweep.yml: weekly `0 22 * * 0` = Mon 06:00 SGT (holds) drains at daily `0 0 * * *` = Mon 08:00 SGT; deep `0 15 1,15 * *` = 23:00 SGT (holds) drains at the next 08:00 SGT. `src/run.py:519-522` calls `flush_due` unconditionally on every non-dry-run sweep. |
| 5 | No finding evidence ever renders as a Python dict repr in Telegram, the bug log, or the HTML report | VERIFIED (forward-looking); legacy residue noted | `src/run.py:266,280,298` route all evidence and details through `evidence.humanize` at the dedupe boundary. `checks/visual.py:187-188` fixed `check_mobile_menu` at source. The `_REPR_MARKERS` backstop was exercised against the two checks that still interpolate raw structures (`check_broken_images` line 81, `check_buttons_clickable` line 227): `humanize("1 broken images: [{'src': '...', 'alt': 'a'}]")` returns `1 broken images: src: ..., alt: a` with no braces or quotes, and a full `dedupe()` round-trip on both shapes produced zero `{'` or `': '` matches. Dry-run log scanned: no repr leak. HTML report scanned: no repr. |
| 6 | README.md and DEFERRED.md describe the pipeline that exists, not the Notion pipeline that was never built | VERIFIED | `notion` count: README.md 0, DEFERRED.md 0, requirements.txt 0, and `grep -rn notion --include=*.py .` returns nothing. Em dash count: README.md 0, DEFERRED.md 0. Cadence table crons (`0 0 * * *`, `0 22 * * 0`, `0 15 1,15 * *`) match sweep.yml exactly. README severity routing, Reporting module list, and Reports section describe the shipped modules. DEFERRED sections 1 and 6 marked DONE with dates; Notion sections and the baseline-screenshot step removed; per-cadence coverage map retained. |

**Score:** 6/6 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `reporters/evidence.py` | humanize() + escape_html() | VERIFIED | 57 lines. Both exports present and imported by telegram.py, html_report.py, visual.py, run.py. Depth-aware dict/list formatting, 1800-char truncation, whitespace collapse, `_REPR_MARKERS` backstop all exercised. |
| `reporters/telegram.py` | HTML send, 4096 split, sendPhoto, emoji headers | VERIFIED | 218 lines. All 5 exports (`send`, `send_photo`, `format_critical`, `format_high`, `format_digest`) present and called from run.py and alert_queue.py. |
| `reporters/alert_queue.py` | Cross-run HIGH queue with 08:00 SGT window | VERIFIED | 104 lines. All 4 exports (`enqueue`, `flush_due`, `in_window`, `QUEUE_PATH`) present. Record shape confirmed: check_id, queued_at, screenshot, severity, site, text. |
| `reporters/screenshot.py` | Reveal-safe capped JPEG capture | VERIFIED | 77 lines. `capture_alert_shot` and `MAX_SHOTS_PER_SWEEP=12` present. `add_style_tag` fires before scroll; `quality=60`; no `full_page=True`. Never-raises contract confirmed: a page object whose `add_style_tag` throws returns None. Cap confirmed: returns None past 12 with a single notice. |
| `reporters/html_report.py` | Self-contained base64 report + diff + prune | VERIFIED | 218 lines. All 4 exports present. `report_rel_path("TRW","critical",ts)` == `reports/trw/20260802-031500-critical.html`; `blob_url` == the expected GitHub blob URL. What-changed classification confirmed for NEW / FIXED (with MTTR) / STILL OPEN, and other-site records correctly excluded. `prune` with 11 files keep=3 deleted the 8 oldest and kept the 3 newest; no off-by-one; missing directory and keep>=count return []. |
| `alert-queue.jsonl` | Committed queue state surviving between Actions runs | MISSING | `git ls-files alert-queue.jsonl` returns nothing; the file does not exist on disk. See gap 1. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/run.py::dedupe` | `reporters/evidence.humanize` | evidence coercion at the pipeline boundary | WIRED | Lines 266, 280, 298. Replaces the previous `str(f.get("evidence",""))`. |
| `src/run.py::route` | `reporters/telegram.send_photo` | critical alert with screenshot | WIRED | Line 349, guarded by `shot` with a `telegram.send` fallback on line 351. |
| `src/run.py::route` | `reporters/alert_queue.enqueue` / `flush_due` | high severity queueing | WIRED | `enqueue` line 353 (high branch, no immediate send), `flush_due` line 520 (after digests, every non-dry-run sweep). |
| `src/run.py::main` | `reporters/html_report.build` | end-of-sweep report generation | WIRED | Line 539, built last, including in dry-run. Report identity computed at line 403-405 before any alert is routed, so `report_url` is available to every formatter. |
| `.github/workflows/sweep.yml` | `reporters/merge_bug_log.py` | record-ownership merge of alert-queue.jsonl | WIRED | Lines 112-116 (bug-log.jsonl) and 117-121 (alert-queue.jsonl), both with the same `--site "$DES_SITE"`, both inside the retry loop after `git reset --hard`. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `reporters/screenshot.py` | JPEG bytes on disk | live Playwright `page.screenshot(type="jpeg", quality=60)` after `add_style_tag(REVEAL_FORCE_CSS)` | Yes: 81,010-byte 780x1688 JPEG, 29,844 distinct colours, visually confirmed as the rendered TRW mobile homepage | FLOWING |
| `reporters/html_report.py` | `img src="data:image/jpeg;base64,..."` | `_b64_img(path)` reading the file above | Yes: decoded base64 is byte-identical to the source JPEG (81,010 == 81,010) | FLOWING |
| `reporters/html_report.py` | what-changed rows | `bug_log._load()` filtered by site | Yes: classified real NEW / FIXED (MTTR 12.5h) / STILL OPEN records and excluded the other site's record | FLOWING |
| `reporters/alert_queue.py` | queued records | `enqueue` writes, `flush_due` reads and rewrites | Yes: full JSONL round-trip with site isolation and failed-send retention | FLOWING |
| `reporters/telegram.py` | message text | `format_*` over deduped, humanized findings | Yes: escaped, emoji-headed, chunk-safe output confirmed on real finding shapes | FLOWING |
| `alert-queue.jsonl` | committed queue state | n/a | No file exists | DISCONNECTED (see gap 1) |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite | `.venv/bin/python -m pytest tests/ -q` | `63 passed in 0.18s` | PASS |
| Crawl-guard regression gate | `git diff --stat 4f8e6e3 -- src/crawl_guard.py` | empty | PASS |
| Live dry-run end-to-end | `.venv/bin/python -m src.run --site=trw --tier=critical --dry-run --limit 2 --delay 2` | exit 0; `DES: scanning 2 URLs on 1 viewports`, `DES: 0 unique findings`, `DES: report .../reports/trw/20260802-043702-critical.html`, `DES: report url https://github.com/ededai/...`; no traceback | PASS |
| Evidence-leak gate on dry-run log | `grep -nE "\{'\|': '" dryrun.log` | no matches | PASS |
| Report self-containment | grep for relative src and external CDN in the produced report | 0 and 0 | PASS |
| sweep.yml valid YAML | `yaml.safe_load(...)` | parses; jobs `['sweep']`; `permissions {'contents':'write'}`; `concurrency {'group':'des-sweep','cancel-in-progress':False}`; `max-parallel: 1`; 6 steps in order | PASS |
| Workflow merges both JSONL files | grep `merge_bug_log.py` in sweep.yml | 2 invocations (lines 112, 117) plus 1 comment mention | PASS |
| Prune staged before commit | grep `html_report.py --prune` and `git add -A reports/` | prune line 122, `git add -A reports/` line 124, both inside the retry loop after `reset --hard` so deletions land in the same commit | PASS |
| Retry loop and permissions untouched | grep `for i in 1 2 3 4 5`, permissions, concurrency | 5-attempt loop present; `contents: write` only; `group: des-sweep`; `max-parallel: 1` | PASS |
| Telegram token contract | grep telegram.py for env names and log content | Only `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`; no `TG_BOT_TOKEN`; 2 `UNDELIVERED.append` sites; no print interpolates the exception object or the request URL | PASS |
| UNDELIVERED fail-loud path | read `src/run.py:545-547` | `if telegram.UNDELIVERED and not args.dry_run: print(...); sys.exit(3)` unchanged from 4f8e6e3, still last in main() | PASS |
| route() medium/low semantics | full diff of `src/run.py` vs 4f8e6e3 | Only the critical and high branches changed. Medium/low still fall through to `send_digests`, which retains the mute set, the deep-tier-only low gate, and the digest period mapping unchanged | PASS |
| `git add -A reports/` on a missing directory | reproduced in a scratch git repo | `fatal: pathspec 'reports/' did not match any files`, exit 128 | FAIL (see gap 2) |
| `git status --porcelain -- <missing paths>` | reproduced in a scratch git repo | no output, exit 0 (does not error) | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| D-01 | 260802-PLAN | Screenshot evidence on critical/high | SATISFIED | `reporters/screenshot.py` wired at `src/run.py:237-242`; real capture verified |
| D-02 | 260802-PLAN | Self-contained HTML report per sweep | SATISFIED | `reporters/html_report.py` wired at `src/run.py:539`; live dry-run produced one |
| D-03 | 260802-PLAN | HTML-formatted Telegram with splitting and UNDELIVERED semantics | SATISFIED | `_chunks` verified at all boundaries; UNDELIVERED contract intact |
| D-04 | 260802-PLAN | Cross-run HIGH queue flushed 08:00-22:00 SGT | PARTIAL | Mechanism verified; committed bootstrap file absent (gap 1) |
| C-EVID | 260802-PLAN | Systemic end to dict-repr evidence | SATISFIED | Boundary humanize plus source fix plus marker backstop, all exercised |
| C-DOCS | 260802-PLAN | Docs describe reality | SATISFIED | Zero Notion references; cadence table matches crons |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `.github/workflows/sweep.yml` | 124 | `git add -A reports/` with no existence guard | Warning | Exits 128 when reports/ does not exist. reports/ is currently untracked, so the first Actions run that reaches the commit step without producing a report fails the step and loses that run's bug-log and alert-queue changes. |
| repo root | n/a | `alert-queue.jsonl` absent and untracked | Warning | Must-have artifact missing. Also leaves a narrow race: if a run's checkout SHA predates the commit that first added the file and that run produces no HIGH, `[ -f alert-queue.jsonl ]` is false, no job copy is made, and `merge_bug_log.merge(site, origin, [])` drops this site's own unsent records from origin. |
| `reporters/merge_bug_log.py` | 45 | `_sort_key` uses `first_seen`, which alert-queue records do not carry (they use `queued_at`) | Info | All queue records sort under an empty first key. Python's stable sort preserves relative order, so no data is lost, but the committed queue is not chronologically ordered. |
| `reporters/html_report.py` | 39-44 | `_b64_img` catches only `OSError`; `Path(None)` raises `TypeError` | Info | Confirmed: `build(..., screenshots=[None])` raises. Unreachable from the current pipeline because `dedupe` truthiness-guards the append and `route` uses `(… or [None])[0]` only for the local variable, but it is a latent crash in the last step of the sweep, after alerts have already been sent. |
| `bug-log.jsonl` | 9 records | Legacy dict-repr evidence from before this change (`{'issue': 'menu_no_autoclose'}`, `1 broken images: [{'src': ...` ) | Info | Not backfilled. These records never reach Telegram or the report body (what-changed renders only title and check_id), but the committed audit trail still shows the old repr. Truth 5 holds for everything written from now on. |
| `reporters/telegram.py` | 160, 182, 201 | Em dash in `CRITICAL — {title}` / `HIGH — {title}` / digest header | Info | Deviates from the plan's literal `CRITICAL - {title}` and from the global no-em-dash writing rule. The SUMMARY documents this as an intentional decision (pre-existing format, plan-mandated shape). Flagged for Ed's call, not treated as a gap. |
| `src/run.py` | 97 | `capture=True` parameter added to `render_and_check` but never passed as False | Info | Dead knob; harmless. |

### Human Verification Required

#### 1. Live Telegram render smoke

**Test:** Source the bot credentials and run plan Task 4 step 8 (`telegram.send(telegram.format_critical(f, report_url=...))` with a `<script>` in the title), then open the TRW chat.
**Expected:** Bold wrench-emoji `[DES] CRITICAL` header, a working "Full sweep report" link, `&lt;script&gt;` shown as literal text rather than a rendered tag.
**Why human:** Requires an actual Telegram API send, which this verification run is forbidden from performing, and Telegram's HTML parser cannot be simulated locally.

#### 2. Visual sign-off on a finding-bearing report

**Test:** Open a report that contains at least one real critical or high finding.
**Expected:** What-changed block at the top, screenshots showing the actual rendered page, no raw HTML tags or dict braces in evidence text.
**Why human:** TRW's live pages are currently clean, so no natural finding-bearing report exists under `reports/`. The verifier confirmed the capture-and-embed path with a real live screenshot in an isolated scratchpad (and visually confirmed the JPEG is a real render), but a production report containing findings has not been eyeballed.

#### 3. First live Actions commit step

**Test:** Watch the "Commit bug log, alert queue and report" step on the first scheduled run after these commits are pushed.
**Expected:** Step succeeds and commits bug-log.jsonl, alert-queue.jsonl and one new report in a single commit.
**Why human:** Requires a live GitHub Actions run; cannot be exercised locally.

### Gaps Summary

All six observable truths are achieved in code, and I verified them by execution rather than by reading the SUMMARY: 63/63 tests pass, the crawl-guard regression gate is clean, a live dry-run against TRW exits 0 with a real report and blob URL, chunking is provably safe at every 4096 boundary I could construct, the queue's window and site-isolation semantics hold at every edge, and a real live screenshot of therightworkshop.com round-trips byte-identically into a self-contained report that I visually confirmed is a genuine page render, not a blank reveal-CSS failure.

The two gaps are the same root cause and neither is a logic defect: **the repository state was never seeded.** Both `alert-queue.jsonl` and `reports/` are expected by `sweep.yml` to exist at checkout, and neither is tracked. The plan explicitly listed `alert-queue.jsonl` as a required artifact, and I confirmed by reproduction that `git add -A reports/` hard-fails with exit 128 on a missing directory, which would take down the `if: always()` commit step and discard that run's bug-log and alert-queue writes. Both close with one commit: add an empty `alert-queue.jsonl` and a `reports/.gitkeep`, or add an existence guard to the workflow's add.

Everything else recorded above is informational: a cosmetic ordering quirk when `merge_bug_log` sorts queue records that have no `first_seen`, an unreachable `TypeError` if a `None` ever entered a `screenshots` list, nine legacy bug-log records that predate the sanitizer, and the em dash the executor deliberately kept in the Telegram header format. None of those block the goal.

---

_Verified: 2026-08-02T04:45:00Z_
_Verifier: Claude (gsd-verifier)_
