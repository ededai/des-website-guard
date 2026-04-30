# Des — Deferred setup steps

Repo + cron + Notion DB + first-run baseline are not yet wired. Codi runs through this checklist when ready.

## 1. GitHub repo

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
| `NOTION_TOKEN` | from Codi's Notion integration | Notion settings → Integrations |
| `NOTION_DES_DB_ID` | new Notion DB id | step 3 below |

## 3. Notion database

Create a new database called **"Des — Website Bug Log"** under Codi's hub with these properties:

| Name | Type |
|---|---|
| Title | title |
| Severity | select (critical / high / medium / low) |
| Status | select (open / fixed / reopened) |
| Site | select (TRW / AURA / ...) |
| InCharge | select (Bryan / AURA / Codi / ...) |
| FirstSeen | date |
| LastSeen | date |
| FixedAt | date |
| URLCount | number |
| URLs | text |
| CheckID | text |
| Evidence | text |
| MTTR_hours | number |

Share it with the Notion integration so the API can write to it. Save the database ID into `NOTION_DES_DB_ID`.

## 4. First-run baseline

After secrets are set, manually trigger a `--tier=deep` run for TRW with `--dry-run` to:
1. Confirm Playwright renders all viewports cleanly
2. Confirm sitemap discovery returns the expected URL count
3. Confirm Telegram + Notion templates render correctly
4. Save initial baseline screenshots into `baselines/trw/<slug>/<viewport>.png`

Then flip off `--dry-run` for live alerts.

## 5. Update Notion ID memory

Once the DB is created, append its ID to `reference_notion_pages.md` so Codi can find it.

## 6. AURA activation

When the AURA rebuild lands (week of 2026-05-06):
- Update `sites/aura.yaml` with `url`, `sitemap`, `canonical_chrome_baseline`, set `active: true`
- Assign `in_charge` (default: Codi until further notice)
- Trigger first deep sweep manually

## 7. Per-cadence override schedule (optional polish)

Right now all 3 cadences share the same workflow. If we want per-tier coverage maps (different URL caps), edit `src/run.py::main` to read tier-specific configs from `sites/*.yaml`.
