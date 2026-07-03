"""
Deterministic analysis over capture metrics: chrome clustering, breadcrumb
geometry, SEO battery, overflow, mobile UX, perf. Emits analysis.json + summary.

Usage: python analyze_metrics.py <site> <metrics.jsonl> <out.json>
"""
import json, sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

SITE = sys.argv[1]
IS_TRW = SITE == "trw"
pages = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
OUT = sys.argv[3]

F = defaultdict(list)  # check_id -> [{url, evidence}]

def add(check, url, evidence, severity):
    F[check].append({"url": url, "evidence": evidence, "severity": severity})

def path(u):
    return urlparse(u).path

COE_WIDE = {"/coe-results/", "/coe-explained/", "/coe-results-explained/", "/cat-a-vs-cat-b-singapore/"}
BYLINE_EXEMPT_PREFIX = ("/contact/", "/about/", "/team/", "/faq/", "/privacy", "/terms", "/blog/",
                        "/topics/", "/services/", "/brands", "/guides/", "/car-tips/", "/news/", "/coe-results/")

# ---------- chrome clustering ----------
def sig_key(sig):
    return json.dumps(sig, sort_keys=True) if sig else "ABSENT"

nav_groups, footer_groups, bc_geo_groups = defaultdict(list), defaultdict(list), defaultdict(list)
for p in pages:
    d = p.get("desktop") or {}
    if not d:
        add("capture_failed", p["url"], p.get("desktopError", "no desktop data"), "high")
        continue
    nav_groups[sig_key(d.get("navSig"))].append(p["url"])
    fkey = sig_key({"links": d.get("footerSig"), "heads": d.get("footerHeadings")})
    footer_groups[fkey].append(p["url"])
    if d.get("bcPresent") and d.get("bcRect"):
        r = d["bcRect"]
        bc_geo_groups[(round(r["x"] / 10) * 10, round(r["w"] / 10) * 10)].append(p["url"])

def cluster_report(groups, kind):
    if not groups:
        return
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    canon_key, canon_urls = ranked[0]
    canon = json.loads(canon_key) if canon_key != "ABSENT" else None
    for key, urls in ranked[1:]:
        if key == "ABSENT":
            continue
        variant = json.loads(key)
        diff = {}
        try:
            if kind == "nav":
                a = {(x["t"], x["h"]) for x in canon}
                b = {(x["t"], x["h"]) for x in variant}
                diff = {"missing_vs_canonical": sorted(a - b), "extra_vs_canonical": sorted(b - a)}
            else:
                a = {(x["t"], x["h"]) for x in (canon.get("links") or [])}
                b = {(x["t"], x["h"]) for x in (variant.get("links") or [])}
                diff = {"missing_vs_canonical": sorted(a - b)[:15], "extra_vs_canonical": sorted(b - a)[:15],
                        "headings": [canon.get("heads"), variant.get("heads")] if canon.get("heads") != variant.get("heads") else None}
        except Exception as e:
            diff = {"err": str(e)}
        for u in urls:
            add(f"{kind}_variant", u, json.dumps(diff, ensure_ascii=False)[:500], "high")

cluster_report(nav_groups, "nav")
cluster_report(footer_groups, "footer")

# breadcrumb geometry clusters
if bc_geo_groups:
    ranked = sorted(bc_geo_groups.items(), key=lambda kv: -len(kv[1]))
    canon_geo = ranked[0][0]
    for geo, urls in ranked[1:]:
        for u in urls:
            if IS_TRW and path(u) in COE_WIDE:
                continue  # intentional wide chrome
            add("bc_geometry_variant", u, f"bc at x~{geo[0]} w~{geo[1]} vs canonical x~{canon_geo[0]} w~{canon_geo[1]}", "medium")

# ---------- per-page battery ----------
fingerprint_stats = Counter()
for p in pages:
    u, d = p["url"], p.get("desktop") or {}
    ph, probes = p.get("phone") or {}, p.get("probes") or {}
    pt = path(u)
    if not d:
        continue
    if p.get("status") and p["status"] >= 500:
        add("http_5xx", u, f"HTTP {p['status']}", "critical")
    elif p.get("status") and p["status"] >= 400:
        add("http_4xx", u, f"HTTP {p['status']} (in sitemap)", "high")
    if not d.get("navPresent"):
        add("missing_nav", u, "no nav found", "critical")
    if not d.get("footerPresent"):
        add("missing_footer", u, "no footer found", "critical")
    if IS_TRW:
        m = d.get("markers") or {}
        for mk, present in m.items():
            fingerprint_stats[(mk, present)] += 1
        for mk in ("NAV-WHITE-PATCH-2026-04-29", "MOBILE-NAV-FIX-2026-04-25"):
            if m.get(mk) is False:
                add("missing_marker_" + mk.split("-")[0].lower(), u, f"{mk} absent", "high")
        if not any(x["h"].startswith("/coe-results") for x in (d.get("navSig") or [])):
            add("missing_coe_nav_link", u, "no /coe-results link in nav", "high")
        if not d.get("byline") and not any(pt.startswith(x) for x in BYLINE_EXEMPT_PREFIX) and pt != "/":
            add("missing_byline", u, "no 'Reviewed by The Right Workshop team'", "medium")
        if d.get("kakiBukit") and not d.get("unitNo"):
            add("missing_unit_number", u, "Kaki Bukit address without #02-61", "medium")
        if d.get("imgNonPhotonCount") and (pt == "/" or pt.startswith("/services/")):
            add("non_photon_images", u, f"{d['imgNonPhotonCount']}: {d.get('imgNonPhoton')}", "medium")
    # breadcrumbs
    exempt = {"/", "/services/", "/topics/", "/brands/", "/conditions/", "/blog/"}
    if not d.get("bcPresent") and pt not in exempt:
        add("missing_breadcrumb", u, "no breadcrumb nav", "medium")
    if d.get("bcPresent"):
        links = d.get("bcLinks") or []
        if links and (links[0]["t"].lower() != "home" or links[0]["h"] not in ("/", "")):
            add("bc_first_not_home", u, f"first crumb: {links[0]}", "medium")
        if not links:
            add("bc_no_links", u, f"breadcrumb has no links: {d.get('bcText', '')[:80]}", "medium")
    # SEO
    t = d.get("title") or ""
    md = d.get("metaDescription")
    if not t:
        add("missing_title", u, "empty <title>", "medium")
    elif len(t) > 60:
        add("long_title", u, f"{len(t)} chars: {t[:80]}", "low")
    if not md:
        add("missing_meta_description", u, "no meta description", "medium")
    elif len(md) > 160:
        add("long_meta_description", u, f"{len(md)} chars", "low")
    can = d.get("canonical")
    if not can:
        add("missing_canonical", u, "no rel=canonical", "medium")
    elif can.rstrip("/") != u.rstrip("/"):
        add("canonical_mismatch", u, f"canonical -> {can}", "high")
    rb = (d.get("robotsMeta") or "").lower()
    if "noindex" in rb:
        add("noindex_in_sitemap", u, f"robots: {rb}", "high")
    h1s = d.get("h1s") or []
    if len(h1s) == 0:
        add("missing_h1", u, "no h1", "medium")
    elif len(h1s) > 1:
        add("multiple_h1", u, f"{len(h1s)} h1s: {h1s[:3]}", "medium")
    for j in d.get("jsonLd") or []:
        if not j.get("ok"):
            add("jsonld_invalid", u, j.get("err", ""), "medium")
    # content
    if d.get("emDash"):
        add("em_dash", u, f"{len(d['emDash'])} hits e.g. {d['emDash'][:2]}", "medium")
    if d.get("emDashAlt"):
        add("em_dash_alt", u, str(d["emDashAlt"][:2]), "medium")
    if d.get("autopSigs"):
        add("autop_signature", u, ",".join(d["autopSigs"]), "high")
    # images
    if d.get("imgBroken"):
        add("broken_images", u, json.dumps(d["imgBroken"][:3]), "high")
    if d.get("imgMissingAltCount"):
        add("missing_alt", u, f"{d['imgMissingAltCount']} imgs: {d.get('imgMissingAlt')[:4]}", "medium")
    # layout
    if d.get("overflowX"):
        add("overflow_desktop", u, f"docW {d.get('docWidth')} offenders {json.dumps(d.get('overflowOffenders', [])[:4])}", "high")
    for w, pr in probes.items():
        if isinstance(pr, dict) and pr.get("of"):
            add(f"overflow_{w}", u, f"docW {pr.get('dw')} vs {pr.get('iw')}", "high")
    if ph.get("overflowX"):
        add("overflow_phone", u, f"docW {ph.get('docWidth')} offenders {json.dumps(ph.get('offenders', [])[:4])}", "high")
    # console
    dc = p.get("desktopConsole") or {}
    pc = p.get("phoneConsole") or {}
    js_errs = (dc.get("js_errors") or []) + (pc.get("js_errors") or []) + (dc.get("console_errors") or []) + (pc.get("console_errors") or [])
    if js_errs:
        add("console_js_errors", u, str(js_errs[:3]), "high")
    fails = [f for f in (dc.get("failed_requests") or []) if urlparse(f["u"]).netloc == urlparse(u).netloc or f["s"] >= 500]
    if fails:
        add("failed_subresources", u, json.dumps(fails[:4]), "high")
    # links / anchors
    if d.get("missingAnchors"):
        add("missing_anchor_targets", u, str(d["missingAnchors"][:5]), "medium")
    if d.get("deadCtas"):
        add("dead_ctas", u, str(d["deadCtas"][:5]), "medium")
    # mobile UX
    ham = p.get("hamburger") or {}
    if ham.get("found") is False:
        add("hamburger_not_found", u, "no hamburger matched on phone viewport", "medium")
    elif ham.get("found") and not ham.get("afterClick"):
        add("hamburger_no_drawer", u, f"clicked {ham.get('sel')} but no drawer detected", "high")
    if len(ph.get("smallTapTargets") or []) >= 5:
        add("small_tap_targets", u, json.dumps(ph["smallTapTargets"][:5]), "medium")
    if (ph.get("tinyFontCount") or 0) >= 10:
        add("tiny_fonts_mobile", u, f"{ph['tinyFontCount']} elements < 11px", "medium")
    if ph.get("bigFixed"):
        add("large_fixed_overlay_mobile", u, str(ph["bigFixed"][:3]), "medium")
    # perf
    perf = p.get("perf") or {}
    if perf.get("lcp") and perf["lcp"] > 2500:
        add("slow_lcp", u, f"LCP {perf['lcp']}ms", "medium")
    if perf.get("cls") and perf["cls"] > 0.1:
        add("high_cls", u, f"CLS {perf['cls']}", "medium")
    if p.get("desktopError"):
        add("desktop_capture_error", u, p["desktopError"], "high")
    if p.get("phoneError"):
        add("phone_capture_error", u, p["phoneError"], "high")

# ---------- emit ----------
result = {"site": SITE, "pages": len(pages), "findings": {}}
for check, items in sorted(F.items(), key=lambda kv: -len(kv[1])):
    result["findings"][check] = {"count": len(items), "severity": items[0]["severity"], "items": items}
if IS_TRW:
    result["fingerprint_stats"] = {f"{k[0]}={k[1]}": v for k, v in sorted(fingerprint_stats.items())}
result["nav_cluster_sizes"] = {k[:100]: len(v) for k, v in sorted(nav_groups.items(), key=lambda kv: -len(kv[1]))}
result["bc_geo_clusters"] = {str(k): len(v) for k, v in sorted(bc_geo_groups.items(), key=lambda kv: -len(kv[1]))}
json.dump(result, open(OUT, "w"), indent=1, ensure_ascii=False)

print(f"=== {SITE}: {len(pages)} pages analyzed ===")
for check, data in sorted(result["findings"].items(), key=lambda kv: ({"critical": 0, "high": 1, "medium": 2, "low": 3}[kv[1]["severity"]], -kv[1]["count"])):
    print(f"[{data['severity'].upper():8}] {check}: {data['count']} pages")
    for it in data["items"][:2]:
        print(f"    {it['url'].replace('https://therightworkshop.com','').replace('https://auraanimalrehab.com','') or '/'} — {str(it['evidence'])[:140]}")
if IS_TRW:
    print("fingerprints:", json.dumps(result["fingerprint_stats"]))
print("bc geo clusters:", result["bc_geo_clusters"])
