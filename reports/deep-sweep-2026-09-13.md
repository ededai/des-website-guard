# Des — TRW deep sweep, 2026-09-13

**Site:** therightworkshop.com
**Tier:** deep (all severities)
**Mode:** report-only dry run. Nothing was fixed. Nothing was sent to Telegram.
**In-charge:** Bryan (via Codi)

## Coverage

| | |
|---|---|
| Sitemap URLs discovered | 211 (`sitemap_index.xml`, both page-sitemap files) |
| URLs rendered | **211 / 211** |
| Viewports | 1440x900 desktop + 390x844 iPhone (2 x 211 = 422 page loads) |
| HTTP status | 422 / 422 returned **200**. Zero 4xx, zero 5xx, zero load errors. |
| Screenshots | `/Users/admin/the-right-workshop/des-website-guard/.des-shots/2026-09-13/` |

### How this sweep was run, and why

The canonical runner was started first, exactly as documented:

```
python -m src.run --site=trw --tier=deep --dry-run
```

It cannot finish. Measured on this machine against the priority-ordered head of the
sitemap (`--limit 45 --delay 1.0`): **38 of 45 URLs in 3 hours 20 minutes**, and slowing —
the last URL alone took over 20 minutes. Extrapolated to all 211 URLs at 5 viewports that
is well past 12 hours, so it never reached the `html_report.build()` step and **this run
produced no HTML report**. It was stopped at 38/45. The newest HTML report in the repo is
still `reports/trw/20260818-005414-critical.html`, for the reason in T-3 below.

Full-sitemap coverage therefore comes from a second pass written for this sweep, which
reproduces the runner's check battery (chrome fingerprints, markers, byline, autop,
meta/title/canonical/h1, JSON-LD, alt text, console + network errors, maroon leak, broken
images) plus the alignment and overflow battery asked for here, at 1440 and 390, holding
one browser context per viewport instead of relaunching a browser per URL. That is the
only reason 211/211 was reachable. Nothing about the checks was relaxed; three of them
were tightened after hand-verifying false positives (see T-1, and the notes under L-2 and
the clipped-text row).

See *Runner health* at the bottom — three defects in the runner itself are why this
matters more than the findings on the site do.

## Severity counts

| Severity | Findings |
|---|---|
| Critical | **0** |
| High | **1** |
| Medium | **4** |
| Low | **4** |
| Tooling (Des itself, not the site) | **3** |

---

## CRITICAL — 0

No HTTP 5xx. No stripped nav or footer. No blank pages. No missing hero images.
No fatal JavaScript. Nothing in this tier.

---

## HIGH — 1

### H-1. Wide tables are clipped on mobile and the cut-off columns cannot be reached

**Severity:** high (skill: *"Mobile/desktop parity broken — content cut off mobile that desktop shows"*)
**Affected:** 5 URLs, phone 390 only
**Check:** `table_hscroll` + `element_overflow`

On phone, several cost/comparison tables render at 342px wide but need 1007px to show
every column. Their wrapper (`div.trw-cost`, `div.trw-table`) is `overflow-x: visible`,
and `html { overflow-x: hidden } / body { overflow-x: clip }` then swallows the excess —
so the page does not scroll sideways and **the wrapper does not scroll either**. The
right-hand columns are simply unreachable on a phone. Desktop shows them fine.

**Confirmed visually.** On `/scrap-car-singapore/` at 390px the table restacks into
cards, and the PARF rebate card's value line reads
`75% down to 50% of the original ARF, with n` — it runs off the right edge of the card
and is cut mid-word. A reader on a phone cannot see the rest of that sentence at all.
Crop: `.des-shots/2026-09-13/scrap-car-singapore--phone390--table_hscroll.jpg`

| URL | table width | content width | columns lost |
|---|---|---|---|
| https://therightworkshop.com/coe-renewal-singapore/ | 342px | 1138px | ~70% |
| https://therightworkshop.com/scrap-car-singapore/ | 342px | 1007px | ~66% |
| https://therightworkshop.com/coe-renewal-car-maintenance-budget-singapore/ | 342px | 837px | ~59% |
| https://therightworkshop.com/singapore-parf-rebate-schedule-and-cap-revised-from-feb-2026/ | 310px | 598px | ~48% |
| https://therightworkshop.com/hybrid-battery-replacement-cost-singapore/ | 342px | 455px | ~25% |

Same root cause, second symptom: on `/coe-renewal-singapore/` the `table.trw-parf`
renders 451px wide inside a `div.trw-tblwrap` that is `overflow-x: visible` on that page,
so it overhangs the 390px viewport by **110px** and the overhang is clipped away.

**The site already has the correct pattern.** On
`/coe-renewal-car-maintenance-budget-singapore/`, `div.trw-tblwrap` is `overflow-x: auto`
and its `table.trw-parf` scrolls properly. The fix is to apply that same wrapper rule to
`.trw-cost` and `.trw-table`, and to make `.trw-tblwrap` consistent across all pages
that use it.

Evidence: `.des-shots/2026-09-13/coe-renewal-singapore--phone390--table_hscroll.jpg`,
`scrap-car-singapore--phone390--table_hscroll.jpg`,
`coe-renewal-singapore--phone390--element_overflow.jpg`,
`coe-renewal-car-maintenance-budget-singapore--phone390--table_hscroll.jpg`,
`singapore-parf-rebate-schedule-and-cap-revised-from-feb-2026--phone390--table_hscroll.jpg`,
`hybrid-battery-replacement-cost-singapore--phone390--table_hscroll.jpg`

---

## MEDIUM — 4

### M-1. Card grids strand a single card alone on the last row

**Severity:** medium (alignment drift)
**Affected:** 26 URLs, both viewports
**Check:** `orphan_card_grid`

Grids of 4, 7, 10 or 13 cards lay out 3-or-4 per row and leave exactly one card by itself
on the final row — the layout reads as unbalanced, which is the "out of alignment" look.
Containers: `div.models-categories` (13 pages), `div.post-grid` (9), `div.trw-grid` (2),
`div.engine-grid` (1), `div.serve-brands` (1).

**Confirmed visually, and there are two distinct symptoms.**

- On the topic hubs the orphan card keeps its column width, leaving two empty columns
  beside it — e.g. `/topics/cooling/` has 4 post cards: three across, then one alone with
  two-thirds of the row blank.
  Crop: `.des-shots/2026-09-13/topics_cooling--desktop1440--orphan_card_grid.jpg`
- On the brand hubs the orphan card **stretches to the full row width**, so it is visibly
  a different size and shape from its siblings — e.g. `/brands/jaguar/` shows SALOON,
  SUV & CROSSOVER and SPORTS as equal thirds, then `CLASSICS · PRE-1990` as one wide
  full-bleed panel underneath.
  Crop: `.des-shots/2026-09-13/brands_jaguar--desktop1440--orphan_card_grid.jpg`

The second symptom is the more noticeable of the two and is the likelier source of Ed's
"out of alignment" impression.

Worst offenders are the brand hubs (`rows 4/1`) and the topic hubs (`rows 3/1`):

```
/about/
/brands/alfa-romeo/            /brands/audi/           /brands/ford/
/brands/honda/                 /brands/jaguar/         /brands/land-rover/
/brands/mazda/                 /brands/mercedes-benz/  /brands/mini/
/brands/mitsubishi/            /brands/nissan/         /brands/porsche/
/brands/suzuki/
/brands/bmw/drivetrain-malfunction-singapore/
/gearbox-oil-change-singapore-cost/
/pre-purchase-car-inspection-singapore/
/topics/congestion/            /topics/cooling/        /topics/deadlines/
/topics/electrical/            /topics/gearbox/        /topics/safety/
/topics/scrap-or-export/       /topics/servicing/      /topics/wheel-alignment/
```

Evidence: `.des-shots/2026-09-13/*--orphan_card_grid.jpg` (one crop per URL, container
scrolled into view).

### M-2. Em / en dashes in body copy

**Severity:** medium (house rule: no em dashes)
**Affected:** 13 URLs
**Check:** `em_dash`

Mostly year ranges rendered with en dashes (`2007–2014`, `W212 · PRE-FACELIFT 2009–2013`)
and price ranges (`SGD 80–180`).

```
/brake-pads-rotors-singapore-when-to-change/
/brake-squeal-judder-singapore/
/brands/mercedes-benz/a-class-cla-common-problems-singapore/
/brands/mercedes-benz/c-class-w205-common-problems-singapore/
/brands/mercedes-benz/e-class-w212-w213-common-problems-singapore/
/brands/mercedes-benz/gearbox-jerking-7g-9g-tronic-singapore/
/car-aircon-not-cold-singapore-fix/
/cat-a-vs-cat-b-singapore/
/cheapest-car-workshop-singapore/
/engine-mounting-singapore/
/independent-workshop-vs-authorised-service-centre-singapore/
/pre-purchase-car-inspection-singapore/
/tyres-battery-singapore-checks-guide/
```

This reopens the `em_dash` bug already open in `bug-log.jsonl` since 2026-08-04 (10 URLs
then, 13 now). Digest-muted per Ed's 2026-07-12 instruction, so it is logged, not pinged.

### M-3. `<title>` over 60 characters

**Severity:** medium
**Affected:** 12 URLs
**Check:** `title_too_long`

The Volvo brand cluster is the bulk of it — the `| The Right Workshop Singapore` suffix
pushes them over on its own.

```
/brands/volvo/aircon-not-cold-singapore/          (65)
/brands/volvo/burning-oil-pcv-singapore/
/brands/volvo/coolant-leak-overheating-singapore/ (75)
/brands/volvo/gearbox-jerking-singapore/          (65)
/brands/volvo/maintenance-cost-singapore/         (84)
/brands/volvo/screen-not-working-singapore/       (72)
/brands/volvo/start-stop-battery-singapore/
/brands/volvo/xc60-years-to-avoid-singapore/
/brands/volvo/xc90-years-to-avoid-singapore/      (66)
/cat-a-coe-august-2026-hits-128501-what-the-surge-tells-us/
/f1-singapore-gp-2026-road-closures-and-transport-guide/
/pothole-kerb-alignment-check-singapore/
```

### M-4. `<meta name="description">` over 160 characters

**Severity:** medium
**Affected:** 7 URLs (169–177 chars)
**Check:** `meta_description_too_long`

```
/brands/volvo/burning-oil-pcv-singapore/
/brands/volvo/coolant-leak-overheating-singapore/
/brands/volvo/maintenance-cost-singapore/
/brands/volvo/start-stop-battery-singapore/
/brands/volvo/xc60-years-to-avoid-singapore/
/cat-a-coe-august-2026-hits-128501-what-the-surge-tells-us/
/f1-singapore-gp-2026-road-closures-and-transport-guide/
```

---

## LOW — 4

### L-1. Pill / chip / breadcrumb rows wrap with one item on the last line

**Severity:** low (cosmetic spacing)
**Affected:** 107 URLs
**Check:** `orphan_pill_row`

`nav.bc` breadcrumbs (76 hits, phone), `div.model-cat-list` (59), `div.tags-row` (24),
`div.meta-strip` (20), plus single hits on `div.year-pills`, `div.coe-arc-pillgroup`,
`div.t-chips`. This is ordinary inline wrapping of variable-width chips, not a broken
grid — separated from M-1 deliberately. Worth a look only if the breadcrumb trail
dropping its last crumb onto its own line on mobile bothers Ed.

Evidence: 4 sample crops, `.des-shots/2026-09-13/*--orphan_pill_row.jpg`.

### L-2. Article hero under 200px tall on mobile

**Severity:** low
**Affected:** 44 URLs, phone 390
**Check:** `hero_short`

`section.article-hero` renders 183px tall on 38 pages, 147–172px on 5 more, and
`section.trw-hero.coe-arc-hero` 153–182px on the COE archive pages. The height is
identical across the whole article template, which points to deliberate design (a compact
article header) rather than a break. Flagged because the sweep was asked to report heroes
under 200px — confirm with Ed whether 183px is the intended article-hero height before
anyone "fixes" it.

Two of the 44 are a detector artifact: `span#cCostHero.cb-val` (30px) on the cost
calculator pages is a value readout whose id contains "Hero", not a hero section.

### L-3. `#7A2A17` rust-red text sits inside the maroon-leak detection band

**Severity:** low — review only, **not** a WordPress.com theme leak
**Affected:** 15 URLs
**Check:** `maroon_leak`

The detector fires on `.dont { background: #FBF1EE; border: 1px solid #EFD5CD; color: #7A2A17 }`
— a hand-authored "Don't" callout box in the page's own stylesheet, plus the same colour
on a few `<p>` elements. That is deliberate design, not the maroon WP.com theme bleeding
through onto buttons or links. Listed so Ed can confirm `#7A2A17` is an approved brand
accent; if it is, it should go on the waiver list in `sites/trw.yaml` so it stops firing.

```
/brake-pads-rotors-singapore-when-to-change/   /brake-squeal-judder-singapore/
/car-aircon-smell-musty-regas-trap/            /car-battery-dead-emergency-singapore/
/car-battery-flat-singapore-warning-signs/     /coe-renewal-singapore/
/driving-singapore-rain-wet-weather/           /ev-hybrid-servicing-independent-workshop-singapore/
/gearbox-making-noise-singapore/               /pass-vicom-inspection-first-try-singapore/
/pothole-kerb-alignment-check-singapore/       /pre-purchase-car-inspection-singapore/
/scrap-car-singapore/                          /tesla-servicing-independent-workshop-singapore/
/what-to-look-for-car-workshop-singapore/
```

### L-4. Carry-forward bugs from earlier sweeps, not re-verified here

These are open in `bug-log.jsonl` and sit outside this sweep's battery. They still need a
targeted recheck:

| check_id | severity | opened | note |
|---|---|---|---|
| `archive_count_mismatch` | high | 2026-07-03 | `/guides/` and `/car-tips/` pill counts vs rendered cards |
| `footer_map_black_box` | medium | 2026-07-03 | footer map embed renders black |
| `carplate_black_box_masking` | medium | 2026-07-03 | homepage review photos use black boxes, house rule is mosaic |
| `slow_lcp` | medium | 2026-07-03 | LCP > 2.5s on 16 pages |
| `long_title` | low | 2026-08-04 | superseded by M-3 above |

---

## Verified clean across all 211 pages

Each of these ran on every URL at both viewports and produced **zero** findings. Detector
coverage is stated so the zeros are not mistaken for a check that never ran.

| Check | Result |
|---|---|
| Page-level horizontal overflow (`documentElement.scrollWidth` vs `innerWidth`) | 0 / 422 page loads overflow |
| Header height | **65px on 211/211 pages, both viewports** — no drift anywhere |
| Breadcrumb top offset | **y=65px on 210/210 pages that carry one** — flush to the header on every canonical-chrome page. Only `/` has no breadcrumb, which is correct (exempt hub) |
| Footer chrome fingerprints | all 5 (`footer-social-btn`, `footer-brand-logo`, `footer-col-title`, `footer-brand-tag`, `footer-grid`) present on 211/211 |
| Footer Saturday hours | `Sat 9am-12:30pm` on **211/211** — no wrong Saturday time anywhere |
| WhatsApp number | **1,494 wa.me links across 211/211 pages, every one on 6589521688** — zero wrong |
| `tel:` links | 6 links across 4 pages, **all `tel:+6589521688`** — zero wrong |
| Old number `98558423` / `9855 8423` | **0 occurrences** in rendered text on any page (detector self-tested against a positive control) |
| Broken images (`naturalWidth === 0`) | 0 |
| Images missing alt text | 0 |
| JavaScript console errors | 0 genuine JS errors on any page |
| Failed same-origin requests | 0 |
| JSON-LD | present on 211/211, **all parse cleanly** |
| `<title>` / canonical / `<h1>` | present and single on 211/211 |
| Reviewer byline | present on 174/211; the 37 without are all on the configured exempt paths |
| COE nav link | `coe-results` present on 211/211 |
| Service-page markers (`NAV-WHITE-PATCH-2026-04-29`, `MOBILE-NAV-FIX-2026-04-25`) | present on every `/services/*` sub-page |
| WP autop injection (strong signatures) | 0 |
| Text clipped by `overflow: hidden` | 0 real cases |

---

## Runner health — 3 defects in Des itself

These are about the tool, not the website, but they distort every sweep until fixed.

### T-1. The `</p></div></section>` autop signature matches valid markup

`~/.claude/skills/des-website-guard/SKILL.md` lists `</p>\s*</div>\s*</section>` as a
wpautop-injection signature. It matches **121 of 211 TRW pages** — and on inspection of
`/brands/audi/q5-common-problems-singapore/` the matched markup is a perfectly ordinary
paragraph closing at the end of a hand-authored section:

```
...worth more than knowing the trim level.</p>   </div> </section>
```

The three strong signatures (`<a class="...-card"...></p>`, `<p></a>`, `<p><script`) match
**zero** pages. Des's own mass-finding plausibility gate would have caught this at 121/211
and downgraded it to "suspected checker defect" — which is exactly what it is. The
signature should be dropped or paired with an anchor-breakage condition.

### T-2. The desktop and laptop viewports are Cloudflare-challenged on every single page

`src/devices.py` sets `user_agent: None` for `desktop` and `laptop`, so Playwright sends
its default headless UA. Cloudflare 403s it on **every URL** ("checking your browser") —
38 of 38 URLs in this run, no exceptions. Des backs off 5s and retries, and the retry
succeeds. Two of five viewports therefore cost an extra ~5s per URL and only render on the
second attempt.

The 403s are not the whole story on speed — `render_and_check()` also launches a brand new
browser per URL, and `check_buttons_clickable` clicks through at five viewports — but they
are the part that is one line to fix.

Reproduced and fixed in one line during this sweep: adding client-hint headers to the
desktop context turns 403 into 200 on the first request, with zero backoff across all 211
URLs.

```
plain headless UA   → home 403, about 403, contact 403
+ Sec-CH-UA, Sec-CH-UA-Mobile, Sec-CH-UA-Platform, Accept-Language → 200, 200, 200
```

One caution learned the hard way: do **not** add `Upgrade-Insecure-Requests` to the
context headers. Playwright replays extra headers on CORS preflights, which makes
`fonts.gstatic.com` and `cloudflareinsights.com` fail and fabricates a `console_errors`
finding on every page. That artifact was hit and removed during this sweep; the zero in
the console-errors row above is from a clean run.

### T-3. No per-sweep HTML report has been written since 2026-08-18

The cron is alive — `ededai/des-website-guard` has a commit every day, and the local
mirror is in sync with origin (0 commits behind). But the commit message changed from
`des: update bug log + report (trw)` to `des: baselines + bug log …` around 2026-08-19,
and `reports/trw/` has had nothing added since:

```
newest report:  reports/trw/20260818-005414-critical.html   (2026-08-18)
latest commit:  3d2a350  2026-09-13  "des: baselines + bug log 2026-09-13T00:24Z"
```

`bug-log.jsonl`'s newest `last_seen` is also 2026-08-18. So for 26 days the sweeps have
been committing baselines while the visual record every Telegram alert links to has not
been produced. Whatever is failing in `html_report.build()` (or in the workflow step that
commits it) is silent — the run stays green. Worth tracing before the next deep cron,
because a sweep whose report never lands is a sweep nobody can audit.

---

## What I would fix first

1. **H-1, the clipped mobile tables.** It is the only finding where a reader on a phone
   loses information the desktop shows — COE renewal costs and scrap-value tables, on the
   money pages. The pattern that works (`.trw-tblwrap { overflow-x: auto }`) is already in
   the codebase; apply it to `.trw-cost` and `.trw-table` and make it consistent on every
   page using `.trw-tblwrap`. One CSS change, 5 pages fixed.
2. **T-3, then T-2 — the sweep is not producing a record.** No HTML report has been
   written for 26 days, and the deep tier cannot finish a full sweep anyway. Right now
   Des looks green on cron while producing neither a completed deep sweep nor the report
   the alerts link to. T-3 is the one to trace first because it is silent; T-2 is a
   one-line change to `src/devices.py` that buys back a large chunk of the runtime.
3. **T-1, drop the loose autop signature.** It matches 121 of 211 pages of perfectly good
   markup. One regex away from flooding the bug log.
4. **M-3 and M-4, the Volvo cluster.** 12 titles and 7 meta descriptions, almost all in
   one brand folder, almost all pushed over the limit by the same suffix. It is a
   half-hour of copy trimming on pages that already rank.
5. **M-1, the orphan cards.** 26 pages, and the brand and topic hubs are the ones people
   land on. Worth deciding once whether the grid should centre the last row, stretch it,
   or pad the card count — then applying that decision everywhere.

Everything else is either cosmetic (L-1, L-2), waiting on a design confirmation from Ed
(L-2, L-3), or already tracked (L-4).
