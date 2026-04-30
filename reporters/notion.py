import os
from datetime import datetime, timezone

try:
    from notion_client import Client
except ImportError:
    Client = None


def get_client():
    token = os.environ.get("NOTION_TOKEN")
    if not token or Client is None:
        return None
    return Client(auth=token)


def log_finding(finding):
    db_id = os.environ.get("NOTION_DES_DB_ID")
    client = get_client()
    if not (client and db_id):
        print(f"[DES] (notion skipped, missing env): {finding['title']}")
        return None
    props = {
        "Title": {"title": [{"text": {"content": finding["title"]}}]},
        "Severity": {"select": {"name": finding["severity"]}},
        "Status": {"select": {"name": finding.get("status", "open")}},
        "Site": {"select": {"name": finding["site"]}},
        "InCharge": {"select": {"name": finding["in_charge"]}},
        "FirstSeen": {"date": {"start": finding.get("first_seen", datetime.now(timezone.utc).isoformat())}},
        "LastSeen": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        "URLCount": {"number": len(finding["urls"])},
        "URLs": {"rich_text": [{"text": {"content": "\n".join(finding["urls"][:50])}}]},
        "CheckID": {"rich_text": [{"text": {"content": finding.get("check_id", "")}}]},
        "Evidence": {"rich_text": [{"text": {"content": finding.get("evidence", "")[:1900]}}]},
    }
    page = client.pages.create(parent={"database_id": db_id}, properties=props)
    return page.get("url")


def close_finding(notion_page_id, mttr_hours=None):
    client = get_client()
    if not client:
        return
    props = {
        "Status": {"select": {"name": "fixed"}},
        "FixedAt": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
    }
    if mttr_hours is not None:
        props["MTTR_hours"] = {"number": mttr_hours}
    client.pages.update(page_id=notion_page_id, properties=props)
