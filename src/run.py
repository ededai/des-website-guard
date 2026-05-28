"""
Entrypoint. Crawl a site's sitemap, render every URL across viewports,
run the check battery, de-dupe findings, route by severity, log to bug_log + Telegram.

Severity routing:
  critical: immediate Telegram + bug log
  high:     immediate Telegram + bug log
  medium:   bug log + end-of-sweep Telegram digest
  low:      bug log + bi-weekly Telegram digest (deep tier only)

Usage:
  python -m src.run --site=trw --tier=critical [--dry-run]

Tiers: critical | weekly | deep
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.devices import DEVICES
from src.sitemap import discover_urls, filter_skip
from checks import content_rules, visual
from reporters import telegram, bug_log


def load_site(name):
    p = ROOT / "sites" / f"{name}.yaml"
    return yaml.safe_load(p.read_text())


async def render_and_check(playwright, url, site, viewports):
    """Open URL across viewports. Run check battery. Return list of findings."""
    findings_per_viewport = {}
    browser = await playwright.chromium.launch(headless=True)
    try:
        for vp_name in viewports:
            vp = DEVICES[vp_name]
            ctx_args = {"viewport": {"width": vp["width"], "height": vp["height"]}}
            if vp.get("user_agent"):
                ctx_args["user_agent"] = vp["user_agent"]
            ctx = await browser.new_context(**ctx_args)
            page = await ctx.new_page()
            captured_errors = []
            page.on("pageerror", lambda exc: captured_errors.append(str(exc)))
            page.on("console", lambda msg: captured_errors.append(f"[{msg.type}] {msg.text}") if msg.type == "error" else None)

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                status = resp.status if resp else None
            except Exception as e:
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "load_failed", "severity": "critical", "evidence": str(e)})
                await ctx.close()
                continue

            if status and status >= 500:
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "http_5xx", "severity": "critical", "evidence": f"HTTP {status}"})
                await ctx.close()
                continue

            html = await page.content()
            findings = []

            # html-level checks
            from urllib.parse import urlparse
            url_path = urlparse(url).path.rstrip("/") + "/"
            byline_exempt = [
                p.rstrip("/") + "/" for p in (site.get("byline_exempt_paths") or [])
            ]
            for fn in content_rules.ALL_HTML_CHECKS:
                # Skip byline check on hub, utility, and contact pages
                if fn is content_rules.check_byline and any(
                    url_path.startswith(p) for p in byline_exempt
                ):
                    continue
                f = fn(html)
                if f:
                    f["url"] = url
                    f["viewport"] = vp_name
                    findings.append(f)

            # markers (TRW service pages)
            if "/services/" in url and "/services/" != url.rstrip("/").split("therightworkshop.com")[-1]:
                f = content_rules.check_required_markers(html, site.get("required_markers", {}).get("service_pages", []))
                if f:
                    f["url"] = url
                    f["viewport"] = vp_name
                    findings.append(f)

            # visual / functional checks
            checks = [visual.check_chrome_consistency, visual.check_maroon_leak, visual.check_broken_images, visual.check_buttons_clickable]
            # Mobile-only: hamburger menu functional check (logged in 2026-05-02 post-mortem)
            if vp_name in ("phone_ios", "phone_and"):
                checks.append(visual.check_mobile_menu)
            for fn in checks:
                try:
                    if fn is visual.check_chrome_consistency:
                        f = await fn(page, None, None)
                    else:
                        f = await fn(page)
                except Exception as e:
                    f = {"check": f"check_error_{fn.__name__}", "severity": "low", "evidence": str(e)}
                if f:
                    f["url"] = url
                    f["viewport"] = vp_name
                    findings.append(f)

            # console errors collected during navigation
            f = await visual.check_console_errors(page, captured_errors)
            if f:
                f["url"] = url
                f["viewport"] = vp_name
                findings.append(f)

            findings_per_viewport[vp_name] = findings
            await ctx.close()
    finally:
        await browser.close()
    return findings_per_viewport


def dedupe(all_findings, site_name, in_charge):
    """Group by check_id; one finding per check with affected URL list."""
    grouped = {}
    for f in all_findings:
        key = f["check"]
        g = grouped.setdefault(key, {
            "title": key.replace("_", " ").title(),
            "check_id": key,
            "severity": f["severity"],
            "site": site_name,
            "in_charge": in_charge,
            "summary": f.get("evidence", "")[:200],
            "urls": [],
            "evidence": f.get("evidence", ""),
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "status": "open",
        })
        if f["url"] not in g["urls"]:
            g["urls"].append(f["url"])
    return list(grouped.values())


def route(finding, dry_run=False):
    """Immediate routing for critical/high. Medium/low collected for digest."""
    sev = finding["severity"]
    if not dry_run:
        bug_log.log_finding(finding)
    if dry_run:
        print(f"[DRY-RUN] {sev.upper()} — {finding['title']} ({len(finding['urls'])} URLs)")
        return
    if sev == "critical":
        telegram.send(telegram.format_critical(finding))
    elif sev == "high":
        # in production this is gated to 08:00 SGT business hours; here always send
        telegram.send(telegram.format_high(finding))
    # medium / low handled in send_digests() after the sweep finishes


def send_digests(findings, site_name, tier, dry_run=False):
    """Emit batched Telegram digests for medium and low after the sweep finishes."""
    medium = [f for f in findings if f["severity"] == "medium"]
    low = [f for f in findings if f["severity"] == "low"]
    period = {"critical": "daily", "weekly": "weekly", "deep": "bi-weekly"}.get(tier, tier)
    if medium:
        msg = telegram.format_digest(medium, "medium", site_name, period)
        if dry_run:
            print(f"[DRY-RUN] MEDIUM DIGEST — {len(medium)} findings\n{msg}")
        else:
            telegram.send(msg)
    # low only goes out on the deep tier (bi-weekly), so noise stays low
    if low and tier == "deep":
        msg = telegram.format_digest(low, "low", site_name, "bi-weekly")
        if dry_run:
            print(f"[DRY-RUN] LOW DIGEST — {len(low)} findings\n{msg}")
        else:
            telegram.send(msg)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="trw")
    ap.add_argument("--tier", choices=["critical", "weekly", "deep"], default="critical")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="cap URLs for testing")
    args = ap.parse_args()

    site = load_site(args.site)
    if not site or site.get("active") is False:
        print(f"site '{args.site}' is inactive or missing")
        return
    if site.get("sitemap") in (None, "TBD"):
        print(f"site '{args.site}' has no sitemap configured yet")
        return

    urls = discover_urls(site["sitemap"])
    urls = filter_skip(urls, site.get("skip_paths", []))
    if args.tier == "critical":
        urls = urls[:20]
    if args.limit:
        urls = urls[: args.limit]

    viewports = ["desktop"] if args.tier == "critical" else list(DEVICES.keys())
    print(f"DES: scanning {len(urls)} URLs on {len(viewports)} viewports — {args.tier}")

    from playwright.async_api import async_playwright
    all_findings = []
    async with async_playwright() as pw:
        for u in urls:
            per_vp = await render_and_check(pw, u, site, viewports)
            for vp, fs in per_vp.items():
                all_findings.extend(fs)

    findings = dedupe(all_findings, site["name"], site["in_charge"])
    findings.sort(key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(f["severity"], 9))
    print(f"DES: {len(findings)} unique findings")
    for f in findings:
        route(f, dry_run=args.dry_run)
    send_digests(findings, site["name"], args.tier, dry_run=args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
