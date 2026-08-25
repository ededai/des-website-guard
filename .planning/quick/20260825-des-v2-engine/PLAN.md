---
task: Des v2 engine
date: 2026-08-25
spec: SPEC-V2.md
status: in-progress
---

# Des v2 engine (wave 1)

Builds the engine agreed in SPEC-V2.md. Wave 1 is everything needed to run a
silent sweep that produces evidence-backed findings. Alerting stays off until
baselines exist, which the model requires anyway.

## Contracts (pinned, do not fork)

`des2/models.py` already exists and is the single source of types:
`Finding`, `Evidence`, `Fingerprint`, `Observation`, `VIEWPORTS`, `owner_for`.

Two invariants every module must respect:
1. `Finding.alertable()` is the only gate to Ed. It requires BOTH
   `reproduced=True` (set only by the verify pass) and hard evidence.
2. Standards are only findings when LOST. A page that never had a byline is
   backlog, never an alarm.

## File ownership (parallel-safe)

| Module | Owns | Must not touch |
|---|---|---|
| `des2/baseline.py` | fingerprint capture + loss-only diff | anything else |
| `des2/checks_layout.py` | measured geometry checks | anything else |
| `des2/checks_break.py` + `des2/gates.py` | breakage checks + inherited gates | anything else |
| `des2/config.py` + `des2/discover.py` | site config + URL selection | anything else |

Integration modules (`fetch.py`, `verify.py`, `report.py`, `run.py`) are wired
after wave 1 lands, by the orchestrator.

## Tests

Every module ships tests in `tests/v2/test_<module>.py`, runnable offline (no
network, no live site). Fixtures are HTML strings and fake page objects.

## Non-goals for wave 1

No alerting, no workflows, no AURA config, no screenshot diffing.
