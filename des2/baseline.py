"""Loss-only regression model for Des v2. The heart of SPEC-V2 section 2.

Des remembers what a page looked like when it was last healthy and alarms only
when something goes MISSING. Ed publishes constantly, so a page that grows is
silently re-baselined and never lands on his phone. A page that shrinks, loses
its chrome, or starts serving broken images is a finding.

Three rules hold everywhere in this module:

1. Losses only. Gains, additions and unchanged values produce nothing.
2. Never invent a loss. Missing data (no prior baseline, a blocked page, a
   baseline written by an older version, a standard the extractor did not
   evaluate) is treated as "unknown", not as "gone".
3. Every finding proves itself. Each Finding carries Evidence.numbers with the
   before and after values, so `Evidence.is_hard()` is always True (SPEC 5).
   `reproduced` is never set here; only the verify pass may set it.

Pure and offline: no browser, no network. `capture()` takes facts that some
other module already extracted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from des2.models import Evidence, Finding, Fingerprint, owner_for

# --------------------------------------------------------------------------
# Thresholds. Module-level so they are auditable in one place, and overridable
# per call so a noisy site can be tuned without editing code.
# --------------------------------------------------------------------------

# Body text may legitimately swing when Ed trims a section or swaps copy, so a
# small shrink is normal editing. A drop past 40% is not editing, it is a
# template or query failing to render the article body.
TEXT_COLLAPSE_PCT = 0.40

# Sections and internal links are structural. Losing half of them means a
# widget, loop or menu stopped rendering rather than someone deleting content
# by hand, so this sits higher than the text threshold.
STRUCTURE_DROP_PCT = 0.50

# "images_total is comparable" means the page still intends to show roughly the
# same number of images. If total itself fell below 90% of baseline, images were
# removed on purpose (a content change) and no image is broken.
IMAGE_TOTAL_COMPARABLE = 0.90

# Percentage drops on tiny numbers are meaningless: a 30-character stub going to
# 10 is 67% and tells us nothing. Below these floors the loss-only model has no
# signal, so it stays quiet rather than guessing.
MIN_TEXT_BASELINE = 200
MIN_STRUCTURE_BASELINE = 2

# Where baselines live when the caller does not say. Repo-relative, matching the
# existing baselines/ directory.
BASELINE_ROOT = Path(__file__).resolve().parent.parent / "baselines"

# Bumped only if the on-disk shape changes. Unknown keys are ignored on load
# (Fingerprint.from_dict filters), so old files stay readable.
BASELINE_FORMAT = 2

_UNSAFE = re.compile(r"[^a-z0-9._-]+")
_MAX_STEM = 80


# --------------------------------------------------------------------------
# Coercion helpers. Nothing in here may raise on bad input.
# --------------------------------------------------------------------------

_TRUE_STRINGS = {"1", "true", "yes", "y", "on", "present"}


def _as_bool(value: Any) -> bool:
    """Truthiness that survives strings. 'false' and '0' are False, not True."""
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    try:
        return bool(value)
    except Exception:
        return False


def _as_int(value: Any) -> int:
    """Best-effort non-negative int. Anything unparseable becomes 0."""
    if isinstance(value, bool):
        return int(value)
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    except Exception:
        return 0
    return n if n > 0 else 0


def _as_standards(value: Any) -> dict[str, bool]:
    """Standards map with string keys and bool values. Non-dict becomes {}."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, bool] = {}
    for k, v in value.items():
        try:
            key = str(k).strip()
        except Exception:
            continue
        if key:
            out[key] = _as_bool(v)
    return out


def _pct_drop(before: float, after: float) -> float:
    """Percentage lost, rounded to one decimal. Zero baseline means no signal."""
    if before <= 0:
        return 0.0
    return round(max(0.0, (before - after) / before) * 100.0, 1)


def _check_name(standard_key: str) -> str:
    """'meta_description' -> 'lost_meta_description'. Idempotent, safe for owner routing."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(standard_key).strip().lower()).strip("_")
    if not slug:
        slug = "standard"
    return slug if slug.startswith("lost_") else f"lost_{slug}"


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def capture(page_data: Any) -> Fingerprint:
    """Build a Fingerprint from already-extracted page facts.

    Counts, not content: the baseline must survive Ed rewording a page and only
    notice things going missing.

    Every key is optional and every value is coerced, because a fingerprint that
    raises on a surprising extractor payload would take the whole sweep down.
    A caller that has nothing (blocked page, load error) gets a default
    Fingerprint, which it must NOT feed to `diff()`; see `diff()` for why.
    """
    d: dict = page_data if isinstance(page_data, dict) else {}

    images_total = _as_int(d.get("images_total"))
    images_ok = _as_int(d.get("images_ok"))
    # ok > total is nonsense and would make later arithmetic lie. Clamp it.
    if images_ok > images_total:
        images_ok = images_total

    return Fingerprint(
        has_nav=_as_bool(d.get("has_nav")),
        has_footer=_as_bool(d.get("has_footer")),
        h1_count=_as_int(d.get("h1_count")),
        text_len=_as_int(d.get("text_len")),
        images_total=images_total,
        images_ok=images_ok,
        internal_links=_as_int(d.get("internal_links")),
        sections=_as_int(d.get("sections")),
        standards=_as_standards(d.get("standards")),
        # Carried through so a saved baseline keeps its memory of which layout
        # defects the page already had. load_baseline() rebuilds via capture(),
        # so a field missing here is silently dropped on every reload.
        layout_defects=sorted({str(k) for k in (d.get("layout_defects") or [])
                               if isinstance(k, (str, int))}),
    )


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def sanitise_url(url: Any) -> str:
    """Turn a URL into a flat, safe, collision-resistant filename stem.

    Scheme and credentials are dropped, everything outside [a-z0-9._-] becomes a
    dash, and a short digest of the ORIGINAL url is appended so two urls that
    sanitise to the same text still get separate files. No slashes and no '..'
    survive, so a hostile url cannot escape the baseline directory.
    """
    raw = url if isinstance(url, str) else str(url or "")
    digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:8]

    stem = raw.strip().lower()
    stem = re.sub(r"^[a-z][a-z0-9+.-]*://", "", stem)   # scheme
    stem = stem.split("@")[-1]                          # credentials
    stem = _UNSAFE.sub("-", stem).strip("-._")
    stem = re.sub(r"-{2,}", "-", stem)
    if len(stem) > _MAX_STEM:
        stem = stem[:_MAX_STEM].strip("-._")
    if not stem:
        stem = "url"
    return f"{stem}-{digest}"


def baseline_path(url: str, viewport: str, root: Union[str, Path, None] = None) -> Path:
    """Absolute path of the baseline file for one url at one viewport."""
    base = Path(root) if root is not None else BASELINE_ROOT
    vp = _UNSAFE.sub("-", str(viewport or "unknown").strip().lower()).strip("-._") or "unknown"
    return base / f"{sanitise_url(url)}__{vp}.json"


def load_baseline(
    url: str,
    viewport: str,
    root: Union[str, Path, None] = None,
) -> Optional[Fingerprint]:
    """Read the stored Fingerprint, or None if there is not a usable one.

    None means "no comparison possible", which the loss-only model treats as
    first sight: save and stay quiet. Corrupt, truncated, unreadable or
    foreign-shaped files return None too, because a guard that crashes on its
    own state file is worse than one that rebuilds it.

    Files written by an older version are read forgivingly: unknown keys are
    dropped and missing keys fall back to the Fingerprint defaults.
    """
    path = baseline_path(url, viewport, root)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    except Exception:
        return None

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    try:
        fp = Fingerprint.from_dict(data)
    except Exception:
        return None

    # An older or hand-edited file can carry the right keys with the wrong
    # types. Push it back through the same coercion capture() uses.
    return capture(fp.to_dict())


def save_baseline(
    url: str,
    viewport: str,
    fp: Fingerprint,
    root: Union[str, Path, None] = None,
) -> None:
    """Write the Fingerprint for one url at one viewport.

    Atomic (temp file then rename) so an interrupted sweep cannot leave a
    half-written baseline that the next run has to distrust.
    """
    if fp is None:
        return
    path = baseline_path(url, viewport, root)
    payload = fp.to_dict()
    payload.update({
        "_format": BASELINE_FORMAT,
        "_url": str(url),
        "_viewport": str(viewport),
        "_saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def accept(
    url: str,
    viewport: str,
    fp: Fingerprint,
    root: Union[str, Path, None] = None,
) -> None:
    """Adopt this observation as the new healthy baseline. THE ANTI-NAG VALVE.

    Call this whenever a page came back healthy or came back BIGGER: new
    sections, more images, longer copy, extra links. Ed publishes constantly and
    must never be asked to approve his own work (SPEC 2), so growth is absorbed
    here silently instead of becoming a diff for a human to dismiss.

    This is what keeps Des believable. Without it the baseline would freeze at
    the first sighting, every publish would read as a change, and the alarm
    would train Ed to ignore it, which is the exact failure this rebuild exists
    to end.

    Do NOT call this for a blocked, errored or non-200 page: its fingerprint is
    not evidence of health, and accepting it would overwrite a good baseline
    with an empty one and blind the guard.
    """
    save_baseline(url, viewport, fp, root)


# --------------------------------------------------------------------------
# diff: the loss-only comparison
# --------------------------------------------------------------------------

def _finding(check: str, url: str, viewport: str, summary: str,
             numbers: dict[str, float], note: str = "") -> Finding:
    return Finding(
        check=check,
        kind="breakage" if check in ("missing_nav", "missing_footer", "broken_images") else "standard_lost",
        url=url,
        viewport=viewport,
        summary=summary,
        evidence=Evidence(numbers=numbers, note=note),
        owner=owner_for(check),
        # reproduced stays False. Only the verify pass may promote a finding.
    )


def diff(
    old: Optional[Fingerprint],
    new: Optional[Fingerprint],
    url: str,
    viewport: str,
    text_collapse_pct: float = TEXT_COLLAPSE_PCT,
    structure_drop_pct: float = STRUCTURE_DROP_PCT,
    image_total_comparable: float = IMAGE_TOTAL_COMPARABLE,
    min_text_baseline: int = MIN_TEXT_BASELINE,
    min_structure_baseline: int = MIN_STRUCTURE_BASELINE,
) -> list[Finding]:
    """Compare a page against its last healthy self and report only LOSSES.

    Returns findings for: nav or footer gone, the last H1 gone, images that used
    to load now failing, the body collapsing, and any standard that was present
    and now is not.

    Returns [] for: gains, additions, unchanged values, a standard that was
    already absent (backlog, not alarm, SPEC 4), and any case where one side is
    missing. `old is None` is first sight. `new is None` is a page that was
    blocked or errored, and a blocked page is not a loss, it is a page we did
    not get to see, so it must produce nothing here.
    """
    if old is None or new is None:
        return []

    findings: list[Finding] = []

    # --- chrome: nav and footer. Present then absent is always breakage. ---
    if old.has_nav and not new.has_nav:
        findings.append(_finding(
            "missing_nav", url, viewport,
            "Nav was present at the last healthy check and is now missing.",
            {"before": 1, "after": 0},
        ))

    if old.has_footer and not new.has_footer:
        findings.append(_finding(
            "missing_footer", url, viewport,
            "Footer was present at the last healthy check and is now missing.",
            {"before": 1, "after": 0},
        ))

    # --- the last H1. 3 -> 1 is an edit; 1 -> 0 is a lost standard. ---
    if old.h1_count >= 1 and new.h1_count == 0:
        findings.append(_finding(
            "lost_h1", url, viewport,
            f"H1 gone: page had {old.h1_count}, now has none.",
            {"before": old.h1_count, "after": 0},
        ))

    # --- images that used to load and now do not. ---
    if (
        old.images_ok >= 1
        and new.images_ok < old.images_ok
        and new.images_total >= old.images_total * image_total_comparable
    ):
        findings.append(_finding(
            "broken_images", url, viewport,
            f"{old.images_ok - new.images_ok} image(s) that used to load now fail "
            f"({new.images_ok}/{new.images_total} loading, was {old.images_ok}/{old.images_total}).",
            {
                "before": old.images_ok,
                "after": new.images_ok,
                "drop_pct": _pct_drop(old.images_ok, new.images_ok),
                "images_total_before": old.images_total,
                "images_total_after": new.images_total,
                "broken_now": max(0, new.images_total - new.images_ok),
            },
            note="images_total is comparable, so these are failures, not removals.",
        ))

    # --- body collapse: text, sections and internal links. ---
    # One event (a template or loop failing to render) shows up in several of
    # these at once, so they are aggregated into a SINGLE lost_body_content
    # finding. Three findings for one cause would nag Bryan three times, and
    # Finding.key() cannot tell them apart anyway.
    body: dict[str, float] = {}
    signals: list[tuple[str, int, int, float]] = []

    if old.text_len >= min_text_baseline:
        pct = _pct_drop(old.text_len, new.text_len)
        if pct > text_collapse_pct * 100:
            signals.append(("text_len", old.text_len, new.text_len, pct))

    if old.sections >= min_structure_baseline:
        pct = _pct_drop(old.sections, new.sections)
        if pct > structure_drop_pct * 100:
            signals.append(("sections", old.sections, new.sections, pct))

    if old.internal_links >= min_structure_baseline:
        pct = _pct_drop(old.internal_links, new.internal_links)
        if pct > structure_drop_pct * 100:
            signals.append(("internal_links", old.internal_links, new.internal_links, pct))

    if signals:
        # The worst drop leads, and also fills the plain before/after/drop_pct
        # keys so every finding reads the same way downstream.
        lead = max(signals, key=lambda s: s[3])
        for name, before, after, pct in signals:
            body[f"{name}_before"] = before
            body[f"{name}_after"] = after
            body[f"{name}_drop_pct"] = pct
        body.update({"before": lead[1], "after": lead[2], "drop_pct": lead[3]})
        parts = ", ".join(f"{n} {b} -> {a} ({p}% lost)" for n, b, a, p in signals)
        findings.append(_finding(
            "lost_body_content", url, viewport,
            f"Body content collapsed: {parts}.",
            body,
        ))

    # --- standards: True -> False only. ---
    # A key ABSENT from the new fingerprint is not a loss: it means this run did
    # not evaluate that standard. Treating "not measured" as "missing" is how
    # false-positive storms start, so absence stays quiet.
    for key in sorted(old.standards):
        if not old.standards.get(key):
            continue  # never had it: backlog for Bryan, not an alarm (SPEC 4)
        if key not in new.standards:
            continue  # not measured this run
        if new.standards.get(key):
            continue  # still there
        check = _check_name(key)
        findings.append(_finding(
            check, url, viewport,
            f"Standard lost: {key} was present at the last healthy check and is now gone.",
            {"before": 1, "after": 0},
            note=f"standard={key}",
        ))

    return findings

# ---------------------------------------------------------------------------
# Layout: pre-existing versus new (added 2026-08-27)
# ---------------------------------------------------------------------------
def layout_key(finding) -> str:
    """Stable identity for a layout defect, independent of exact pixel counts.

    Measurements wobble by a pixel between runs, so keying on them would make
    every defect look new every day. Element plus check is stable.
    """
    sel = (finding.evidence.selector or "").strip()
    return f"{finding.check}|{sel}"


def classify_layout(findings: list, old: Optional[Fingerprint]):
    """(new, pre_existing). Only NEW layout defects deserve Ed's attention.

    A first sight (no baseline) treats everything as pre-existing: the guard
    has no idea what this page is supposed to look like yet, and announcing
    every longstanding design choice as breakage on day one is precisely how a
    guard loses its reader.
    """
    if old is None:
        return [], list(findings)
    known = set(old.layout_defects or [])
    fresh, seen_before = [], []
    for f in findings:
        (seen_before if layout_key(f) in known else fresh).append(f)
    return fresh, seen_before


def remember_layout(fp: Fingerprint, findings: list) -> Fingerprint:
    """Fold the layout defects seen this run into the fingerprint to be saved."""
    fp.layout_defects = sorted({layout_key(f) for f in (findings or [])})
    return fp
