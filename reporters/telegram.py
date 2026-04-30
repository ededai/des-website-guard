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
        + (f"Notion: {finding['notion_url']}\n" if finding.get("notion_url") else "")
        + f"In-charge: {finding['in_charge']}"
    )


def format_high(finding):
    return (
        f"[DES] HIGH — {finding['title']}\n"
        f"Site: {finding['site']} | Severity: high | Affected: {len(finding['urls'])} pages\n"
        f"{finding['summary']}\n"
        + (f"Notion: {finding['notion_url']}\n" if finding.get("notion_url") else "")
        + f"In-charge: {finding['in_charge']}\n"
        + f"Codi reroutes to {finding['in_charge']} now."
    )
