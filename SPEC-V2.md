# Des v2 — specification

Agreed with Ed on 2026-08-25 by interrogation, question by question. This file
is the contract. If a build decision is not covered here, it is a gap in the
spec, not a licence to improvise.

Ed's own words for the goal: "make sure that the whole website does not have
bad pages or bugs or things to make the page look off. Basically to make sure
everything is in order."

## 1. What Des is

A REGRESSION GUARD. It catches things that were fine and are now broken. It is
not a quality auditor grading the site against an ideal, because every
false-positive storm in this project's history came from a check judging
quality rather than change, and an alarm nobody believes is worse than no alarm.

## 2. The core rule: alarm on loss

Des remembers what each page had when it was last healthy.

- Something DISAPPEARS or breaks: nav or footer gone, article body collapses,
  images that used to load now 404, menu stops opening. That is a finding.
- A page GAINS content: silently accepted as the new baseline. Ed publishes
  constantly and must never be asked to approve his own work.

Consequence: Des is necessarily quiet on first contact with a page, because it
has nothing to compare against yet. The shakedown period is inherent, not
optional.

## 3. Coverage

CORRECTED 2026-08-27. Daily sweeps THE WHOLE SITE, ordered by value: template
pages first, then recently edited pages, then everything else. Viewports are
320, 390, 768 and 1440; 320 is where layout actually breaks and was previously
untested, as was tablet.

The earlier design checked only changed pages plus a template sample, capped at
40, to save Actions minutes. Wrong constraint from the wrong repo: Cole is
private and metered, this one is public where minutes are free. It bought
nothing and cost the ability to notice a quiet break on an unedited page.

Still true, and the reason templates lead the order: pages edited in the last
48h (WordPress reports this for free), plus one representative page per
template. The template sample is essential, not a
nicety: site-wide chrome is injected by Code Snippet 25, so it can break every
page at once without a single page being "edited".

TRW templates: homepage, service, brand, article, topic hub, COE hub, contact,
about. AURA: its own equivalents in its own config.

Weekly: full sitemap, both sites.

Viewports: desktop 1440 and phone 390. Real mobile emulation with touch, not a
narrow desktop window, or mobile-only bugs never reproduce.

## 4. What counts as a finding

ALWAYS a finding (breakage, regardless of history):
- page does not load, 4xx or 5xx, or a bot challenge served to a real visitor
- first-party resource failing (status >= 400, taken from the NETWORK LOG with
  the exact URL, never from console text)
- images broken or rendering at zero size
- nav or footer missing
- mobile menu does not open, or does not close on link tap
- booking widget or contact path dead
- genuine uncaught JS errors (resource-load lines are excluded by construction)
- LAYOUT DEFECTS, measured, not eyeballed: text overflowing its container,
  content wider than the viewport (horizontal scroll), elements overlapping or
  misaligned, headings or buttons clipped, tap targets under the minimum on
  phone

ONLY when LOST (page had it, now does not):
- meta description, title, canonical, H1, byline, breadcrumb, alt text,
  address unit number, house style rules (e.g. no em dashes)

Pages that never had these are NOT alerts. They go to a quiet backlog file for
Bryan to work through.

## 5. The proof bar

Nothing reaches Ed's phone without proof, because Ed chose that ANY confirmed
breakage alerts, so "confirmed" is carrying the entire load.

A finding may only alert if BOTH hold:
1. It reproduces on an immediate re-test in a clean browser context.
2. It can name its evidence: exact URL, exact element or resource, HTTP status
   or measured numbers, and a screenshot.

A finding that cannot produce that is logged for Codi to investigate and is
never sent. This is the direct fix for the beacon "404" with no URL that cost a
morning of investigating a healthy site.

Inherited gates that stay (they were paid for in incidents, see
feedback_des_audit_no_handler_false_positives):
- crawl-health gate: a non-200 or challenge page skips every DOM check
- mass-finding gate: one check firing on more than half the swept pages is a
  suspected checker defect, reported once, never as a storm
- untappable-control rule: a 0x0 or hidden element is never the real control
- behavioural verification before claiming "no handler" or "no output"

## 6. What Des does about it

REPORT ONLY. Des never writes to either site. Its value is being believed, and
a guard that edits can cause the regression it exists to catch.

Each finding carries an owner: Bryan for TRW content and SEO, Cole for COE
data, Codi for chrome and infrastructure, Dom for media. Findings persist in a
bug log with status (open, fixed, reopened) so nothing is lost and repeats do
not nag.

### Re-alert policy, and escalation by age (added 2026-08-27)

A finding alerts when it is NEW or when it REOPENS. One already open and
unchanged stays in the log and does not ping again, because a guard repeating
yesterday's news is one you learn to ignore.

But reporting once and then going silent forever lets something stay broken and
unmentioned indefinitely. So an unfixed finding earns another mention at 7 days
and a harder one at 14. Twice, by age, never by repetition. The weekly
heartbeat carries the count of known-open findings, so an all-clear can never
hide a standing backlog.

### Layout: pre-existing versus new

Longstanding design (36x36 social icons, 32x16 carousel dots) is not today's
regression. Each baseline remembers which layout defects a page already had,
and only new ones alert; the rest are backlog. Without this, sweeping every
page across three touch viewports would have sent roughly 2,400 findings on the
first run.

## 7. Cadence and cost

Daily targeted sweep, weekly full sweep. No publish-triggered runs.

Cost discipline, CORRECTED 2026-08-27. This repo is public: Actions minutes are
free and unmetered and never touch the private quota Cole's repo exhausted. The
scarce resource is Ed's attention, not compute, so Des checks thoroughly and
reports sparingly rather than the reverse. Efficiency below is about wall-clock
time, not budget:
- The repo is PUBLIC, so its Actions minutes do not touch the 2,000-minute
  private quota. Cost pressure is real but not billed the same way.
- Plain HTTP for anything that does not need a browser. A browser is launched
  once per viewport per sweep, not per page.
- One browser context reused across pages, with a polite pause between hosts.
- The daily sweep is bounded by design (changed pages plus a template sample),
  not by the size of the sitemap.
- Hard ceiling: if a sweep exceeds its budget, it reports what it covered and
  says so, rather than running long and silently.

## 8. Architecture

Same public repo, new engine. Harvest what earned its place: the three routing
gates, crawl-blocked detection, the network-log resource check, the mobile
menu selector contract (both chrome generations), and their tests. Delete the
audit-everything-against-standards shape that kept pulling toward noise.

The repo URL must stay alive: Cole's qa-coe-chart workflow downloads
qa_coe_chart.py from its raw URL.

Des does NOT move into Cole's repo. Cole's is private and metered, and a
watcher living inside the thing it watches can be silenced by the same broken
deploy.

## 9. Silence must be meaningful

Weekly heartbeat on a pass. If a week goes by with no message at all, the guard
is not running, whatever the reason. This is the lesson from 2026-08-20 to -24,
when billing stopped every workflow and the silence read exactly like all clear.

A run that dies before the checking code loads (import error, failed install,
runner fault) must still alarm, saying "could not run", never "site is wrong".

## 10. The move, 1 October 2026

Des must be live and past its shakedown before the flip, so there is a proven
picture of a healthy site to compare against.

Loss-only means the address swap itself stays quiet, which is correct: it is a
change, not a loss. A dropped footer or a dead map image on the same day is
caught.

Temporary move checks, on from the flip: the old postcode 417883 must appear
nowhere, and the new unit must appear everywhere it should. Retire them once
the move is settled.

## 11. Rollout

1. Build the engine, port the harvested checks and tests, TRW config first.
2. Run silent for about a week to establish baselines and prove behaviour.
3. Turn alerts on for TRW.
4. Add the AURA config, same engine, once TRW is trusted.
5. Add the temporary move checks before 1 October.

## 12. Non-goals

- No auto-fixing.
- No pixel diffing or AI vision review in v2. Layout is judged by measurement.
- No grading pages that were never compliant. That is a backlog, not an alarm.
