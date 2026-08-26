"""Structural gates. These are not checks; they decide whether checks may be believed.

Each one exists because of a specific incident, and each cost real time to
learn. They are cheap to keep and expensive to rediscover.

  CRAWL HEALTH   2026-08-02. A sweep hit bot-challenge interstitials mid-run
                 and ran DOM checks against the challenge page, reporting
                 CRITICAL "no drawer" on all 159 TRW pages. A page we did not
                 actually see tells us nothing about the site.
  MASS FINDING   The same day. One check firing across most of the site is
                 evidence about the CHECK, not about the site. It collapses to
                 a single "suspected checker defect" note instead of a storm.
  SWEEP ABORT    If too much of the run was blocked, the run itself is not
                 trustworthy and must say so rather than publish a partial
                 picture as if it were complete.
"""
from __future__ import annotations

import os
import sys
from typing import Iterable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import crawl_guard  # harvested wholesale: it already encodes the signatures

from des2.models import Evidence, Finding, owner_for

# More than this share of a check's pages, and we doubt the check, not the site.
MASS_FINDING_SHARE = 0.5
# Below this many pages the share is meaningless, so the gate stays out of the way.
MASS_FINDING_MIN_PAGES = 4
# Above this share of blocked pages the sweep is not a measurement of anything.
SWEEP_ABORT_SHARE = 0.20
SWEEP_ABORT_MIN_PAGES = 10


def crawl_health(status: Optional[int], html: Optional[str] = None) -> tuple[str, str]:
    """(verdict, evidence). Verdict is ok | blocked | dead | server_error.

    Anything other than 'ok' means NO DOM finding from this page may be
    trusted. The caller must skip the battery, not downgrade it.
    """
    return crawl_guard.classify_response(status, html)


def blocked_finding(url: str, viewport: str, evidence_text: str) -> Finding:
    """A blocked page is its own (low-drama) finding, never a pile of DOM ones."""
    return Finding(
        check="crawl_blocked", kind="breakage", url=url, viewport=viewport,
        summary=f"could not see the page: {evidence_text}",
        evidence=Evidence(note=evidence_text), owner=owner_for("crawl_blocked"),
    )


def mass_finding_gate(findings: list[Finding], pages_swept: int) -> list[Finding]:
    """Collapse any check that fired on most of the site into one doubt.

    Returns the findings to actually carry forward. A collapsed group is
    replaced by a single finding that names the check and the count, so the
    signal survives without the storm.
    """
    if pages_swept < MASS_FINDING_MIN_PAGES or not findings:
        return findings
    by_check: dict[str, list[Finding]] = {}
    for f in findings:
        by_check.setdefault(f.check, []).append(f)

    out: list[Finding] = []
    for check, group in by_check.items():
        pages = {f.url for f in group}
        if len(pages) / pages_swept > MASS_FINDING_SHARE:
            sample = sorted(pages)[:3]
            out.append(Finding(
                check="suspected_checker_defect", kind="breakage",
                url=next(iter(sorted(pages))), viewport=group[0].viewport,
                summary=(f"'{check}' fired on {len(pages)} of {pages_swept} pages, "
                         "which is more likely a checker defect than a site-wide fault"),
                evidence=Evidence(note="sample: " + ", ".join(sample),
                                  numbers={"pages_hit": len(pages), "pages_swept": pages_swept}),
                owner="codi",
            ))
        else:
            out.extend(group)
    return out


def sweep_abort(blocked_pages: int, total_pages: int) -> tuple[bool, str]:
    """(should_abort, reason). A mostly-blocked sweep is not a measurement."""
    if total_pages < SWEEP_ABORT_MIN_PAGES or total_pages <= 0:
        return False, ""
    share = blocked_pages / total_pages
    if share > SWEEP_ABORT_SHARE:
        return True, (f"{blocked_pages} of {total_pages} pages were blocked "
                      f"({share:.0%}); the sweep cannot be trusted")
    return False, ""
