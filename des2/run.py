"""The sweep. Everything else in des2 is a part; this is the machine.

Order matters and each step exists for a reason:

  discover -> visit -> GATE -> check -> baseline -> VERIFY -> reconcile -> report

The two capitalised steps are the ones that keep the guard honest. The gate
throws away everything seen on a page we did not actually reach, and the verify
pass re-tests each candidate in a clean context so transients die before they
reach anyone's phone.

Baselines are only advanced on a HEALTHY page. If a page lost something, the
old baseline is kept, so the loss keeps being detected until it is genuinely
fixed. Rebaselining a broken page would make the guard forget the break and
then cheerfully declare it fixed.

Usage:
  python -m des2.run --site trw --tier daily [--silent] [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from des2 import baseline as bl
from des2 import checks_break as cb
from des2 import checks_layout as cl
from des2 import fetch, gates, report, verify
from des2.config import load_site
from des2.discover import all_urls, daily_set
from des2.models import VIEWPORTS, Finding

SGT = timezone(timedelta(hours=8))


async def check_page(page, v, site_cfg, with_layout: bool = True) -> list[Finding]:
    """Every check that needs a live DOM, for one page at one viewport."""
    out: list[Finding] = []
    out += cb.check_page_error(v.status, v.url, v.viewport)
    out += cb.check_resource_failures(v.net_failures, v.url, v.viewport)
    out += cb.check_js_errors(
        [e for e in v.console_errors if not cb.is_resource_msg(e)], v.url, v.viewport)
    out += await cb.check_chrome(page, v.url, v.viewport, site_cfg)
    out += await cb.check_broken_images(page, v.url, v.viewport)
    out += await cb.check_mobile_menu(page, v.url, v.viewport, site_cfg)
    if with_layout:
        for layout_check in cl.ALL_LAYOUT_CHECKS:
            out += await layout_check(page, v.url, v.viewport)
    return out


async def sweep_one(context, url: str, viewport: str, site_cfg, baseline_root,
                    with_layout: bool = True):
    """One page at one viewport. Returns (findings, blocked, layout_findings)."""
    page, v = await fetch.visit(context, url, viewport)
    try:
        verdict, evidence = gates.crawl_health(v.status, v.html)
        if verdict == "blocked":
            # Nothing seen on this page may be trusted, so nothing is reported
            # from it except the fact that we could not see it.
            return [gates.blocked_finding(url, viewport, evidence)], True, []

        found = await check_page(page, v, site_cfg, with_layout=with_layout)
        layout = [f for f in found if f.kind == "layout"]
        other = [f for f in found if f.kind != "layout"]

        facts = await fetch.page_facts(page, site_cfg)
        old = bl.load_baseline(url, viewport, root=baseline_root)
        current = bl.capture(facts)

        losses = bl.diff(old, current, url, viewport) if facts else []
        fresh_layout, _pre_existing = bl.classify_layout(layout, old)

        # Advance the baseline ONLY when the page is healthy. A broken page
        # keeps its old baseline so the break stays visible.
        def _keep_layout_memory(fp):
            # A daily run does not look at layout, so it must NOT overwrite the
            # remembered defects with an empty list. Doing so would make every
            # longstanding quirk look brand new at the next weekly sweep.
            if with_layout:
                return bl.remember_layout(fp, layout)
            fp.layout_defects = list(old.layout_defects) if old else []
            return fp

        if facts and not losses and not other:
            bl.accept(url, viewport, _keep_layout_memory(current), root=baseline_root)
        elif old is None and facts:
            # First sight: record what is there, defects and all, so day one
            # announces nothing.
            bl.accept(url, viewport, _keep_layout_memory(current), root=baseline_root)

        return other + losses + fresh_layout, False, layout
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def sweep(site_name: str, tier: str = "daily", silent: bool = False,
                limit: int | None = None, now=None) -> int:
    now = now or datetime.now(SGT)
    cfg = load_site(site_name)
    site_label = cfg.get("name", site_name)
    baseline_root = os.path.join("baselines", site_name)

    # SPLIT BY VALUE (2026-08-27). Functional breakage is cheap to check and
    # matters every day. Layout is the expensive half (five checks across four
    # widths) and rarely changes without a deploy, so it earns a weekly slot
    # rather than a daily one. Both tiers still sweep every page.
    #   daily  : whole site, desktop + phone, breakage and lost content
    #   weekly : whole site, all four widths, everything including layout
    with_layout = (tier == "weekly")
    viewports = list(VIEWPORTS) if with_layout else ["desktop", "phone"]

    urls = (all_urls(cfg) if tier == "weekly" else daily_set(cfg, now=now)[0])
    if limit:
        urls = urls[:limit]
    if not urls:
        print("no URLs discovered; refusing to report a clean sweep of nothing")
        return 1

    from playwright.async_api import async_playwright

    all_findings: list[Finding] = []
    blocked = 0
    page_views = 0

    # All four viewports at once, with a longer pause between pages to hold the
    # per-host request rate roughly where two lanes had it. What this site's
    # edge reacts to is requests per second, not how many browser contexts we
    # happen to own, and the 2026-08-02 challenge storm came from hammering it
    # fast rather than from breadth. Measured: this keeps it near half a
    # request a second while halving the wall clock again.
    lane = asyncio.Semaphore(4)

    async def sweep_viewport(browser, viewport):
        found_all, blocked_n, views = [], 0, 0
        async with lane:
            context = await fetch.new_context(browser, viewport)
            try:
                for url in urls:
                    found, was_blocked, _ = await sweep_one(
                        context, url, viewport, cfg, baseline_root, with_layout)
                    found_all += found
                    blocked_n += 1 if was_blocked else 0
                    views += 1
                    await fetch.polite_pause()
            finally:
                await context.close()
        return found_all, blocked_n, views

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            results = await asyncio.gather(
                *(sweep_viewport(browser, vp) for vp in viewports),
                return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    # One viewport failing must not lose the other three.
                    print(f"  [viewport error] {type(r).__name__}: {str(r)[:120]}")
                    continue
                f, b, v = r
                all_findings += f
                blocked += b
                page_views += v

            # A mostly-blocked sweep is not a measurement of anything.
            should_abort, why = gates.sweep_abort(blocked, page_views)
            if should_abort:
                print(f"ABORT: {why}")
                if not silent:
                    report.send_telegram(report.could_not_run_text(why))
                return 1

            all_findings = gates.mass_finding_gate(all_findings, max(1, len(urls)))

            # VERIFY: re-test each affected page in a CLEAN context.
            async def recheck(u: str, vp: str) -> list[Finding]:
                ctx = await fetch.new_context(browser, vp)
                try:
                    found, _, _ = await sweep_one(ctx, u, vp, cfg, baseline_root, with_layout)
                    return found
                finally:
                    await ctx.close()

            await verify.verify_findings(all_findings, recheck)
        finally:
            await browser.close()

    alertable, held = verify.partition(all_findings)
    log = report.load_log()
    swept = set(urls)
    log, worth_alerting = report.reconcile(alertable, log, now=now, swept_urls=swept)
    aged = report.escalations(log, now=now)
    report.save_log(log)

    print(f"{site_label} {tier}: {len(urls)} urls x {len(viewports)} viewports "
          f"= {page_views} page views")
    print(f"  candidates {len(all_findings)} | provable {len(alertable)} | held {len(held)}")
    print(f"  new/reopened {len(worth_alerting)} | aged {len(aged)} | "
          f"open now {report.open_count(log)}")
    for f in held:
        print(f"  [held] {f.check} {f.url} ({verify.why_not_alertable(f)})")

    if silent:
        print("SILENT: baselines updated, nothing sent")
        return 0

    for f in worth_alerting:
        report.send_telegram(report.format_alert(f))
    for rec in aged:
        report.send_telegram(report.format_escalation(rec, now=now))
    if not worth_alerting and not aged and report.heartbeat_due(now):
        report.send_telegram(
            report.heartbeat_text(len(urls), site_label, report.open_count(log)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Des v2 sweep")
    ap.add_argument("--site", default="trw")
    ap.add_argument("--tier", choices=["daily", "weekly"], default="daily")
    ap.add_argument("--silent", action="store_true",
                    help="run and learn baselines, send nothing")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    try:
        return asyncio.run(sweep(a.site, a.tier, a.silent, a.limit))
    except Exception as e:
        # A guard that dies must say so, and must not be mistaken for a clean run.
        msg = report.could_not_run_text(f"{type(e).__name__}: {str(e)[:150]}")
        print(msg)
        if not a.silent:
            report.send_telegram(msg)
        return 1


if __name__ == "__main__":
    sys.exit(main())
