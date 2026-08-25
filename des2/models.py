"""Shared contracts for Des v2. Every module imports these; nothing forks them.

Pinned before implementation so parallel work cannot drift. If a module needs a
field that is not here, add it here first.

See SPEC-V2.md for why each piece exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Literal, Optional

# A finding may only reach Ed if it reproduces AND carries evidence (SPEC 5).
Kind = Literal["breakage", "layout", "standard_lost"]
Viewport = Literal["desktop", "phone"]

VIEWPORTS: dict[str, dict[str, Any]] = {
    "desktop": {"width": 1440, "height": 900, "is_mobile": False},
    # Real mobile emulation, not a narrow desktop window, or mobile-only bugs
    # never reproduce (2026-08 post-mortem).
    "phone": {"width": 390, "height": 844, "is_mobile": True,
              "has_touch": True, "device_scale_factor": 2},
}


@dataclass
class Evidence:
    """What makes a finding believable. No evidence, no alert."""
    selector: Optional[str] = None      # CSS path of the offending element
    resource: Optional[str] = None      # exact failing URL
    status: Optional[int] = None        # HTTP status when relevant
    numbers: dict[str, float] = field(default_factory=dict)  # measured values
    screenshot: Optional[str] = None    # path, filled at alert time
    note: str = ""

    def is_hard(self) -> bool:
        """True when this finding can name what is wrong, not just that it is.

        The beacon '404' that cost a morning had no URL and no element; it
        would fail here and be logged instead of sent.
        """
        return bool(self.selector or self.resource or self.numbers or self.status)


@dataclass
class Finding:
    check: str                 # stable id, e.g. "resource_404", "text_overflow"
    kind: Kind
    url: str
    viewport: str
    summary: str               # one line, human first
    evidence: Evidence = field(default_factory=Evidence)
    owner: str = "codi"        # bryan | cole | codi | dom
    reproduced: bool = False   # set by the verify pass, never by a check

    def key(self) -> str:
        """Identity for dedupe and for bug-log status tracking."""
        target = self.evidence.resource or self.evidence.selector or ""
        return f"{self.url}|{self.check}|{target}"

    def alertable(self) -> bool:
        return self.reproduced and self.evidence.is_hard()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Fingerprint:
    """What a healthy page looked like. The unit of the loss-only model.

    Counts, not content: Ed publishes constantly, so the baseline must survive
    wording changes and only notice things going missing.
    """
    has_nav: bool = False
    has_footer: bool = False
    h1_count: int = 0
    text_len: int = 0
    images_total: int = 0
    images_ok: int = 0
    internal_links: int = 0
    sections: int = 0
    # Standards present on the page. Loss of one is a finding; never having had
    # it is backlog, not an alarm (SPEC 4).
    standards: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        known = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Observation:
    """One page seen at one viewport."""
    url: str
    viewport: str
    status: Optional[int] = None
    blocked: bool = False          # bot challenge or non-200: DOM checks invalid
    fingerprint: Optional[Fingerprint] = None
    findings: list[Finding] = field(default_factory=list)
    error: str = ""


# Owner routing (SPEC 6). Content and SEO to Bryan, COE data to Cole,
# chrome and infrastructure to Codi, media to Dom.
OWNER_BY_CHECK: dict[str, str] = {
    "page_error": "codi",
    "crawl_blocked": "codi",
    "resource_404": "codi",
    "broken_images": "dom",
    "missing_nav": "codi",
    "missing_footer": "codi",
    "mobile_menu_dead": "codi",
    "booking_path_dead": "codi",
    "js_error": "codi",
    "text_overflow": "codi",
    "horizontal_scroll": "codi",
    "element_overlap": "codi",
    "clipped_content": "codi",
    "tap_target_small": "codi",
    "lost_meta_description": "bryan",
    "lost_title": "bryan",
    "lost_canonical": "bryan",
    "lost_h1": "bryan",
    "lost_byline": "bryan",
    "lost_alt_text": "bryan",
    "lost_address_unit": "bryan",
    "lost_body_content": "bryan",
    "house_style_regression": "bryan",
}


def owner_for(check: str) -> str:
    return OWNER_BY_CHECK.get(check, "codi")
