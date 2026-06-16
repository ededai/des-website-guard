"""
Content-rule checks. Strip HTML/CSS/JS comments before scanning user-visible
prose so we don't false-positive on `i--` in JS or `<!-- -->` in HTML.
"""
import re

from bs4 import BeautifulSoup

EM_DASH_RE = re.compile(r"[\u2014\u2013]|--")  # em + en + double-dash

# Elements whose visible text is a JS-injected placeholder that Des must NOT scan.
# These hold "\u2014" / "\u2014\u2014" only until client-side JS replaces them with real values, so a
# raw-HTML scan would false-positive on the em-dash. Add a data-attribute here to teach
# Des to ignore a new placeholder. (Learned 2026-06-16: COE chart range/date/count cells.)
EM_DASH_IGNORE_SELECTORS = ["[data-range-text]", "[data-date-pill]", "[data-round-count]"]


def visible_text(html, ignore_selectors=None):
    # Strip standard + bogus HTML comments (wptexturize can mangle --> causing leakage)
    html = re.sub(r"<!--[\s\S]*?-->", "", html)
    html = re.sub(r"<!-[^-][\s\S]*?-->", "", html)
    soup = BeautifulSoup(html, "html.parser")
    # Strip non-visible head content (title, meta) and script/style
    for tag in soup(["head", "script", "style", "noscript"]):
        tag.decompose()
    # Strip JS-placeholder elements (their \u2014 is replaced at runtime; not a real em-dash)
    for sel in (ignore_selectors if ignore_selectors is not None else EM_DASH_IGNORE_SELECTORS):
        for el in soup.select(sel):
            el.decompose()
    return soup.get_text(separator=" ")


def check_em_dash(html):
    text = visible_text(html)
    matches = EM_DASH_RE.findall(text)
    if matches:
        return {"check": "em_dash", "severity": "medium", "evidence": f"{len(matches)} em/en/double-dash occurrences in body"}
    # also scan alt text and JSON-LD
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        alt = img.get("alt", "")
        if EM_DASH_RE.search(alt):
            return {"check": "em_dash", "severity": "medium", "evidence": f"em/en/double-dash in alt: {alt!r}"}
    for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if EM_DASH_RE.search(s.get_text() or ""):
            return {"check": "em_dash", "severity": "medium", "evidence": "em/en/double-dash inside JSON-LD"}
    return None


def check_byline(html, expected="Reviewed by The Right Workshop team"):
    if expected not in html:
        return {"check": "missing_byline", "severity": "medium", "evidence": f"required byline {expected!r} not found"}
    return None


def check_address_unit(html, unit="#02-61"):
    # only flag if an address is present at all
    if "Kaki Bukit" in html and unit not in html:
        return {"check": "missing_unit_number", "severity": "medium", "evidence": f"address present but unit {unit!r} missing"}
    return None


def check_required_markers(html, markers):
    missing = [m for m in markers if m not in html]
    if missing:
        return {"check": "missing_markers", "severity": "high", "evidence": f"markers missing: {', '.join(missing)}"}
    return None


def check_meta_description(html):
    soup = BeautifulSoup(html, "html.parser")
    m = soup.find("meta", attrs={"name": "description"})
    if not m or not m.get("content"):
        return {"check": "missing_meta_description", "severity": "medium", "evidence": "<meta name=description> missing or empty"}
    if len(m["content"]) > 160:
        return {"check": "long_meta_description", "severity": "low", "evidence": f"meta description {len(m['content'])} chars"}
    return None


def check_title(html):
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("title")
    if not t or not (t.get_text() or "").strip():
        return {"check": "missing_title", "severity": "medium", "evidence": "<title> missing or empty"}
    if len(t.get_text()) > 60:
        return {"check": "long_title", "severity": "low", "evidence": f"title {len(t.get_text())} chars"}
    return None


def check_canonical(html):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.find("link", attrs={"rel": "canonical"}):
        return {"check": "missing_canonical", "severity": "medium", "evidence": "<link rel=canonical> missing"}
    return None


def check_h1(html):
    soup = BeautifulSoup(html, "html.parser")
    h1s = soup.find_all("h1")
    if len(h1s) == 0:
        return {"check": "missing_h1", "severity": "medium", "evidence": "no <h1> on page"}
    if len(h1s) > 1:
        return {"check": "multiple_h1", "severity": "low", "evidence": f"{len(h1s)} <h1> on page"}
    return None


def check_alt_text(html):
    soup = BeautifulSoup(html, "html.parser")
    missing = []
    for img in soup.find_all("img"):
        if img.get("alt") is not None and img.get("alt") != "":
            continue
        if img.get("aria-hidden") == "true":
            continue
        # Skip Jetpack tracking pixel (pixel.wp.com/g.gif, id="wpstats").
        # alt="" is correct for decorative trackers.
        if img.get("id") == "wpstats":
            continue
        src = (img.get("src") or "")
        if "pixel.wp.com" in src or "stats.wp.com" in src:
            continue
        # alt="" is technically valid for decorative imgs, only flag truly missing
        if img.get("alt") == "":
            continue
        missing.append(img)
    if missing:
        srcs = [img.get("src", "?")[:80] for img in missing[:5]]
        return {"check": "missing_alt", "severity": "medium", "evidence": f"{len(missing)} <img> without alt. Srcs: {srcs}"}
    return None


# Canonical footer fingerprints — classes/markers only in the TRW canonical footer.
# Update these if the canonical footer is intentionally redesigned.
CANONICAL_FOOTER_FINGERPRINTS = [
    "footer-social-btn",    # custom class on social icon buttons
    "footer-brand-logo",    # custom class on footer logo img
    "footer-col-title",     # was footer-nav-col (stale); the canonical footer uses footer-col-title for multi-column headings
    "footer-brand-tag",     # custom class on footer brand tagline
    "footer-grid",          # canonical multi-column wrapper
]

# Pages that legitimately have no breadcrumb (top-level hubs / homepage)
BC_EXEMPT_SLUGS = {"/", "/services/", "/topics/", "/brands/"}


def check_footer_drift(html, url=""):
    """HIGH — page footer doesn't contain the canonical footer fingerprints."""
    missing = [fp for fp in CANONICAL_FOOTER_FINGERPRINTS if fp not in html]
    if missing:
        return {
            "check": "footer_drift",
            "severity": "high",
            "evidence": f"footer missing canonical markers: {', '.join(missing)}",
        }
    return None


def check_breadcrumb(html, url=""):
    """MEDIUM — page is missing a breadcrumb nav (class='bc')."""
    # Skip exempt top-level pages
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/") + "/"
    if path in BC_EXEMPT_SLUGS or path == "/":
        return None
    if 'class="bc"' not in html and "class='bc'" not in html:
        return {
            "check": "missing_breadcrumb",
            "severity": "medium",
            "evidence": "no <nav class=\"bc\"> breadcrumb found on page",
        }
    return None


ALL_HTML_CHECKS = [
    check_em_dash,
    check_byline,
    check_address_unit,
    check_footer_drift,
    check_breadcrumb,
    check_meta_description,
    check_title,
    check_canonical,
    check_h1,
    check_alt_text,
]
