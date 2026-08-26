---
task: Des v2 engine
date: 2026-08-25
status: incomplete
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

## Remaining

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
