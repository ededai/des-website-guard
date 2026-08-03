# Des: Website Guard

Proactive site-wide post-publish QA sweeper for all of Ed's websites (TRW, AURA, future). Strict report-only.

Identity: `~/.claude/projects/-Users-admin-Desktop-Claude-folder/memory/user_des_identity.md`
Skill: `~/.claude/skills/des-website-guard/SKILL.md`

## What it does

- Crawls each registered site's `sitemap.xml`
- Renders every URL across 5 viewports (desktop / laptop / tablet / iPhone / Android)
- Runs a battery of checks: visual / functional (button clicks) / content / SEO / CWV / TRW-specific
- De-duplicates findings (same bug across N pages = 1 report with URL list)
- Routes by severity:
  - **Critical** → Telegram photo + caption + report link, 24/7
  - **High** → queued in `alert-queue.jsonl`, flushed at 08:00 SGT
  - **Medium** → end-of-sweep Telegram digest
  - **Low** → deep-tier bi-weekly digest
  - **Everything** → `bug-log.jsonl` + the per-sweep HTML report
- Auto-closes bugs that no longer reproduce on next sweep; bumps severity on re-opens

## Cadence (SGT)

| Cadence | Cron (UTC) | Coverage |
|---|---|---|
| Daily critical | `0 0 * * *` | Top 20 pages, critical only |
| Weekly critical | `0 22 * * 0` (Mon 06:00 SGT) | Full sitemap, critical + high |
| Bi-weekly deep | `0 15 1,15 * *` (1st + 15th, 23:00 SGT) | Full sitemap, all viewports, all checks |

## Sites registered

See `sites/` for one config file per site.

| Site | Config | In-charge |
|---|---|---|
| TRW | `sites/trw.yaml` | Bryan (via Codi) |
| AURA | `sites/aura.yaml` | Codi |

## Local dev

```bash
cd /Users/admin/the-right-workshop/des-website-guard
pip install -r requirements.txt
playwright install chromium
python -m src.run --site=trw --tier=critical --dry-run
```

A dry-run prints every finding, its screenshot path (if one was captured), and builds the HTML report without sending anything to Telegram.

## Hosting

GitHub Actions cron in `ededai/des-website-guard`. Same model as `ededai/trw-ig-scheduler`. Mac can be off.

Secrets required:
- `TELEGRAM_BOT_TOKEN` (reuse TRW bot)
- `TELEGRAM_CHAT_ID` (reuse TRW chat)

## Reporting

- `reporters/evidence.py`: the single pipeline-boundary formatter (`humanize()` / `escape_html()`) that turns any check's evidence (string, dict, list) into safe, readable text. No finding evidence reaches Telegram, the bug log, or the HTML report without going through it first.
- `reporters/telegram.py`: HTML-mode sender with per-site emoji headers, 4096-char message splitting, and `sendPhoto` for critical/high alerts with a screenshot.
- `reporters/alert_queue.py`: cross-run persisted HIGH-severity queue (`alert-queue.jsonl`), flushed at 08:00-22:00 SGT so a HIGH found at 03:00 doesn't wake anyone.
- `reporters/screenshot.py`: reveal-safe, capped viewport JPEG capture for critical/high findings during the live sweep.
- `reporters/html_report.py`: builds the self-contained per-sweep HTML report and prunes old ones.
- `reporters/bug_log.py`: the audit-trail JSONL log with a full open/fixed/reopened lifecycle.

## Reports

Every sweep writes one self-contained HTML report to `reports/<site>/<timestamp>-<tier>.html`. It embeds every finding screenshot as base64, so it opens offline with zero external requests. The last 10 reports per site are kept; older ones are pruned in the same commit as the new one. Every Telegram alert links to the report for that sweep.

## Severity routing

Authoritative definitions live in `~/.claude/skills/des-website-guard/SKILL.md`. Edit there, not here.

## False-positive gates (src/run.py `route()`)

Every documented false-positive family (missed JS handlers, id-guessed calculator outputs, CSS console issues miscounted as JS errors, Cloudflare challenge pages judged as broken pages, 0x0 shadow buttons clicked instead of the real control) traced back to the same root cause: a one-shot static check alerting on first sighting, with no reproduce step and no plausibility check against how many pages fired at once. Three structural gates close that off, applied in this order to every critical/high finding, check-agnostic (they cover future checks automatically):

1. **Reproduce-before-alert** — a critical/high finding is re-run against the same URL in a FRESH browser context (fresh page, cleared console errors) within the same sweep before it may alert at that severity (`reproduce_finding()`). If it does not reproduce, it is logged to `bug-log.jsonl` at severity `medium` with `flaky: true` and only ever reaches the end-of-sweep digest — never immediate Telegram.
2. **Mass-finding plausibility gate** — if a check_id fires critical/high on more than 50% of the site's swept pages, Des does not send a per-page or aggregated critical/high alert. It sends ONE medium alert: `suspected checker defect: {check_id} fired on N/M pages, verify checker before trusting`, with 3 sample URLs. History shows every all-pages finding has been a checker bug, never a real site-wide break.
3. **console_errors cross-sweep debounce** — `console_errors` alone has multiple unconfirmed-positive incidents. First sighting for a site logs at its true severity in the bug log (so history/MTTR stay accurate) but delivers as a medium digest item, not an immediate/queued alert. A second CONSECUTIVE sweep with the same check_id+site (still open when the new sweep starts) escalates normally.

Gate order matters: a finding that fails gate 1 never reaches 2 or 3. A reproducing all-pages finding is caught by gate 2 before it can reach gate 3. Only a reproducing, not-mass finding can reach gate 3.

## Status

Both sites sweeping on cron (daily critical, weekly critical+high, bi-weekly deep). `bug-log.jsonl` is the audit trail; the per-sweep HTML report under `reports/` is the visual record.
