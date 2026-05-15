"""
Telegram reporter. Single shared TRW bot/chat. Messages prefixed with [DES].

Severity routing (configured in src/run.py):
- critical: immediate
- high:     immediate (queued to 08:00 SGT business hours in production)
- medium:   batched into end-of-sweep digest (one message per sweep run)
- low:      batched into bi-weekly digest (only on the deep-sweep tier)
"""
import os

import requests


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        print(f"[DES] (telegram skipped, missing env): {text}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}, timeout=15)
    r.raise_for_status()


def format_critical(finding):
    return (
        f"[DES] CRITICAL — {finding['title']}\n"
        f"Site: {finding['site']} | Severity: critical | Affected: {len(finding['urls'])} pages\n"
        f"{finding['summary']}\n"
        + "Top URLs:\n" + "\n".join("- " + u for u in finding["urls"][:3]) + "\n"
        + f"In-charge: {finding['in_charge']}"
    )


def format_high(finding):
    return (
        f"[DES] HIGH — {finding['title']}\n"
        f"Site: {finding['site']} | Severity: high | Affected: {len(finding['urls'])} pages\n"
        f"{finding['summary']}\n"
        + f"In-charge: {finding['in_charge']}\n"
        + f"Codi reroutes to {finding['in_charge']} now."
    )


def format_digest(findings, severity_label, site, period_label):
    """Batched digest for medium/low — one Telegram message listing all findings."""
    if not findings:
        return None
    header = f"[DES] {severity_label.upper()} DIGEST — {site} ({period_label})\n{len(findings)} finding(s)\n"
    lines = []
    for f in findings:
        lines.append(
            f"\n• {f['title']} ({len(f['urls'])} URL{'s' if len(f['urls']) != 1 else ''})"
            f"\n  {f['summary'][:140]}"
            f"\n  In-charge: {f['in_charge']}"
        )
    return header + "".join(lines)
