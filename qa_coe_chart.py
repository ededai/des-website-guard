#!/usr/bin/env python3
"""
Des — automated health check for the COE interactive chart.

Page: https://therightworkshop.com/coe-results/
Owner: Des (website guard). READ-ONLY. Never writes to the website.

Background
----------
The COE chart was fixed for mobile (2026-06). Previously it rendered as an
unusable ~46px-tall sliver on phones (fixed wide SVG viewBox). The fix makes the
MOBILE svg a tall portrait plot (viewBox ~"0 0 292 385", rendered height ~385px),
while DESKTOP keeps the original wide "0 0 1100 280". This check pings ONLY if
that regresses (e.g. a future data refresh reverting the fix).

Two run modes
-------------
1. FULL (local / playwright in CI): renders the page in headless chromium and
   asserts the *rendered DOM* — SVG height, viewBox orientation, drawn lines,
   horizontal overflow, value-card 2-up layout. This is the real check.

   - Local: uses the gstack `browse` binary if present
     (~/.claude/skills/gstack/browse/dist/browse).
   - CI: uses Playwright if importable (workflow installs chromium).

2. SERVER fallback (plain requests, no browser): GETs the page HTML and verifies
   the chart container + snippet markers exist (coe-chart-v5 / v5-svg / v5-card).
   It CANNOT verify rendered SVG height/lines because the SVG is drawn client-side
   by JS. Used only when no browser engine is available (e.g. a minimal CI runner).
   A server-only pass is reported as a DEGRADED pass, not a full pass.

Severity (per des-website-guard SKILL.md)
-----------------------------------------
A broken/sliver chart on a top page = mobile/desktop parity break + content not
drawn. Routed as HIGH. In-charge: Bryan (TRW). Telegram prefix [DES].

Exit codes: 0 = all pass (silent, no Telegram). 1 = any failure (Telegram sent).
"""

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import requests

PAGE_URL = "https://therightworkshop.com/coe-results/"
SITE = "TRW"
IN_CHARGE = "Bryan"
CHECK_ID = "coe-chart-mobile-portrait"
TITLE = "COE interactive chart regression"

# gstack browse binary (local only; absent on GitHub Actions runners)
BROWSE_BIN = Path.home() / ".claude/skills/gstack/browse/dist/browse"

# JS evaluated in the page to snapshot chart state. Returns a JSON string.
PROBE_JS = (
    "(() => {"
    "const s=document.querySelector('.coe-chart-v5 svg.v5-svg');"
    "if(!s)return JSON.stringify({err:'no-svg'});"
    "const b=s.getBoundingClientRect();"
    "const cards=[...document.querySelectorAll('.v5-card')];"
    "const t0=cards[0]?Math.round(cards[0].getBoundingClientRect().top):null;"
    "const t1=cards[1]?Math.round(cards[1].getBoundingClientRect().top):null;"
    "return JSON.stringify({"
    "h:Math.round(b.height),"
    "vb:s.getAttribute('viewBox'),"
    "lines:s.querySelectorAll('path.line').length,"
    "overflow:document.documentElement.scrollWidth-window.innerWidth,"
    "cardCount:cards.length,card0top:t0,card1top:t1"
    "});})()"
)

# Server-side markers — what we can still confirm with plain requests.
SERVER_MARKERS = ["coe-chart-v5", "v5-svg", "v5-card"]


def cb_url():
    """Cache-bust the URL on every run."""
    return f"{PAGE_URL}?cb={random.randint(10_000_000, 99_999_999)}"


# ---------------------------------------------------------------------------
# Rendering backends
# ---------------------------------------------------------------------------

def render_with_browse(url, width, height):
    """Use the local gstack browse binary. Returns the parsed probe dict."""
    b = str(BROWSE_BIN)
    subprocess.run([b, "viewport", f"{width}x{height}"], capture_output=True, timeout=60)
    subprocess.run([b, "goto", url], capture_output=True, timeout=90)
    subprocess.run([b, "wait", "--networkidle"], capture_output=True, timeout=90)
    out = subprocess.run([b, "js", PROBE_JS], capture_output=True, text=True, timeout=60)
    raw = out.stdout.strip()
    return json.loads(raw)


def render_with_playwright(url, width, height):
    """Use Playwright (CI). Returns the parsed probe dict."""
    from playwright.sync_api import sync_playwright  # noqa: local import — optional dep

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle", timeout=60_000)
        raw = page.evaluate(PROBE_JS)
        browser.close()
    return json.loads(raw)


def pick_renderer():
    """full browser renderer if available, else None (→ server fallback)."""
    if BROWSE_BIN.exists():
        return ("browse", render_with_browse)
    try:
        import playwright.sync_api  # noqa: F401
        return ("playwright", render_with_playwright)
    except Exception:
        return (None, None)


# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------

def check_mobile(state):
    """Returns list of failure strings (empty = pass)."""
    fails = []
    if state.get("err"):
        return [f"MOBILE: chart SVG not found ({state['err']})"]
    h = state.get("h", 0)
    vb = state.get("vb") or ""
    lines = state.get("lines", 0)
    overflow = state.get("overflow", 999)
    c0, c1 = state.get("card0top"), state.get("card1top")

    if not (h > 250):
        fails.append(f"MOBILE: chart height {h}px is a sliver (expected > 250)")

    parts = vb.split()
    if len(parts) == 4:
        w_vb, h_vb = float(parts[2]), float(parts[3])
        if not (h_vb > w_vb):
            fails.append(f"MOBILE: viewBox '{vb}' is not portrait (H must exceed W)")
    else:
        fails.append(f"MOBILE: viewBox '{vb}' unreadable (expected like '0 0 292 385')")

    if not (lines >= 3):
        fails.append(f"MOBILE: only {lines} path.line drawn (expected >= 3)")

    if not (overflow <= 2):
        fails.append(f"MOBILE: horizontal overflow {overflow}px (scrollWidth > innerWidth)")

    if c0 is None or c1 is None:
        fails.append(f"MOBILE: fewer than 2 value cards present (found {state.get('cardCount')})")
    elif abs(c0 - c1) > 5:
        fails.append(f"MOBILE: value cards not 2-up — card tops {c0} vs {c1} (>5px apart)")

    return fails


def check_desktop(state):
    fails = []
    if state.get("err"):
        return [f"DESKTOP: chart SVG not found ({state['err']})"]
    vb = state.get("vb") or ""
    if vb != "0 0 1100 280":
        fails.append(f"DESKTOP: viewBox '{vb}' changed (expected '0 0 1100 280' — desktop must stay untouched)")
    return fails


def check_server_markers():
    """Plain-requests fallback. Returns (fails, observed_dict)."""
    r = requests.get(cb_url(), timeout=30, headers={"User-Agent": "Des-WebsiteGuard/1.0"})
    r.raise_for_status()
    html = r.text
    present = {m: (m in html) for m in SERVER_MARKERS}
    fails = [f"SERVER: chart marker '{m}' missing from rendered HTML" for m, ok in present.items() if not ok]
    return fails, {"http_status": r.status_code, "markers": present}


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_telegram(fails, observed):
    """POST a HIGH alert to the TRW Telegram chat. Only called on failure."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    # Des uses TELEGRAM_CHAT_ID; trw-cole/.env uses TG_CHAT_ID (same chat). Accept either.
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("TG_CHAT_ID")
    summary = "; ".join(fails)
    msg = (
        f"[DES] HIGH — {TITLE}\n"
        f"Site: {SITE} | Severity: high | Page: {PAGE_URL}\n"
        f"Check: {CHECK_ID}\n"
        f"{len(fails)} failed assertion(s):\n"
        + "\n".join("- " + f for f in fails) + "\n"
        f"Observed: {json.dumps(observed)}\n"
        f"In-charge: {IN_CHARGE}\n"
        f"Codi reroutes to {IN_CHARGE} now."
    )
    if not (token and chat_id):
        print(f"[DES] (telegram skipped, missing TELEGRAM_BOT_TOKEN/CHAT_ID): {summary}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "text": msg, "disable_web_page_preview": "true"},
            timeout=15,
        )
        resp.raise_for_status()
        print("[DES] Telegram alert sent.")
    except Exception as e:  # never swallow silently — log + still exit non-zero
        print(f"[DES] Telegram send FAILED: {e}\nMessage was:\n{msg}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    engine, render = pick_renderer()
    observed = {"engine": engine or "server-fallback"}

    if engine is None:
        # No browser engine. Verify markers only — DEGRADED, cannot prove SVG height.
        print("[DES] No browser engine (browse/playwright) available — server-marker fallback only.")
        fails, server_obs = check_server_markers()
        observed.update(server_obs)
        if fails:
            print(json.dumps({"result": "FAIL", "observed": observed, "fails": fails}, indent=2))
            send_telegram(fails, observed)
            return 1
        print(json.dumps({"result": "PASS (DEGRADED — markers only, SVG not rendered)", "observed": observed}, indent=2))
        return 0

    # Full rendered-DOM check.
    mobile = render(cb_url(), 390, 844)
    desktop = render(cb_url(), 1280, 900)
    observed["mobile"] = mobile
    observed["desktop"] = desktop

    fails = check_mobile(mobile) + check_desktop(desktop)

    if fails:
        print(json.dumps({"result": "FAIL", "observed": observed, "fails": fails}, indent=2))
        send_telegram(fails, observed)
        return 1

    print(json.dumps({"result": "PASS", "observed": observed}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
