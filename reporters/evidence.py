"""
Single pipeline-boundary evidence formatter (C-EVID).

Every check-emitted evidence value (str, dict, list, or anything else) must
pass through humanize() before it reaches Telegram, the HTML report or
bug-log.jsonl. This is the systemic guarantee: a check that starts returning
a new dict shape can never regress an alert into a raw Python repr.
"""
import html

_MAX_LEN = 1800
_MAX_ITEMS = 3
_REPR_MARKERS = ("{'", "': ", "\"}", "[{")


def humanize(ev, _depth=0):
    if ev is None:
        out = ""
    elif isinstance(ev, str):
        out = ev.strip()
        if _depth > 0:
            return out
    elif isinstance(ev, dict):
        sep = "; " if _depth == 0 else ", "
        parts = []
        for k, v in ev.items():
            vs = humanize(v, _depth + 1)
            parts.append(f"{k}: {vs}" if _depth == 0 else f"{k}={vs}")
        out = sep.join(parts)
        if _depth > 0:
            return out
    elif isinstance(ev, (list, tuple)):
        sep = "; " if _depth == 0 else ", "
        items = list(ev)
        shown = items[:_MAX_ITEMS]
        out = sep.join(humanize(v, _depth + 1) for v in shown)
        if len(items) > _MAX_ITEMS:
            out += f" (+{len(items) - _MAX_ITEMS} more)"
        if _depth > 0:
            return out
    else:
        out = str(ev)
        if _depth > 0:
            return out

    # Final pass — only reached at depth 0.
    out = " ".join(out.split())
    if len(out) > _MAX_LEN:
        out = out[:_MAX_LEN].rstrip() + "..."
    if any(marker in out for marker in _REPR_MARKERS):
        for ch in "{}[]'\"":
            out = out.replace(ch, "")
    return out


def escape_html(s):
    return html.escape(str(s), quote=False)
