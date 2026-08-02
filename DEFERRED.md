# Des: Deferred setup steps

## 1. GitHub repo: DONE (2026-07-11)

```
gh repo create ededai/des-website-guard --public --source=. --remote=origin --push
```

Same model as `ededai/trw-ig-scheduler`.

## 2. GitHub Actions secrets

Set these in `gh secret set` or via the UI:

| Secret | Value | Source |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | reuse from `trw-ig-scheduler` | existing TRW bot |
| `TELEGRAM_CHAT_ID` | reuse from `trw-ig-scheduler` | existing TRW chat |

## 3. AURA activation: DONE (2026-07-03)

- Updated `sites/aura.yaml` with `url`, `sitemap`, `canonical_chrome_baseline`, `active: true`
- Assigned `in_charge: Codi`
- First deep sweep triggered manually during the 2026-07-03 audit

## 4. Per-cadence override schedule (optional polish)

Right now all 3 cadences share the same workflow. If we want per-tier coverage maps (different URL caps), edit `src/run.py::main` to read tier-specific configs from `sites/*.yaml`.
