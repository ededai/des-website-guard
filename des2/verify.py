"""The proof pass: nothing reaches Ed that has not reproduced and cannot prove itself.

Ed chose that ANY confirmed breakage alerts him, rather than only
customer-impacting ones (SPEC-V2 Q4). That decision puts the entire load on the
word "confirmed", so this module is the load-bearing one.

Two independent hurdles, both required:
  1. REPRODUCES. The finding is re-tested in a CLEAN browser context. Transient
     things (a CDN blip, a bot challenge, a beacon aborting at teardown) do not
     survive a second look and die here silently.
  2. PROVES ITSELF. It can name a URL, a selector, a status or a measured
     number. The 2026-08 "1 console error" with no URL attached is the reason
     this exists: it was unfalsifiable, so it could be "fixed" and reopen
     forever while the site was healthy the whole time.

A finding that fails either hurdle is not discarded, it is LOGGED for Codi.
Silence towards Ed is not the same as pretending nothing happened.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Iterable

from des2.models import Finding


async def verify_findings(
    candidates: list[Finding],
    recheck: Callable[[str, str], Awaitable[list[Finding]]],
) -> list[Finding]:
    """Re-run checks for each affected page in a clean context and mark survivors.

    `recheck(url, viewport)` must open a FRESH browser context (no cookies, no
    warmed cache, listeners attached before navigation) and return the findings
    it sees. Reusing the dirty context would inherit exactly the state that
    produced the ghost, which is how the retried-after-challenge false positive
    happened in v1.

    Returns the same Finding objects, with `reproduced` set where confirmed.
    Never raises: a recheck that blows up leaves the finding unconfirmed, which
    means it will be logged rather than sent.
    """
    if not candidates:
        return []
    pages = sorted({(f.url, f.viewport) for f in candidates})
    seen: set[str] = set()
    for url, viewport in pages:
        try:
            again = await recheck(url, viewport)
        except Exception:
            continue
        seen.update(f.key() for f in (again or []))
    for f in candidates:
        if f.key() in seen:
            f.reproduced = True
    return candidates


def partition(findings: Iterable[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Split into (alertable, logged_only).

    `Finding.alertable()` is the single gate, so this cannot drift from the
    contract in models.py.
    """
    alertable, logged = [], []
    for f in findings:
        (alertable if f.alertable() else logged).append(f)
    return alertable, logged


def why_not_alertable(f: Finding) -> str:
    """Human reason a finding was held back, for the log Codi reads."""
    if not f.reproduced and not f.evidence.is_hard():
        return "did not reproduce, and carries no hard evidence"
    if not f.reproduced:
        return "did not reproduce on a clean re-test (likely transient)"
    return "reproduced but names no URL, selector, status or measurement"
