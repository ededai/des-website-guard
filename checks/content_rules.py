"""
Content-rule checks. Strip HTML/CSS/JS comments before scanning user-visible
prose so we don't false-positive on `i--` in JS or `<!-- -->` in HTML.
"""
import re

from bs4 import BeautifulSoup

EM_DASH_RE = re.compile(r"[\u2014\u2013]|--")  # em + en + double-dash


def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
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
    missing = [img for img in soup.find_all("img") if not img.get("alt") and not img.get("aria-hidden") == "true"]
    if missing:
        return {"check": "missing_alt", "severity": "medium", "evidence": f"{len(missing)} <img> without alt"}
    return None


ALL_HTML_CHECKS = [
    check_em_dash,
    check_byline,
    check_address_unit,
    check_meta_description,
    check_title,
    check_canonical,
    check_h1,
    check_alt_text,
]
