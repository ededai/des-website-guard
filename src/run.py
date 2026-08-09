"""
Entrypoint. Crawl a site's sitemap, render every URL across viewports,
run the check battery, de-dupe findings, route by severity, log to bug_log + Telegram.

Severity routing:
  critical: immediate Telegram (photo + caption when a screenshot exists) + bug log
  high:     queued (reporters/alert_queue.py), flushed at 08:00-22:00 SGT + bug log
  medium:   bug log + end-of-sweep Telegram digest
  low:      bug log + bi-weekly Telegram digest (deep tier only)

Every sweep also writes one self-contained HTML report (reporters/html_report.py),
linked from every Telegram alert.

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
from src import crawl_guard
from checks import content_rules, visual, recheck
from reporters import telegram, bug_log, evidence, alert_queue, screenshot, html_report


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


async def _goto_with_backoff(page, url, guard, captured_errors=None):
    """Navigate, and if the edge bot-challenges us, back off and retry once
    before believing it. Returns (status, html, verdict, evidence).

    Most 403s under a sweep are self-inflicted rate limiting, not a site fault —
    so we give the host room to breathe before writing anything down."""
    attempt = 0
    while True:
        # Use "commit" + DOMContentLoaded fallback so slow third-party CSS
        # bundles (Jetpack Boost, gtag, stats.wp.com) don't make the sweep
        # timeout on otherwise-healthy pages.
        resp = await page.goto(url, wait_until="commit", timeout=30000)
        status = resp.status if resp else None
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=20000)
        except Exception:
            pass
        # Give inline scripts a moment to execute so pageerror handlers fire
        await page.wait_for_timeout(2000)
        html = await page.content()
        verdict, evidence = crawl_guard.classify_response(status, html)
        if verdict != "blocked" or attempt >= guard.max_retries:
            return status, html, verdict, evidence
        await guard.backoff(attempt, url=url, evidence=evidence)
        attempt += 1
        # The challenge interstitial runs its OWN scripts on this same page
        # object, and the pageerror/console listeners were attached before the
        # first goto. Without this reset the retried (clean, 200) load inherits
        # the challenge page's exceptions and reports a phantom HIGH
        # `console_errors` — a challenged page leaking a high finding is exactly
        # what this module exists to prevent.
        if captured_errors is not None:
            captured_errors.clear()


async def render_and_check(playwright, url, site, viewports, guard, capture=True):
    """Open URL across viewports. Run check battery. Return dict of findings.

    A crawl-blocked page (bot challenge / non-200) short-circuits every viewport:
    no DOM, visual or content check may run against a page we never actually
    saw. See src/crawl_guard.py for the 2026-08-02 false-positive post-mortem."""
    findings_per_viewport = {}
    page_blocked = False
    blocked_evidence = ""
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
                status, html, verdict, http_evidence = await _goto_with_backoff(
                    page, url, guard, captured_errors=captured_errors)
            except Exception as e:
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "load_failed", "severity": "critical", "evidence": str(e)})
                await ctx.close()
                continue

            # --- Crawl-health gate -------------------------------------------
            # Nothing below this line may run against a page we did not really
            # receive. A bot-challenge body has no nav, no footer and no mobile
            # drawer; judging it produces false criticals (2026-08-02: 159 of
            # them on TRW). Blocked pages roll up into ONE medium finding.
            if verdict == "blocked":
                page_blocked = True
                blocked_evidence = http_evidence
                findings_per_viewport.setdefault(vp_name, []).append({
                    "url": url, "viewport": vp_name, "check": "crawl_blocked",
                    "severity": "medium",
                    "evidence": f"{http_evidence} — all DOM/visual/content checks skipped for this URL",
                })
                await ctx.close()
                break  # don't hammer a host that is already refusing us
            if verdict == "server_error":
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "http_5xx", "severity": "critical", "evidence": http_evidence})
                await ctx.close()
                continue
            if verdict == "dead":
                # 404/410 on a sitemap-published URL is a dead page Google is
                # being told to index — genuine high, and still no DOM checks.
                findings_per_viewport.setdefault(vp_name, []).append({"url": url, "viewport": vp_name, "check": "http_4xx_dead_page", "severity": "high", "evidence": http_evidence})
                await ctx.close()
                continue

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
                    if fn in (visual.check_chrome_consistency, visual.check_mobile_menu):
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
            # Screenshot evidence for critical/high findings only (D-01). One
            # shot per viewport is enough; the alert and the report share it.
            # capture_alert_shot() never raises, so this cannot skip ctx.close().
            if capture and any(f["severity"] in ("critical", "high") for f in findings):
                shot = await screenshot.capture_alert_shot(page, url, vp_name)
                if shot:
                    for f in findings:
                        if f["severity"] in ("critical", "high"):
                            f["screenshot"] = shot
            await ctx.close()
            # Small inter-viewport pause: 5 viewports per URL is 5 requests to
            # the same host, which is what tripped the 2026-08-02 challenge.
            await guard.polite_pause(scale=0.34)
    finally:
        await browser.close()
    guard.record(url, page_blocked, blocked_evidence)
    return findings_per_viewport


# How many of a check_id's affected URLs the reproduce gate re-verifies
# before giving up and calling it non-reproducing. Capped so a 73-page
# finding doesn't cost 73 extra page loads to confirm.
REPRODUCE_SAMPLE = 3


async def reproduce_finding(pw, finding, site, guard, sitemap_urls=None):
    """REPRODUCE-BEFORE-ALERT gate. Re-run `finding["check_id"]` against up to
    REPRODUCE_SAMPLE of its affected URLs in a FRESH browser context (fresh
    page, cleared captured_errors) and return True the moment it fires again
    on any of them.

    This is deliberately check-agnostic: it dispatches on WHERE a check_id's
    logic lives, not on what the check does, so every current and future
    check is covered without teaching this function anything new:
      - curated recheck ids (checks.recheck.REGISTRY) reuse their own
        dedicated producer via recheck.run_site_rechecks() — already written
        to re-test that exact bug, and a crashed/unverifiable re-test there
        already reads as "keep open" rather than "absent" (never a silent
        downgrade on ambiguity).
      - everything else — the whole checks/visual.py + checks/content_rules.py
        battery, plus the network-level ids render_and_check() itself emits
        (load_failed, http_5xx, http_4xx_dead_page, crawl_blocked,
        sweep_aborted_bot_challenge) — is re-verified by calling
        render_and_check() again: the SAME function that produced the
        original finding, against a brand-new browser context.

    Every documented false-positive family (missed handlers, id-guessed
    outputs, CSS-as-JS-errors, challenge pages, 0x0 shadow buttons) was a
    single, unconfirmed sighting. This is the generic backstop: nothing may
    alert at critical/high off one look, regardless of which check produced
    it or whether that check exists yet.

    Uses a throwaway SweepGuard so reproduction traffic never touches the
    real sweep's crawl-health accounting — guard.pages_attempted /
    blocked_ratio feed the abort/reconcile decisions and must reflect only
    the actual crawl, not extra confirmation requests.
    """
    check_id = finding.get("check_id", "")
    urls = (finding.get("urls") or [])[:REPRODUCE_SAMPLE]
    if not urls:
        return False

    if check_id in recheck.REGISTRY:
        record = {"check_id": check_id, "severity": finding.get("severity", "high"), "url_list": urls}
        try:
            subs = await recheck.run_site_rechecks(
                pw, site, [record], sitemap_urls or urls, http_budget=10, nav_budget=6)
        except Exception:
            return False
        return any(s.get("check") == check_id for s in subs)

    by_url_vp = finding.get("_viewport_by_url") or {}
    repro_guard = crawl_guard.SweepGuard(
        delay=guard.delay, abort_ratio=guard.abort_ratio, min_pages=guard.min_pages,
        retry_backoff_base=guard.retry_backoff_base, max_retries=guard.max_retries,
    )
    for url in urls:
        vp_name = by_url_vp.get(url, "desktop")
        if vp_name not in DEVICES:
            vp_name = "desktop"
        try:
            per_vp = await render_and_check(pw, url, site, [vp_name], repro_guard, capture=False)
        except Exception:
            continue
        if any(f["check"] == check_id for f in per_vp.get(vp_name, [])):
            return True
    return False


SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def dedupe(all_findings, site_name, in_charge):
    """Group by check_id; one finding per check with affected URL list.
    Evidence is humanized at this pipeline boundary (reporters/evidence.py).
    Some checks return dict evidence, which crashed every weekly/deep sweep
    from 2026-05-15 to 2026-07-03 as a raw str() coercion, and could still
    leak a Python repr into Telegram. The group takes the WORST severity
    seen, not whichever arrived first."""
    grouped = {}
    for f in all_findings:
        key = f["check"]
        ev = evidence.humanize(f.get("evidence"))
        g = grouped.setdefault(key, {
            "title": key.replace("_", " ").title(),
            "check_id": key,
            "severity": f["severity"],
            "site": site_name,
            "in_charge": in_charge,
            "summary": ev[:200],
            "urls": [],
            # First-seen viewport per affected URL. Used only by the
            # reproduce-before-alert gate (reproduce_finding()) so a
            # mobile-only check (e.g. mobile_menu) gets re-verified on the
            # viewport it actually fired on, not silently re-checked on
            # desktop where that check never even runs.
            "_viewport_by_url": {},
            "evidence": ev,
            # Raw per-page detail (e.g. the actual console message text). The
            # summary counts things; this says WHAT. Without it a
            # "1 JS console errors" finding cannot be triaged without re-running
            # the sweep — which is how a console_errors HIGH went undiagnosed.
            "details": [evidence.humanize(d) for d in (f.get("details") or [])][:5],
            # One screenshot per group; the alert and the report share it.
            "screenshots": [],
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "status": "open",
        })
        if SEV_RANK.get(f["severity"], 9) < SEV_RANK.get(g["severity"], 9):
            g["severity"] = f["severity"]
        if f["url"] not in g["urls"]:
            g["urls"].append(f["url"])
        g["_viewport_by_url"].setdefault(f["url"], f.get("viewport", "desktop"))
        # Merge DISTINCT evidence strings instead of keeping only the first.
        # 2026-07-10: an autop_injection group spanning p_wrapped_comment (1 pg)
        # and p_wrapped_script (6 pgs) alerted as "7 pages" but named only the
        # comment signature — the script family was invisible in the alert.
        if ev and ev not in g["evidence"]:
            g["evidence"] = f"{g['evidence']}; {ev}"
            g["summary"] = g["evidence"][:200]
        for d in (f.get("details") or []):
            dh = evidence.humanize(d)
            if dh not in g["details"] and len(g["details"]) < 5:
                g["details"].append(dh)
        if f.get("screenshot") and not g["screenshots"]:
            g["screenshots"].append(f["screenshot"])
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


# check_id subject to the console-errors cross-sweep debounce (gate 3).
# Scoped deliberately: console_errors alone carries 4+ documented
# unconfirmed-positive incidents (feedback_des_audit_no_handler_false_positives.md).
DEBOUNCE_CHECK_IDS = {"console_errors"}


async def route(finding, dry_run=False, report_url=None, reproduce_fn=None, total_pages=None):
    """Severity routing, gated by three structural, check-agnostic defenses
    against single-measurement ghosts — the common thread across every
    documented false-positive family (missed handlers, id-guessed outputs,
    CSS-as-JS-errors, challenge pages, 0x0 shadow buttons):

      1. REPRODUCE-BEFORE-ALERT — any critical/high finding must reproduce
         against a FRESH browser context (via `reproduce_fn`) before it may
         alert at that severity. A finding that fails to reproduce is
         downgraded to medium with flaky=True and only ever reaches the
         digest — never immediate Telegram.
      2. MASS-FINDING PLAUSIBILITY GATE — a check_id firing critical/high on
         more than half the swept pages (`total_pages`) is far more likely to
         be a checker defect than a real site-wide break (every all-pages
         incident on record has been the former: bot-challenge poisoning,
         0x0 shadow buttons). One medium "suspected checker defect" alert
         replaces the storm.
      3. CONSOLE-ERRORS CROSS-SWEEP DEBOUNCE — first sighting of a
         DEBOUNCE_CHECK_IDS check for a site logs at its TRUE severity (bug
         log / MTTR unaffected) but delivers as a medium digest item; a
         second CONSECUTIVE sweep (still open when this one starts) escalates
         normally.

    Order matters and is enforced by early returns: 1 -> 2 -> 3. A finding
    that fails gate 1 never reaches 2 or 3. A reproducing all-pages finding
    is caught by 2 before it can reach 3. Only a reproducing, not-mass
    finding can reach 3.

    Immediate delivery: critical (photo when a screenshot exists). High is
    never sent immediately: it's queued (reporters/alert_queue.py) and
    drained at 08:00-22:00 SGT. Medium/low collected for digest.
    """
    sev = finding["severity"]
    check_id = finding.get("check_id", "")

    if sev in ("critical", "high"):
        # --- Gate 1: reproduce-before-alert ---------------------------
        reproduced = bool(reproduce_fn) and await reproduce_fn(finding)
        if not reproduced:
            finding["severity"] = "medium"
            finding["flaky"] = True
            if not dry_run:
                bug_log.log_finding(finding)
            else:
                print(f"[DRY-RUN] FLAKY (gate 1: did not reproduce, downgraded medium) — "
                      f"{finding['title']} ({len(finding['urls'])} URLs)")
            return  # medium/low handled in send_digests() after the sweep finishes

        # --- Gate 2: mass-finding plausibility gate --------------------
        if total_pages:
            n = len(finding["urls"])
            if n / total_pages > 0.5:
                sample = ", ".join(finding["urls"][:3])
                finding["severity"] = "medium"
                finding["suspected_checker_defect"] = True
                finding["evidence"] = (
                    f"suspected checker defect: {check_id} fired on {n}/{total_pages} pages, "
                    f"verify checker before trusting. Sample URLs: {sample}"
                )
                if not dry_run:
                    bug_log.log_finding(finding)
                else:
                    print(f"[DRY-RUN] SUSPECTED CHECKER DEFECT (gate 2) — {finding['evidence']}")
                return

        # --- Gate 3: console-errors cross-sweep debounce ----------------
        if check_id in DEBOUNCE_CHECK_IDS:
            # Read BEFORE log_finding() mutates state, or every sighting
            # would see itself as "already open".
            already_open = bool(bug_log.open_records(finding["site"], check_ids=[check_id]))
            if not already_open:
                if not dry_run:
                    bug_log.log_finding(finding)  # true severity persisted
                finding["_deliver_medium"] = True
                if dry_run:
                    print(f"[DRY-RUN] FIRST SIGHTING (gate 3: {check_id}) — delivered as medium "
                          f"digest item, true severity {sev} logged")
                return
            # else: already open from a prior sweep -> escalate normally below.

    if not dry_run:
        bug_log.log_finding(finding)
    shot = (finding.get("screenshots") or [None])[0]
    if dry_run:
        urls_preview = "\n  ".join(finding["urls"][:20])
        print(f"[DRY-RUN] {sev.upper()} — {finding['title']} ({len(finding['urls'])} URLs)\n  {urls_preview}\n  evidence: {finding.get('evidence','')[:120]}")
        for d in finding.get("details") or []:
            print(f"  detail: {str(d)[:200]}")
        if shot:
            print(f"  screenshot: {shot}")
        return
    if sev == "critical":
        if shot:
            telegram.send_photo(shot, telegram.format_critical(finding, report_url))
        else:
            telegram.send(telegram.format_critical(finding, report_url))
    elif sev == "high":
        alert_queue.enqueue(finding["site"], "high", finding["check_id"],
                             telegram.format_high(finding, report_url), screenshot=shot)
    # medium / low handled in send_digests() after the sweep finishes


def send_digests(findings, site_name, tier, dry_run=False, mute=(), report_url=None):
    """Emit batched Telegram digests for medium and low after the sweep finishes.
    Check ids in `mute` (yaml: digest_mute_checks) keep the full bug-log
    lifecycle but never ping Telegram — Ed muted em_dash on 2026-07-12.

    `_digest_sev()` treats a gate-3 first-sighting (route() sets
    `_deliver_medium` but leaves `severity` at its true value, on purpose —
    see route()) as medium for digest purposes without touching the record
    bug_log already wrote at true severity."""
    def _digest_sev(f):
        return "medium" if f.get("_deliver_medium") else f["severity"]

    muted = [f for f in findings if f["check_id"] in mute and _digest_sev(f) in ("medium", "low")]
    if muted:
        print(f"DES: digest muted for {len(muted)} finding(s): {sorted(f['check_id'] for f in muted)}")
    medium = [f for f in findings if _digest_sev(f) == "medium" and f["check_id"] not in mute]
    low = [f for f in findings if _digest_sev(f) == "low" and f["check_id"] not in mute]
    period = {"critical": "daily", "weekly": "weekly", "deep": "bi-weekly"}.get(tier, tier)
    if medium:
        msg = telegram.format_digest(medium, "medium", site_name, period, report_url=report_url)
        if dry_run:
            print(f"[DRY-RUN] MEDIUM DIGEST — {len(medium)} findings\n{msg}")
        else:
            telegram.send(msg)
    # low only goes out on the deep tier (bi-weekly), so noise stays low
    if low and tier == "deep":
        msg = telegram.format_digest(low, "low", site_name, "bi-weekly", report_url=report_url)
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
    ap.add_argument("--delay", type=float, default=None,
                    help="seconds to pause between requests (default: site crawl_delay_seconds, else 1.5)")
    args = ap.parse_args()

    site = load_site(args.site)
    if not site or site.get("active") is False:
        print(f"site '{args.site}' is inactive or missing")
        return
    if site.get("sitemap") in (None, "TBD"):
        print(f"site '{args.site}' has no sitemap configured yet")
        return

    # Report identity computed BEFORE any alert is sent: critical/high
    # alerts and digests all link to this report via report_url.
    sweep_started = datetime.now(timezone.utc)
    report_rel = html_report.report_rel_path(site["name"], args.tier, sweep_started)
    report_url = html_report.blob_url(report_rel)

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

    # Daily tier includes one phone viewport so check_mobile_menu (phone-only)
    # runs every day under the full gate stack. Replaces the retired claude.ai
    # "TRW Mobile Menu Audit" trigger (disabled 2026-08-09), which used pre-v5
    # selectors, concurrency 6 (tripped the Cloudflare challenge) and alerted
    # without reproduction.
    viewports = ["desktop", "phone_ios"] if args.tier == "critical" else list(DEVICES.keys())

    # Politeness + crawl-health guard. Defaults are deliberately gentle: Des is
    # a monitor, not a load test, and a challenged crawl produces garbage
    # findings (see src/crawl_guard.py). Tunable per site in sites/<site>.yaml.
    guard = crawl_guard.SweepGuard(
        delay=args.delay if args.delay is not None else float(site.get("crawl_delay_seconds", 1.5)),
        abort_ratio=float(site.get("crawl_abort_ratio", 0.20)),
        min_pages=int(site.get("crawl_abort_min_pages", 10)),
    )
    print(f"DES: scanning {len(urls)} URLs on {len(viewports)} viewports — {args.tier} "
          f"(delay {guard.delay}s, abort at >{guard.abort_ratio:.0%} blocked)")

    from playwright.async_api import async_playwright
    all_findings = []
    async with async_playwright() as pw:
        for idx, u in enumerate(urls):
            if idx:
                await guard.polite_pause()
            # Fault isolation: one exploding page must not discard the whole
            # sweep's findings (previously a single crash lost everything).
            try:
                per_vp = await render_and_check(pw, u, site, viewports, guard)
            except Exception as e:
                all_findings.append({"url": u, "viewport": "n/a", "check": "sweep_page_crash", "severity": "high", "evidence": f"render_and_check raised: {e}"})
                continue
            for vp, fs in per_vp.items():
                all_findings.extend(fs)
            # Systematically challenged: everything gathered so far is suspect.
            if guard.should_abort():
                print(f"DES: ABORTING sweep — {guard.pages_blocked}/{guard.pages_attempted} pages bot-challenged")
                break

        # Curated-finding recheck (Des v2). Re-test the open curated bugs from
        # the manual deep audit whose check_ids the normal battery never emits,
        # so they can auto-close once fixed live instead of freezing forever.
        # Runs in the SAME gate as reconcile() (full sweeps only: not dry-run,
        # not --limit) and BEFORE dedupe/route/reconcile, sharing this live
        # Playwright instance. Emitted findings flow through the normal
        # dedupe -> route -> log_finding path (bumping last_seen) and their ids
        # land in current_check_ids so reconcile leaves still-broken ones open.
        # A crashed/unverifiable recheck emits a low keep-open finding, never a
        # silent absence — so reconcile can't false-close a bug we didn't verify.
        if not args.dry_run and not args.limit and not guard.aborted:
            open_curated = bug_log.open_records(site["name"], set(recheck.REGISTRY))
            if open_curated:
                print(f"DES: rechecking {len(open_curated)} open curated finding(s)")
                rc_findings = await recheck.run_site_rechecks(pw, site, open_curated, urls)
                all_findings.extend(rc_findings)

        # An aborted sweep reports ONE fact: we were bot-challenged and saw
        # nothing we can trust. Everything gathered before the abort is
        # discarded rather than shipped — partial data from a challenged
        # crawl is how 159 fake criticals got sent on 2026-08-02.
        if guard.aborted:
            all_findings = [guard.abort_finding(site["url"])]

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

        # Reproduce-before-alert (gate 1) needs a live browser, so route()
        # runs here, inside the Playwright context, instead of after it
        # closes. total_pages (gate 2's denominator) is the count of
        # distinct pages this sweep actually visited — fixed by now, and
        # never touched by reproduction's own throwaway guard.
        total_pages = guard.pages_attempted

        async def _reproduce(finding):
            return await reproduce_finding(pw, finding, site, guard, sitemap_urls=urls)

        print(f"DES: {len(findings)} unique findings")
        for f in findings:
            await route(f, dry_run=args.dry_run, report_url=report_url,
                        reproduce_fn=_reproduce, total_pages=total_pages)
        send_digests(findings, site["name"], args.tier, dry_run=args.dry_run,
                     mute=set(site.get("digest_mute_checks") or []), report_url=report_url)

    # Drain the cross-run HIGH queue for this site if we're inside the
    # 08:00-22:00 SGT window; this is what makes D-04 work across runs,
    # regardless of whether THIS sweep produced any HIGH finding.
    if not args.dry_run:
        n = alert_queue.flush_due(site["name"], telegram.send, telegram.send_photo)
        if n:
            print(f"DES: flushed {n} queued high alert(s) (inside 08:00-22:00 SGT window)")

    # Lifecycle: close issues that stopped firing (full sweeps only — a
    # --limit run hasn't seen every page and must not mass-close). A sweep that
    # was crawl-blocked on any meaningful share of pages must not close either:
    # a bug on a page we never loaded is not a bug that got fixed.
    if not args.dry_run and not args.limit and not guard.reconcile_is_safe():
        print(f"DES: skipping reconcile — {guard.pages_blocked}/{guard.pages_attempted} pages crawl-blocked "
              f"({guard.blocked_ratio:.0%}); cannot distinguish 'fixed' from 'not seen'")
    elif not args.dry_run and not args.limit:
        closed = bug_log.reconcile(site["name"], {f["check_id"] for f in findings})
        if closed:
            print(f"DES: {len(closed)} issue(s) auto-closed as fixed: {', '.join(closed)}")

    # Self-contained per-sweep report, built LAST so it can include everything
    # above (findings, screenshots, what-changed). In dry-run too, so this
    # step is inspectable without sending anything.
    path = html_report.build(site["name"], args.tier, findings, sweep_started, report_rel,
                              waived=[f for f, _ in waived], aborted=guard.aborted)
    print(f"DES: report {path}\nDES: report url {report_url}")

    # Alerts that failed to deliver must fail the run — a green sweep whose
    # Telegram went nowhere is how criticals get missed.
    if telegram.UNDELIVERED and not args.dry_run:
        print(f"DES: FATAL — {len(telegram.UNDELIVERED)} alert(s) undelivered")
        sys.exit(3)


if __name__ == "__main__":
    asyncio.run(main())
