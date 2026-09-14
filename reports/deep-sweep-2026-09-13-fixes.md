# Des deep sweep 2026-09-13 — fix log

**Status:** IN PROGRESS (started 2026-09-14)
**Source report:** `deep-sweep-2026-09-13.md`
**Crops:** `.des-shots/2026-09-13/fixes/`

---

## H-1 — Wide tables clipped on mobile (5 URLs)

### Diagnosis (live measurement at 390, own headless Chromium)

The report's proposed fix (`overflow-x: auto` on the wrapper) is **rejected** by house rule:
tables never scroll sideways on mobile. Live inspection also shows the report's read of the
cause was wrong on four of the five pages.

Measured at 390 (`scratchpad/measure.py tables`, `scratchpad/cells.py`):

| URL | wrapper | already stacking? | real cause |
|---|---|---|---|
| /coe-renewal-singapore/ | `.trw-cost` | yes (thead hidden, `td::before` labels shown) | `td.range { white-space: nowrap }` — one cell 1120px wide in a 306px card |
| /coe-renewal-singapore/ | `.trw-tblwrap` x2 (`table.trw-parf`) | **no** — still `display: table`, `overflow-x: visible`, 451px table in a 292px wrapper | no stacking rule for `.trw-parf`; 5 `td` missing `data-label` |
| /scrap-car-singapore/ | `.trw-cost` | yes | `td.range` nowrap — 989px cell |
| /coe-renewal-car-maintenance-budget-singapore/ | `.trw-cost` | yes | `td.range` nowrap — 4 cells, worst 819px |
| /coe-renewal-car-maintenance-budget-singapore/ | `.trw-tblwrap` (`table.trw-parf`) | **no** — `overflow-x: auto` (the "correct pattern" the report wanted copied) | sideways scroll, banned by house rule |
| /singapore-parf-rebate-schedule-and-cap-revised-from-feb-2026/ | `.trw-table` | yes | `td.num { white-space: nowrap }` — 580px cell in a 274px card |
| /hybrid-battery-replacement-cost-singapore/ | `.trw-cost` | yes | `td.range` nowrap — 2 cells, worst 437px |

So the real defect is one line of CSS, not a missing wrapper rule: the stacking block at
`max-width: 760px` restores `text-align: left` on `td.range` but never clears the
`white-space: nowrap` that the desktop rule sets, so any prose that lives in a "range" or
"num" cell renders as a single unbreakable line and is clipped by the card.

### What changed

Two CSS additions, both inserted inside each page's **own inline `<style>`**, at the end of
the existing `@media (max-width: 760px)` stacking block, immediately after the exact line:

```
  .trw-page .article-body .col .trw-cost td.range { text-align: left; }
```

Exact-string replace, count asserted at 1 occurrence per file, output length asserted.
No `overflow-x: auto` anywhere. Nothing else on any page was touched.

**Fix 1 — clear the inherited `nowrap` (all 5 pages, +557 bytes each):**

```css
  .trw-page .article-body .col .trw-table td,
  .trw-page .article-body .col .trw-table .num,
  .trw-page .article-body .col .trw-cost td,
  .trw-page .article-body .col .trw-cost td.range { white-space: normal; overflow-wrap: anywhere; }
```

**Fix 2 — stack `.trw-parf` as labelled cards (pages 5720 and 9177 only, +1885 bytes each):**
full `.trw-tblwrap table.trw-parf` block — `overflow-x: visible` on the wrapper (this is what
replaces 9177's banned `overflow-x: auto`), `thead { display: none }`, `tbody/tr/td { display:
block }`, card `tr` with all four border sides stated explicitly, `td` with all four border
sides stated explicitly and `padding: 0 0 16px`, `td::before { content: attr(data-label) }`,
and `td[data-label=""]::before { display: none }` for the one table whose header cell is
`&nbsp;` (the key column is its own label there). Every `td` on both pages already carried a
`data-label`, so no markup changed — this finding is CSS-only.

### Pushed

All five via `audit/des-push.py` (content only, expected-length assertion + byte-identical
read-back). No POST was denied.

| id | page | bytes | result |
|---|---|---|---|
| 5720 | /coe-renewal-singapore/ | 83,765 -> 86,207 | byte-identical read-back OK |
| 5726 | /scrap-car-singapore/ | 79,593 -> 80,150 | byte-identical read-back OK |
| 9177 | /coe-renewal-car-maintenance-budget-singapore/ | 91,320 -> 93,762 | byte-identical read-back OK |
| 6187 | /singapore-parf-rebate-schedule-and-cap-revised-from-feb-2026/ | 56,041 -> 56,598 | byte-identical read-back OK |
| 9191 | /hybrid-battery-replacement-cost-singapore/ | 87,119 -> 87,676 | byte-identical read-back OK |

### Re-measured live at 390 after the push

`document.documentElement.scrollWidth == window.innerWidth == 390` on all five, so no page
scrolls sideways. Per table wrapper: `scrollWidth == clientWidth` (nothing clipped),
`overflow-x: visible` (nothing scrolls sideways either), `thead` hidden, every `td` rendering
its `data-label`, and **zero** overflowing cells.

| URL | wrapper | before (wrap / scroll) | after (wrap / scroll) | labels shown | overflowing cells before -> after |
|---|---|---|---|---|---|
| /coe-renewal-singapore/ | `.trw-cost` | 342 / 1138 | 342 / 342 | 12 / 12 | 1 -> 0 |
| /coe-renewal-singapore/ | `.trw-tblwrap` #1 | 292 / 451 (table 451px, unreachable) | 292 / 292 | 20 / 20 | 0 -> 0 |
| /coe-renewal-singapore/ | `.trw-tblwrap` #2 | 342 / 342 (real table, 0 labels) | 342 / 342 | 15 / 15 | 0 -> 0 |
| /scrap-car-singapore/ | `.trw-cost` | 342 / 1007 | 342 / 342 | 6 / 6 | 1 -> 0 |
| /scrap-car-singapore/ | `.trw-table` | 342 / 342 | 342 / 342 | 24 / 24 | 0 -> 0 |
| /coe-renewal-car-maintenance-budget-singapore/ | `.trw-cost` | 342 / 837 | 342 / 342 | 21 / 21 | 4 -> 0 |
| /coe-renewal-car-maintenance-budget-singapore/ | `.trw-tblwrap` | 342 / 460, `overflow-x: auto` | 342 / 342, `visible` | 18 / 18 | 0 -> 0 |
| /singapore-parf-rebate-schedule-and-cap-revised-from-feb-2026/ | `.trw-table` | 310 / 598 | 310 / 310 | 24 / 24 | 1 -> 0 |
| /hybrid-battery-replacement-cost-singapore/ | `.trw-table` | 342 / 342 | 342 / 342 | 8 / 8 | 0 -> 0 |
| /hybrid-battery-replacement-cost-singapore/ | `.trw-cost` | 342 / 455 | 342 / 342 | 9 / 9 | 2 -> 0 |

The specific cell the report photographed on `/scrap-car-singapore/` — "75% down to 50% of the
original ARF, with n…" — measured 989px of content in a 306px card before the fix and wraps
inside the card after it.

**H-1 status: CLOSED on all 5 URLs.**

---

## M-2 — Em / en dashes in body copy (13 URLs flagged, 12 real)

### Diagnosis

Scanned each page's raw with zones marked (`<style>`, `<script>`, HTML comments vs body), then
checked the **rendered** text in a real browser as the authoritative measure.

Findings:

- **Every dash in body copy is an en dash entity (`&ndash;`) and every one is a numeric or
  year range.** No em dashes in prose anywhere, so no sentence needed a comma / colon / full
  stop; the correct replacement throughout is **"to"**.
- **Zero dashes in any `<h1>`**, so the naming-convention exemption never applied.
- **Zero dashes in FAQPage JSON-LD or in visible FAQ text** on any of the 13 pages, so the
  "keep FAQ and JSON-LD identical" rule is satisfied by construction. Nothing in a
  `<script type="application/ld+json">` was touched.
- The 31 literal em dashes (`—`) that appear on most of these pages are all inside CSS comments
  in the component kit (`/* tldr-block — summary of the whole piece. */`) or inside HTML
  comments (`<!-- Nav + breadcrumb injected by Snippet 25 (Canonical Chrome) — do not add
  here. -->`). They never render. Left alone.
- **`/cat-a-vs-cat-b-singapore/` is a false positive.** It has **zero** dashes in rendered body
  text; its only dashes are the 31 CSS-comment em dashes above. Confirmed in the browser
  (`hits 0`, `ldWithDash 0`). No edit made. See T-4 below.

### What changed

One edit per page: `&ndash;` -> ` to `, count asserted per file, applied only after a guard
proved no `&ndash;` sat inside a `<style>`, `<script>` or HTML comment, and none inside an `<h1>`.

| id | URL | n | examples |
|---|---|---|---|
| 1221 | /brake-pads-rotors-singapore-when-to-change/ | 3 | `S$150&ndash;S$280` -> `S$150 to S$280` |
| 9335 | /brake-squeal-judder-singapore/ | 5 | `SGD 80&ndash;180` -> `SGD 80 to 180` |
| 10813 | /brands/mercedes-benz/a-class-cla-common-problems-singapore/ | 5 | `2012&ndash;2018` -> `2012 to 2018`; `60,000&ndash;80,000km` -> `60,000 to 80,000km` |
| 11012 | /brands/mercedes-benz/c-class-w205-common-problems-singapore/ | 3 | eyebrow `2014&ndash;2021`, facts `2014&ndash;2018`, `2018&ndash;2021` |
| 11005 | /brands/mercedes-benz/e-class-w212-w213-common-problems-singapore/ | 9 | `.vc-years` `2009&ndash;2013` etc, `2018&ndash;2019` in prose |
| 10815 | /brands/mercedes-benz/gearbox-jerking-7g-9g-tronic-singapore/ | 1 | `W204 (2007&ndash;2014)` -> `W204 (2007 to 2014)` |
| 966 | /car-aircon-not-cold-singapore-fix/ | 1 | `7&ndash;12 yrs` -> `7 to 12 yrs` |
| 5743 | /cheapest-car-workshop-singapore/ | 3 | `$80&ndash;$150` -> `$80 to $150` |
| 7132 | /engine-mounting-singapore/ | 4 | `$150&ndash;$500` -> `$150 to $500` |
| 1224 | /independent-workshop-vs-authorised-service-centre-singapore/ | 1 | `8&ndash;10 yrs` -> `8 to 10 yrs` |
| 5736 | /pre-purchase-car-inspection-singapore/ | 1 | `$150&ndash;$300` -> `$150 to $300` |
| 967 | /tyres-battery-singapore-checks-guide/ | 5 | `2&ndash;4 yrs`, `S$120&ndash;S$350` etc |
| — | /cat-a-vs-cat-b-singapore/ | 0 | **no edit — false positive** |

38 replacements across 12 pages. No spaced hyphens introduced.

