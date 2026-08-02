"""
Telegram reporter. Single shared TRW bot/chat. Messages prefixed with [DES],
sent HTML-mode with a bold per-site emoji header, and split under the 4096
sendMessage limit (1024 for a sendPhoto caption).

Severity routing (configured in src/run.py):
- critical: immediate, photo + caption when a screenshot exists
- high:     queued (reporters/alert_queue.py), flushed at 08:00 SGT
- medium:   batched into end-of-sweep digest (one message per sweep run)
- low:      batched into bi-weekly digest (only on the deep-sweep tier)
"""
import os

import requests

from reporters import evidence

UNDELIVERED = []  # alerts that failed to send; run.py fails the sweep if non-empty

MAX_MSG = 4096
MAX_CAPTION = 1024
SITE_EMOJI = {"TRW": "🔧", "AURA": "🐾"}
DEFAULT_EMOJI = "🌐"

# Room reserved on every continuation chunk for the "[DES] (cont. i/n)\n"
# prefix, so a chunk already packed to (limit - _PREFIX_RESERVE) never
# overflows once the prefix is added.
_PREFIX_RESERVE = 24


def _emoji(site):
    return SITE_EMOJI.get(str(site).strip().upper(), DEFAULT_EMOJI)


def _chunks(text, limit=MAX_MSG):
    """Accumulate whole lines up to `limit`. A single line longer than the
    limit is hard-split. Chunks after the first are prefixed
    '[DES] (cont. i/n)\\n' so the [DES] convention holds on every delivered
    message; room for that prefix is reserved while packing so no content is
    lost trimming it back on afterward."""
    lines = text.split("\n")
    raw = []
    cur = []
    cur_len = 0

    def cap():
        return limit if not raw else limit - _PREFIX_RESERVE

    for line in lines:
        c = cap()
        while len(line) > c:
            if cur:
                raw.append("\n".join(cur))
                cur, cur_len = [], 0
                c = cap()
            raw.append(line[:c])
            line = line[c:]
            c = cap()
        add_len = len(line) + (1 if cur else 0)
        if cur and cur_len + add_len > c:
            raw.append("\n".join(cur))
            cur, cur_len = [line], len(line)
        else:
            cur.append(line)
            cur_len += add_len
    if cur:
        raw.append("\n".join(cur))
    if not raw:
        raw = [""]

    if len(raw) == 1:
        return raw
    n = len(raw)
    out = [raw[0]]
    for i, chunk in enumerate(raw[1:], start=2):
        out.append(f"[DES] (cont. {i}/{n})\n{chunk}")
    return out


def send(text, parse_mode="HTML"):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        # Never fail silent: record it so the sweep exits non-zero and CI
        # shows red instead of a green run whose alerts went nowhere.
        UNDELIVERED.append(text)
        print(f"[DES] ALERT NOT DELIVERED (telegram credentials missing): {text[:200]}")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok = True
    for chunk in _chunks(text, MAX_MSG):
        try:
            r = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": "true",
                },
                timeout=15,
            )
            r.raise_for_status()
        except requests.RequestException as e:
            # Do NOT print the exception or URL: both can contain the bot token,
            # and Actions logs are public. Status code + reason only.
            status = getattr(getattr(e, "response", None), "status_code", "n/a")
            UNDELIVERED.append(text)
            print(f"[DES] ALERT NOT DELIVERED (telegram HTTP {status}): {text[:200]}")
            ok = False
    return ok


def _truncate_caption(text, limit=MAX_CAPTION):
    if len(text) <= limit:
        return text, ""
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut], text[cut:].lstrip("\n")


def send_photo(image_path, caption, parse_mode="HTML"):
    """POST sendPhoto with a caption capped at MAX_CAPTION on a line boundary.
    Any failure (missing credentials, missing file, request error) falls back
    to send(caption) so evidence is never silently lost. UNDELIVERED is only
    recorded if that fallback also fails. A truncated caption's remainder is
    sent as a follow-up send() call."""
    head, rest = _truncate_caption(caption, MAX_CAPTION)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id and image_path and os.path.exists(image_path):
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            with open(image_path, "rb") as fh:
                r = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": head, "parse_mode": parse_mode},
                    files={"photo": fh},
                    timeout=30,
                )
                r.raise_for_status()
            if rest:
                send(rest, parse_mode=parse_mode)
            return True
        except (requests.RequestException, OSError) as e:
            status = getattr(getattr(e, "response", None), "status_code", "n/a")
            print(f"[DES] photo send failed (telegram HTTP {status}), falling back to text")
    return send(caption, parse_mode=parse_mode)


def format_critical(finding, report_url=None):
    site = finding.get("site", "")
    emoji = _emoji(site)
    title = evidence.escape_html(finding.get("title", ""))
    summary = evidence.escape_html(finding.get("summary", ""))
    urls = finding.get("urls", [])
    in_charge = evidence.escape_html(finding.get("in_charge", ""))
    lines = [
        f"<b>{emoji} [DES] CRITICAL — {title}</b>",
        f"Site: {evidence.escape_html(site)} | Severity: critical | Affected: {len(urls)} pages",
        summary,
        "Top URLs:",
    ]
    lines += ["- " + evidence.escape_html(u) for u in urls[:3]]
    lines.append(f"In-charge: {in_charge}")
    if report_url:
        lines.append(f'📄 <a href="{report_url}">Full sweep report</a>')
    return "\n".join(lines)


def format_high(finding, report_url=None):
    # Top URLs included since 2026-07-10: a high alert without page names
    # cost a full site re-scan to locate the 7 autop pages.
    site = finding.get("site", "")
    emoji = _emoji(site)
    title = evidence.escape_html(finding.get("title", ""))
    summary = evidence.escape_html(finding.get("summary", ""))
    urls = finding.get("urls", [])
    in_charge = evidence.escape_html(finding.get("in_charge", ""))
    lines = [
        f"<b>{emoji} [DES] HIGH — {title}</b>",
        f"Site: {evidence.escape_html(site)} | Severity: high | Affected: {len(urls)} pages",
        summary,
        "Top URLs:",
    ]
    lines += ["- " + evidence.escape_html(u) for u in urls[:3]]
    lines.append(f"In-charge: {in_charge}")
    lines.append(f"Codi reroutes to {in_charge} now.")
    if report_url:
        lines.append(f'📄 <a href="{report_url}">Full sweep report</a>')
    return "\n".join(lines)


def format_digest(findings, severity_label, site, period_label, report_url=None):
    """Batched digest for medium/low: one Telegram message listing all findings."""
    if not findings:
        return None
    emoji = _emoji(site)
    header = (
        f"<b>{emoji} [DES] {severity_label.upper()} DIGEST — {evidence.escape_html(site)} "
        f"({period_label})</b>\n{len(findings)} finding(s)\n"
    )
    lines = []
    for f in findings:
        title = evidence.escape_html(f.get("title", ""))
        urls = f.get("urls", [])
        summary = evidence.escape_html(f.get("summary", ""))[:140]
        in_charge = evidence.escape_html(f.get("in_charge", ""))
        lines.append(
            f"\n• {title} ({len(urls)} URL{'s' if len(urls) != 1 else ''})"
            f"\n  {summary}"
            f"\n  In-charge: {in_charge}"
        )
    out = header + "".join(lines)
    if report_url:
        out += f'\n📄 <a href="{report_url}">Full sweep report</a>'
    return out
