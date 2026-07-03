"""
Link integrity sweep. Reads cap/<site>/metrics.jsonl link inventories,
dedupes, checks each URL once (internal GET, external HEAD->GET), maps
failures back to the pages that link to them.

Usage: python link_check.py <site_base_url> <metrics.jsonl> <out.json>
"""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests

BASE = sys.argv[1].rstrip("/")
METRICS = sys.argv[2]
OUT = sys.argv[3]

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 DesAudit/1.0"}

pages = [json.loads(l) for l in open(METRICS) if l.strip()]
internal_map, external_map = {}, {}
for p in pages:
    d = p.get("desktop") or {}
    for path in d.get("internalLinks", []):
        internal_map.setdefault(urljoin(BASE + "/", path), set()).add(p["url"])
    for ext in d.get("externalLinks", []):
        external_map.setdefault(ext, set()).add(p["url"])

print(f"unique internal: {len(internal_map)}, external: {len(external_map)}", flush=True)

def check(url, internal):
    try:
        if internal:
            r = requests.get(url, headers=UA, timeout=20, allow_redirects=True, stream=True)
        else:
            r = requests.head(url, headers=UA, timeout=15, allow_redirects=True)
            if r.status_code in (403, 405, 400, 501):
                r = requests.get(url, headers=UA, timeout=15, allow_redirects=True, stream=True)
        chain = [h.status_code for h in r.history]
        final = r.url
        r.close()
        return {"url": url, "status": r.status_code, "chain": chain, "final": final if final.rstrip("/") != url.rstrip("/") else None}
    except Exception as e:
        return {"url": url, "status": None, "err": f"{type(e).__name__}: {str(e)[:120]}"}

results = {"internal": [], "external": []}
with ThreadPoolExecutor(max_workers=12) as ex:
    for res in ex.map(lambda u: check(u, True), internal_map.keys()):
        res["linkedFrom"] = sorted(internal_map[res["url"]])[:15]
        res["linkedFromCount"] = len(internal_map[res["url"]])
        results["internal"].append(res)
with ThreadPoolExecutor(max_workers=10) as ex:
    for res in ex.map(lambda u: check(u, False), external_map.keys()):
        res["linkedFrom"] = sorted(external_map[res["url"]])[:10]
        res["linkedFromCount"] = len(external_map[res["url"]])
        results["external"].append(res)

bad_int = [r for r in results["internal"] if not r["status"] or r["status"] >= 400]
red_int = [r for r in results["internal"] if r["status"] and r["status"] < 400 and (r["chain"] or r["final"])]
bad_ext = [r for r in results["external"] if not r["status"] or r["status"] >= 400]
json.dump(results, open(OUT, "w"), indent=1)
print(f"BROKEN internal: {len(bad_int)}", flush=True)
for r in bad_int:
    print(f"  {r['status']} {r['url']} <- {r['linkedFromCount']} pages e.g. {r['linkedFrom'][:3]}", flush=True)
print(f"REDIRECTING internal: {len(red_int)}", flush=True)
for r in red_int[:30]:
    print(f"  {r['url']} -> {r.get('final')} {r['chain']}", flush=True)
print(f"BROKEN external: {len(bad_ext)}", flush=True)
for r in bad_ext:
    print(f"  {r.get('status')} {r.get('err','')} {r['url']} <- {r['linkedFrom'][:2]}", flush=True)
print("LINKCHECK DONE", flush=True)
