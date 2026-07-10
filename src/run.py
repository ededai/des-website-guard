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


# Console messages that are browser/CSS noise, not JavaScript errors.
# Per the 2026-06-20 incident (feedback_des_audit_no_handler_false_positives):
# only genuine JS errors count. Sites can extend via `console_ignore_patterns`.
CONSOLE_IGNORE_PATTERNS = [
    "Ignored @property rule",
    "Content Security Policy",
    "Tracking Prevention",
    "third-party cookie",
    "[issue]",
    "[warning]",
    "[debug]",
]


def _console_noise(text, extra_patterns):
    return any(p in text for p in CONSOLE_IGNORE_PATTERNS) or any(p in text for p in (extra_patterns or []))


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
            # Real mobile emulation — without these, phone viewports are just
            # narrow desktop windows and mobile-only bugs never reproduce.
            if vp.get("is_mobile"):
                ctx_args["is_mobile"] = True
                ctx_args["has_touch"] = True
                ctx_args["device_scale_factor"] = vp.get("device_scale_factor", 2)
            ctx = await browser.new_context(**ctx_args)
            page = await ctx.new_page()
            captured_errors = []
            ignore_extra = site.get("console_ignore_patterns") or []
            page.on("pageerror", lambda exc: captured_errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: captured_errors.append(f"[{msg.type}] {msg.text}")
                if msg.type == "error" and not _console_noise(msg.text, ignore_extra)
                else None,
            )

            try:
                # Use "commit" + DOMContentLoaded fallback so slow third-party
                # CSS bundles (Jetpack Boost, gtag, stats.wp.com) don't make
                # the sweep timeout on otherwise-healthy pages.
                resp = await page.goto(url, wait_until="commit", timeout=30000)
                status = resp.status if resp else None
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=20000)
                except Exception:
                    pass
                # Give inline scripts a moment to execute so pageerror handlers fire
                await page.wait_for_timeout(2000)
            except Exception as e:
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "load_failed", "severity": "critical", "evidence": str(e)})
                await ctx.close()
                continue

            if status and status >= 500:
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "http_5xx", "severity": "critical", "evidence": f"HTTP {status}"})
                await ctx.close()
                continue
            if status and status >= 400:
                # 404/410 on a sitemap-published URL is a dead page Google is
                # being told to index — high. 403/429 under a fast headless
                # crawl is usually a Cloudflare/bot challenge, not a site bug —
                # medium, so it surfaces without paging anyone at 3am.
                if status in (404, 410):
                    f = {"check": "http_4xx_dead_page", "severity": "high", "evidence": f"HTTP {status} on sitemap URL"}
                else:
                    f = {"check": "http_4xx_access_blocked", "severity": "medium", "evidence": f"HTTP {status} (possible bot challenge under crawl)"}
                f.update({"url": url, "viewport": vp_name})
                findings_per_viewport.setdefault(vp_name, []).append(f)
                await ctx.close()
                continue

            html = await page.content()
            findings = []

            # html-level checks (site-agnostic battery)
            from urllib.parse import urlparse
            url_path = urlparse(url).path.rstrip("/") + "/"
            byline_exempt = [
                p.rstrip("/") + "/" for p in (site.get("byline_exempt_paths") or [])
            ]
            for fn in content_rules.ALL_HTML_CHECKS:
                f = fn(html)
                if f:
                    f["url"] = url
                    f["viewport"] = vp_name
                    findings.append(f)

            # site-aware checks — all values come from sites/<site>.yaml so a
            # non-TRW site never gets judged against TRW rules.
            site_checks = []
            if site.get("required_byline") and not any(url_path == p or url_path.startswith(p) for p in byline_exempt) and url_path != "/":
                site_checks.append(content_rules.check_byline(html, expected=site["required_byline"]))
            site_checks.append(content_rules.check_address_unit(
                html, unit=site.get("address_unit") or "", address_marker=site.get("address_marker") or ""))
            fps = site.get("canonical_footer_fingerprints")
            if fps is not None:
                site_checks.append(content_rules.check_footer_drift(html, url=url, fingerprints=fps))
            bc_exempt = set(site.get("bc_exempt_slugs") or []) or None
            site_checks.append(content_rules.check_breadcrumb(html, url=url, exempt_slugs=bc_exempt))
            for f in site_checks:
                if f:
                    f["url"] = url
                    f["viewport"] = vp_name
                    findings.append(f)

            # markers (service sub-pages; the /services/ hub itself is exempt).
            # The old hub-exclusion compared against a hardcoded
            # therightworkshop.com split and could never be true.
            if url_path.startswith("/services/") and url_path != "/services/":
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
                        f = await fn(page, site)
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


SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def dedupe(all_findings, site_name, in_charge):
    """Group by check_id; one finding per check with affected URL list.
    Evidence is str()-coerced (some checks return dict evidence — this
    crashed every weekly/deep sweep from 2026-05-15 to 2026-07-03), and the
    group takes the WORST severity seen, not whichever arrived first."""
    grouped = {}
    for f in all_findings:
        key = f["check"]
        ev = str(f.get("evidence", ""))
        g = grouped.setdefault(key, {
            "title": key.replace("_", " ").title(),
            "check_id": key,
            "severity": f["severity"],
            "site": site_name,
            "in_charge": in_charge,
            "summary": ev[:200],
            "urls": [],
            "evidence": ev,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "status": "open",
        })
        if SEV_RANK.get(f["severity"], 9) < SEV_RANK.get(g["severity"], 9):
            g["severity"] = f["severity"]
        if f["url"] not in g["urls"]:
            g["urls"].append(f["url"])
        # Merge DISTINCT evidence strings instead of keeping only the first.
        # 2026-07-10: an autop_injection group spanning p_wrapped_comment (1 pg)
        # and p_wrapped_script (6 pgs) alerted as "7 pages" but named only the
        # comment signature — the script family was invisible in the alert.
        if ev and ev not in g["evidence"]:
            g["evidence"] = f"{g['evidence']}; {ev}"
            g["summary"] = g["evidence"][:200]
    return list(grouped.values())


def is_waived(finding, waivers):
    """Return the matching waiver dict (or None). A finding is waived when its check_id matches
    and every provided condition matches. This is Des's learning loop: a confirmed false positive
    is suppressed declaratively in sites/<site>.yaml, so it never re-alerts — no code edit needed.

    Waiver fields (all optional except `check`):
      check:          check_id to match (required)
      url_contains:   only waive if at least one affected URL contains this substring
      evidence_regex: only waive if the finding evidence matches this regex
      reason:         human note (shown in the waived-findings log)
    """
    import re as _re
    for w in waivers or []:
        if w.get("check") != finding.get("check_id"):
            continue
        uc = w.get("url_contains")
        if uc and not any(uc in u for u in finding.get("urls", [])):
            continue
        er = w.get("evidence_regex")
        if er and not _re.search(er, finding.get("evidence", "")):
            continue
        return w
    return None


def route(finding, dry_run=False):
    """Immediate routing for critical/high. Medium/low collected for digest."""
    sev = finding["severity"]
    if not dry_run:
        bug_log.log_finding(finding)
    if dry_run:
        urls_preview = "\n  ".join(finding["urls"][:20])
        print(f"[DRY-RUN] {sev.upper()} — {finding['title']} ({len(finding['urls'])} URLs)\n  {urls_preview}\n  evidence: {finding.get('evidence','')[:120]}")
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

    try:
        urls = discover_urls(site["sitemap"])
    except Exception as e:
        print(f"DES: FATAL — sitemap fetch failed for {site['sitemap']}: {e}")
        sys.exit(2)
    if not urls:
        # Zero URLs must never look like a clean sweep — fail loudly so the
        # CI run goes red instead of a silent green with nothing scanned.
        print(f"DES: FATAL — sitemap {site['sitemap']} yielded 0 URLs")
        sys.exit(2)
    urls = filter_skip(urls, site.get("skip_paths", []))

    # Priority-first: homepage, then hub + section pages, then articles.
    priority_prefixes = [p.rstrip("/") for p in (site.get("priority_paths") or []) if p.rstrip("/")]
    if priority_prefixes:
        site_base = site["url"].rstrip("/")
        home_urls, priority_urls, rest_urls = [], [], []
        for u in urls:
            path = u.replace(site_base, "").rstrip("/") or "/"
            if path == "/":
                home_urls.append(u)
            elif any(path == p or path.startswith(p + "/") for p in priority_prefixes):
                priority_urls.append(u)
            else:
                rest_urls.append(u)
        urls = home_urls + priority_urls + rest_urls

    if args.limit:
        urls = urls[: args.limit]

    viewports = ["desktop"] if args.tier == "critical" else list(DEVICES.keys())
    print(f"DES: scanning {len(urls)} URLs on {len(viewports)} viewports — {args.tier}")

    from playwright.async_api import async_playwright
    all_findings = []
    async with async_playwright() as pw:
        for u in urls:
            # Fault isolation: one exploding page must not discard the whole
            # sweep's findings (previously a single crash lost everything).
            try:
                per_vp = await render_and_check(pw, u, site, viewports)
            except Exception as e:
                all_findings.append({"url": u, "viewport": "n/a", "check": "sweep_page_crash", "severity": "high", "evidence": f"render_and_check raised: {e}"})
                continue
            for vp, fs in per_vp.items():
                all_findings.extend(fs)

    findings = dedupe(all_findings, site["name"], site["in_charge"])
    findings.sort(key=lambda f: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(f["severity"], 9))

    # Learning loop: drop findings that match a waiver in sites/<site>.yaml (confirmed false
    # positives / known-noise). Waived findings are printed for audit but never alerted.
    waivers = site.get("waivers", [])
    active, waived = [], []
    for f in findings:
        w = is_waived(f, waivers)
        (waived if w else active).append((f, w))
    if waived:
        print(f"DES: {len(waived)} finding(s) waived (see sites/{args.site}.yaml waivers):")
        for f, w in waived:
            print(f"  WAIVED {f['severity'].upper()} — {f['title']} ({len(f['urls'])} URLs) — {w.get('reason','(no reason)')}")
    findings = [f for f, _ in active]

    print(f"DES: {len(findings)} unique findings")
    for f in findings:
        route(f, dry_run=args.dry_run)
    send_digests(findings, site["name"], args.tier, dry_run=args.dry_run)

    # Lifecycle: close issues that stopped firing (full sweeps only — a
    # --limit run hasn't seen every page and must not mass-close).
    if not args.dry_run and not args.limit:
        closed = bug_log.reconcile(site["name"], {f["check_id"] for f in findings})
        if closed:
            print(f"DES: {len(closed)} issue(s) auto-closed as fixed: {', '.join(closed)}")

    # Alerts that failed to deliver must fail the run — a green sweep whose
    # Telegram went nowhere is how criticals get missed.
    if telegram.UNDELIVERED and not args.dry_run:
        print(f"DES: FATAL — {len(telegram.UNDELIVERED)} alert(s) undelivered")
        sys.exit(3)


if __name__ == "__main__":
    asyncio.run(main())
