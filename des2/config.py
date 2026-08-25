"""Site configuration loading for Des v2.

`load_site(name)` reads `sites/<name>.v2.yaml`, applies sensible defaults so a
thin config still works, then validates that every key required by
SPEC-V2.md section 3 (coverage) is present -- clearly, so a broken config
fails at load time instead of producing a silently degraded sweep.

See des2/models.py for the pinned types this config feeds into (templates ->
discover.template_urls, skip_patterns -> discover.all_urls).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

# Every key a v2 site config must resolve to a value for. wp_api_base is the
# one exception: it may legitimately be null for a non-WordPress site.
REQUIRED_KEYS = (
    "name",
    "base_url",
    "sitemap_url",
    "wp_api_base",
    "nav_selector",
    "footer_selector",
    "mobile_nav_toggle_selector",
    "mobile_nav_panel_selector",
    "templates",
    "skip_patterns",
)

NULLABLE_KEYS = {"wp_api_base"}

# Applied for any key the YAML omits, so a thin config (just the identity
# fields) still loads. name/base_url/sitemap_url are deliberately absent --
# there is no sane default for a site's own identity.
DEFAULTS: dict[str, Any] = {
    "wp_api_base": None,
    "nav_selector": "nav",
    "footer_selector": "footer",
    "mobile_nav_toggle_selector": None,
    "mobile_nav_panel_selector": None,
    "templates": [],
    "skip_patterns": [],
}

SITES_DIR = Path(__file__).resolve().parent.parent / "sites"


class ConfigError(ValueError):
    """A site config is missing, malformed, or missing a required key."""


def _site_path(name: str) -> Path:
    return SITES_DIR / f"{name}.v2.yaml"


def load_site(name: str) -> dict:
    """Load and validate `sites/<name>.v2.yaml`.

    Defaults fill in any key the file omits, then every REQUIRED_KEYS entry
    is checked to have resolved to a value (null permitted only for
    wp_api_base). Unknown extra keys pass through untouched -- never a
    reason to crash, per SPEC-V2.md's cost-discipline spirit of degrading
    gracefully rather than dying on something unexpected.
    """
    path = _site_path(name)
    if not path.exists():
        raise ConfigError(f"site config not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML ({exc})") from exc

    raw = raw or {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: top-level YAML must be a mapping, got {type(raw).__name__}"
        )

    cfg = dict(DEFAULTS)
    cfg.update(raw)

    missing = [
        key for key in REQUIRED_KEYS
        if key not in cfg or (cfg[key] is None and key not in NULLABLE_KEYS)
    ]
    if missing:
        raise ConfigError(
            f"{path}: missing required key(s): {', '.join(missing)}"
        )

    if not isinstance(cfg["templates"], list):
        raise ConfigError(f"{path}: 'templates' must be a list of {{name, url}}")
    for i, tmpl in enumerate(cfg["templates"]):
        if not isinstance(tmpl, dict) or "name" not in tmpl or "url" not in tmpl:
            raise ConfigError(
                f"{path}: templates[{i}] must be a mapping with 'name' and 'url', got {tmpl!r}"
            )

    if not isinstance(cfg["skip_patterns"], list):
        raise ConfigError(f"{path}: 'skip_patterns' must be a list of regex strings")
    for i, pat in enumerate(cfg["skip_patterns"]):
        try:
            re.compile(pat)
        except re.error as exc:
            raise ConfigError(
                f"{path}: skip_patterns[{i}] ({pat!r}) is not a valid regex: {exc}"
            ) from exc

    cfg["name"] = str(cfg["name"])
    cfg["base_url"] = str(cfg["base_url"]).rstrip("/")

    return cfg
