"""
Crawl-health guard — kills the "challenge page judged as a broken page" family
of false positives.

WHAT HAPPENED (2026-08-02, therightworkshop.com):
  Mid-sweep, the host started bot-challenging Des's headless crawler. 159 URLs
  came back 403. The old pipeline logged the 403s AND, on the viewports where
  the challenge body was served with a normal status, kept running the full DOM
  battery against the challenge HTML. A challenge page has no nav, no footer and
  no mobile drawer — so Des emitted 159 bogus CRITICAL `mobile_menu /
  no_drawer` findings (plus missing_nav) against a site that was, at that exact
  moment, returning 200 and the full chrome-v5 drawer to every normal request.

RULES ENFORCED HERE:
  1. A page that did not come back as a real page is CRAWL-BLOCKED. No DOM,
     visual or content check may run against it, and it can never produce a
     critical or high finding. Absence of evidence is not evidence of absence.
  2. Every crawl-blocked page rolls into ONE medium `crawl_blocked` finding with
     the affected URL list — never one finding per page.
  3. Be polite: pace requests, and back off + retry once before calling a page
     blocked. Most 403s under crawl are self-inflicted rate limiting.
  4. If a large enough share of the sweep is crawl-blocked, the sweep itself is
     untrustworthy. Abort and report that single fact instead of reporting
     hundreds of page findings derived from challenge pages.

Related lineage: feedback_des_audit_no_handler_false_positives.md
"""
import asyncio

# Statuses that mean "the edge refused us", not "the page is broken".
CHALLENGE_STATUSES = {401, 403, 405, 406, 409, 418, 429}

# 503 is ambiguous: Cloudflare's interstitial uses it, but so does a genuinely
# down origin. Only treat it as a challenge when the body says so.
AMBIGUOUS_STATUSES = {503}

# Unmistakable challenge/WAF markers — trusted at any page size.
STRONG_CHALLENGE_SIGNATURES = (
    "cf-browser-verification",
    "cf_chl_opt",
    "__cf_chl",
    "cdn-cgi/challenge-platform",
    "_incapsula_resource",
    "sucuri website firewall",
    "attention required! | cloudflare",
    "ddos protection by",
    "enable javascript and cookies to continue",
)

# Weaker phrases that also occur in ordinary prose. Only trusted on a body small
# enough to be an interstitial rather than a real article.
WEAK_CHALLENGE_SIGNATURES = (
    "just a moment",
    "checking your browser",
    "verifying you are human",
    "please verify you are a human",
    "you have been blocked",
    "access denied",
    "too many requests",
    "rate limited",
    "request unsuccessful",
)

# An interstitial is small. A real TRW page is ~250KB of HTML.
CHALLENGE_BODY_MAX = 30000


def challenge_signature(html):
    """Return the matched bot-challenge signature, or None."""
    if not html:
        return None
    low = html.lower()
    for sig in STRONG_CHALLENGE_SIGNATURES:
        if sig in low:
            return sig
    if len(html) <= CHALLENGE_BODY_MAX:
        for sig in WEAK_CHALLENGE_SIGNATURES:
            if sig in low:
                return f"{sig} (body {len(html)}B)"
    return None


def classify_response(status, html=None):
    """Classify a navigation result.

    Returns (verdict, evidence) where verdict is one of:
      'ok'           — a real page; safe to run the check battery
      'blocked'      — bot challenge / access control; SKIP all checks
      'dead'         — 404/410 on a sitemap-published URL; genuine high finding
      'server_error' — 5xx from the origin; genuine critical finding

    A 'blocked' verdict must never be escalated above medium: we did not see the
    page, so we know nothing about it.
    """
    sig = challenge_signature(html)

    if status is not None:
        if status in CHALLENGE_STATUSES:
            detail = sig or "bot challenge / access control under crawl"
            return "blocked", f"HTTP {status} ({detail})"
        if status in (404, 410):
            return "dead", f"HTTP {status} on sitemap URL"
        if status in AMBIGUOUS_STATUSES and sig:
            return "blocked", f"HTTP {status} ({sig})"
        if status >= 500:
            return "server_error", f"HTTP {status}"
        if status >= 400:
            return "blocked", f"HTTP {status} (unexpected status under crawl)"

    if sig:
        shown = status if status is not None else "n/a"
        return "blocked", f"HTTP {shown} but body matched bot-challenge signature: {sig}"

    return "ok", ""


class SweepGuard:
    """Politeness pacing + crawl-block accounting + sweep abort decision."""

    def __init__(self, delay=1.5, abort_ratio=0.20, min_pages=10,
                 retry_backoff_base=5.0, max_retries=1):
        self.delay = max(0.0, float(delay))
        self.abort_ratio = float(abort_ratio)
        self.min_pages = int(min_pages)
        self.retry_backoff_base = float(retry_backoff_base)
        self.max_retries = int(max_retries)
        self.pages_attempted = 0
        self.pages_blocked = 0
        self.blocked_urls = []
        self.aborted = False

    def record(self, url, blocked, evidence=""):
        self.pages_attempted += 1
        if blocked:
            self.pages_blocked += 1
            if url not in self.blocked_urls:
                self.blocked_urls.append(url)
            print(f"DES: crawl-blocked {url} — {evidence} (checks skipped)")

    @property
    def blocked_ratio(self):
        if not self.pages_attempted:
            return 0.0
        return self.pages_blocked / self.pages_attempted

    def should_abort(self):
        """True once the sweep has seen enough pages to know it is being
        challenged systematically. Latches, so the caller can test it freely."""
        if self.aborted:
            return True
        if self.pages_attempted >= self.min_pages and self.blocked_ratio > self.abort_ratio:
            self.aborted = True
        return self.aborted

    def reconcile_is_safe(self):
        """Auto-closing findings requires having actually seen the pages. A sweep
        that was partly blocked would 'fix' bugs it simply never looked at."""
        return not self.aborted and self.blocked_ratio <= 0.02

    async def polite_pause(self, scale=1.0):
        """Pace requests. scale<1 is used between viewports of the same URL so a
        5-viewport page doesn't cost 5 full delays (the deep tier would blow the
        120-minute Actions budget)."""
        if self.delay:
            await asyncio.sleep(self.delay * scale)

    async def backoff(self, attempt, url="", evidence=""):
        """Exponential backoff before a retry: 5s, 10s, 20s..."""
        wait = self.retry_backoff_base * (2 ** attempt)
        print(f"DES: backing off {wait:.0f}s — {evidence or 'bot challenge'} on {url} "
              f"(retry {attempt + 1}/{self.max_retries})")
        await asyncio.sleep(wait)

    def abort_finding(self, site_url):
        sample = ", ".join(self.blocked_urls[:5])
        return {
            "url": site_url,
            "viewport": "n/a",
            "check": "sweep_aborted_bot_challenge",
            "severity": "high",
            "evidence": (
                f"sweep aborted: {self.pages_blocked}/{self.pages_attempted} pages "
                f"({self.blocked_ratio:.0%}) returned a bot challenge, over the "
                f"{self.abort_ratio:.0%} threshold. No page-level findings emitted — "
                f"DOM checks run against challenge pages produce false criticals. "
                f"Sample blocked URLs: {sample}"
            ),
        }
