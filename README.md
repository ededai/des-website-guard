# Des — Website Guard

Proactive site-wide post-publish QA sweeper for all of Ed's websites (TRW, AURA, future). Strict report-only.

Identity: `~/.claude/projects/-Users-admin-Desktop-Claude-folder/memory/user_des_identity.md`
Skill: `~/.claude/skills/des-website-guard/SKILL.md`

## What it does

- Crawls each registered site's `sitemap.xml`
- Renders every URL across 5 viewports (desktop / laptop / tablet / iPhone / Android)
- Runs a battery of checks: visual / functional (button clicks) / content / SEO / CWV / TRW-specific
- De-duplicates findings (same bug across N pages = 1 report with URL list)
- Routes by severity:
  - **Critical** → Telegram (24/7) + Claude session + Notion log
  - **High** → Telegram (queued to 08:00 SGT) + Claude session + Notion log
  - **Medium / Low** → Notion log only
- Auto-closes bugs that no longer reproduce on next sweep; bumps severity on re-opens

## Cadence (SGT)

| Cadence | Cron (UTC) | Coverage |
|---|---|---|
| Daily critical | `0 0 * * *` | Top 20 pages, critical only |
| Weekly critical | `0 22 * * 0` (Mon 06:00 SGT) | Full sitemap, critical + high |
| Bi-weekly deep | `0 15 */14 * *` (Sun 23:00 SGT) | Full sitemap, all viewports, all checks |

## Sites registered

See `sites/` — one config file per site.

| Site | Config | In-charge |
|---|---|---|
| TRW | `sites/trw.yaml` | Bryan (via Codi) |
| AURA | `sites/aura.yaml` (placeholder until rebuild ~2026-05-06) | Codi |

## Local dev

```bash
cd /Users/admin/the-right-workshop/des-website-guard
pip install -r requirements.txt
playwright install chromium
python -m src.run --site=trw --tier=critical --dry-run
```

## Hosting

GitHub Actions cron in `ededai/des-website-guard`. Same model as `ededai/trw-ig-scheduler`. Mac can be off.

Secrets required:
- `TELEGRAM_BOT_TOKEN` (reuse TRW bot)
- `TELEGRAM_CHAT_ID` (reuse TRW chat)
- `NOTION_TOKEN`
- `NOTION_DES_DB_ID` (Des — Website Bug Log)

## Severity routing

Authoritative definitions live in `~/.claude/skills/des-website-guard/SKILL.md`. Edit there, not here.

## Reporting templates

`reporters/templates/` — Telegram + Notion + Claude session formats. Don't change without updating SKILL.md.

## Status

Scaffolded 2026-04-30. First sweep pending repo creation + secret config + first-run baseline.
