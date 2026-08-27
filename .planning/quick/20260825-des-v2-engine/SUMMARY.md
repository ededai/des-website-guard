---
task: Des v2 engine
date: 2026-08-25
status: complete
spec: SPEC-V2.md
---

# Des v2 engine, wave 1

## Landed

| Module | What it does | Tests |
|---|---|---|
| `des2/models.py` | pinned contracts. `Finding.alertable()` requires BOTH a clean re-test and hard evidence | (used by all) |
| `des2/baseline.py` | loss-only fingerprint + diff. Gains auto-rebaseline, losses become findings | 21 |
| `des2/verify.py` | the proof pass: reproduce in a clean context, or be logged not sent | 6 |
| `des2/report.py` | bug log with status, owner routing, alert / could-not-run / weekly heartbeat | 12 |
| `des2/config.py` + `des2/discover.py` + `sites/trw.v2.yaml` | bounded daily set: changed pages + one page per template | 17 |
| `des2/checks_break.py` + `des2/gates.py` | breakage checks; crawl-health, mass-finding and abort gates | 32 |
| `des2/checks_layout.py` | measured layout defects | 14 |

102 tests, all green. Verified against the live site, not just fakes.

## What the live run taught us

Unit tests with fake pages passed while the layout checks were still wrong.
Running them against therightworkshop.com exposed four false-positive classes
(CSS default `text-overflow: clip`, screen-reader-only text, designed
truncations, and stretched overlay links), all now fixed. Desktop is clean
across four pages.

## Wave 2 (2026-08-27): it runs

`des2/fetch.py` opens pages and extracts the counts the baseline compares.
`des2/run.py` is the sweep itself: discover, visit, gate, check, baseline,
verify, reconcile, report. `.github/workflows/des-v2.yml` runs it daily and
fully on Mondays, persists baselines back to the repo, and alarms separately if
the guard itself could not run.

Proven live, end to end:
- first run announced nothing and wrote baselines (correct: it does not yet
  know what the pages should look like)
- a tampered baseline (body cut 80%, byline removed) was caught on all three
  pages with before/after numbers, routed to Bryan
- repeat runs did NOT re-alert the same open findings
- findings escalate at 7 days; the heartbeat admits the standing backlog
- three consecutive runs on the healthy site produced zero alerts

Two more false positives were found only by running it for real, both fixed:
mega-menu and carousel elements overlap by design, and widgets caught
mid-animation overlap momentarily (this one FLICKERED between sweeps, which
reads as a new fault each time).

Alerts stay OFF behind the `DES_V2_ALERTS` repo variable until a week of quiet
runs proves it.

## Corrections made 2026-08-27 (see also the same-dated commits)

Ed compared the build against advice from an earlier session and was right on
every count:
1. Coverage was bounded to save Actions minutes on a PUBLIC repo where minutes
   are free. The constraint belonged to Cole's private repo. Now sweeps
   everything.
2. Findings went silent forever once reported. Now escalate at 7 and 14 days.
3. Only two viewports. Now 320, 390, 768, 1440.
4. Caused by 1: whole-site sweeping would have sent ~2,400 tap-target findings
   on day one. Baselines now separate pre-existing design from new defects.

## Still open

1. `des2/fetch.py` and `des2/run.py`: the orchestrator that opens one browser,
   walks the daily set, runs the battery, verifies, reconciles and reports.
2. Daily and weekly workflows, silent at first so baselines can form.
3. Layout findings need the pre-existing versus new distinction. Right now
   `tap_target_small` fires on shared chrome (36x36 social buttons, 32x16
   carousel dots) on every phone page. Those are longstanding design, not
   today's regression, so they belong in the backlog rather than on Ed's phone.
   Fix lands with the orchestrator: record layout defect keys in the baseline,
   alert only on ones that are new.
4. AURA config, after TRW is trusted.
5. Move checks before 1 Oct: old postcode must vanish, new unit must appear.

## Note on delegation

Four subagents were spawned (one Opus on the core, three Sonnet). All four died
to environment faults: the machine slept twice, and a watchdog stalled two
more. Two left usable modules behind (`baseline.py`, `config.py`); the rest was
finished directly. Two of the tests written for the surviving modules were
wrong rather than the code, and are noted in their commits.
