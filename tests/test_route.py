"""
Unit tests for the three structural false-positive gates in src/run.py:

  1. REPRODUCE-BEFORE-ALERT (route(), gate 1) — a critical/high finding must
     reproduce against a fresh browser context before it may alert at that
     severity; a non-reproducing finding downgrades to medium + flaky=True.
  2. MASS-FINDING PLAUSIBILITY GATE (route(), gate 2) — a check_id firing on
     more than half the swept pages becomes one medium "suspected checker
     defect" alert instead of a critical/high storm.
  3. CONSOLE-ERRORS CROSS-SWEEP DEBOUNCE (route(), gate 3) — first sighting of
     a DEBOUNCE_CHECK_IDS check for a site logs at true severity but delivers
     as a medium digest item; a second consecutive sweep (still open)
     escalates normally.

Gates 1-3 are exercised with a fake `reproduce_fn` (deterministic, no
browser) so the gating/composition LOGIC is tested precisely and fast. One
additional test (test_reproduce_finding_real_browser_*) drives the REAL
reproduce_finding() against actual Playwright-launched pages (data: URLs, no
network) to prove the browser-based reproduction path itself works, not just
its mocked stand-in.

Runs under pytest OR as a plain script (prints one evidence line per test):

    ./.venv/bin/python -m pytest tests/test_route.py -q
    ./.venv/bin/python -m pytest tests/test_route.py -q -s   # see evidence prints
    ./.venv/bin/python tests/test_route.py                   # plain assert runner
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import run
from src import crawl_guard
from reporters import bug_log


def _finding(check_id="mobile_menu", severity="critical", urls=None, site="TRW",
             in_charge="Bryan", evidence_text="evidence text"):
    urls = list(urls or ["https://example.com/a/"])
    return {
        "title": check_id.replace("_", " ").title(),
        "check_id": check_id,
        "severity": severity,
        "site": site,
        "in_charge": in_charge,
        "summary": evidence_text[:200],
        "urls": urls,
        "_viewport_by_url": {u: "desktop" for u in urls},
        "evidence": evidence_text,
        "details": [],
        "screenshots": [],
        "first_seen": "2026-08-03T00:00:00+00:00",
        "status": "open",
    }


def _wire_bug_log(tmp_path, monkeypatch):
    p = tmp_path / "bug-log.jsonl"
    monkeypatch.setenv("DES_BUG_LOG", str(p))
    return p


def _wire_telegram(monkeypatch):
    sent, photos, queued = [], [], []
    monkeypatch.setattr(run.telegram, "send", lambda text, **kw: sent.append(text) or True)
    monkeypatch.setattr(run.telegram, "send_photo", lambda path, caption, **kw: photos.append(caption) or True)
    monkeypatch.setattr(run.alert_queue, "enqueue", lambda *a, **kw: queued.append((a, kw)))
    return sent, photos, queued


async def _true(finding):
    return True


async def _false(finding):
    return False


# --- Gate 1: reproduce-before-alert -----------------------------------------

def test_gate1_reproducing_critical_finding_still_alerts(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)
    f = _finding(check_id="missing_nav", severity="critical")

    asyncio.run(run.route(f, dry_run=False, reproduce_fn=_true, total_pages=100))

    assert sent, "a reproducing critical finding must still page Telegram immediately"
    assert f["severity"] == "critical"
    assert not f.get("flaky")
    entries = bug_log._load()
    assert entries[0]["severity"] == "critical"
    assert entries[0]["flaky"] is False
    print("EVIDENCE (a): synthetic genuinely-broken critical finding reproduced -> "
          f"still alerted immediately (telegram.send called, {len(sent)} message(s)); "
          f"bug-log severity={entries[0]['severity']} flaky={entries[0]['flaky']}")


def test_gate1_nonreproducing_high_finding_downgrades_to_medium_flaky(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)
    f = _finding(check_id="mobile_menu", severity="high")

    asyncio.run(run.route(f, dry_run=False, reproduce_fn=_false, total_pages=100))

    assert not sent and not photos and not queued, "a non-reproducing finding must never reach Telegram"
    assert f["severity"] == "medium"
    assert f["flaky"] is True
    entries = bug_log._load()
    assert entries[0]["severity"] == "medium"
    assert entries[0]["flaky"] is True
    print("EVIDENCE (b): high finding FAILED re-verification -> "
          f"no Telegram call, landed in bug-log as severity={entries[0]['severity']} "
          f"flaky={entries[0]['flaky']}")


# --- Gate 2: mass-finding plausibility gate ---------------------------------

def test_gate2_60_percent_of_pages_emits_one_suspected_defect_medium(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)
    urls = [f"https://example.com/p{i}/" for i in range(6)]  # 6 of 10 = 60%
    f = _finding(check_id="mobile_menu", severity="critical", urls=urls)

    asyncio.run(run.route(f, dry_run=False, reproduce_fn=_true, total_pages=10))

    assert not sent and not photos and not queued, "mass finding must not send a critical/high alert"
    assert f["severity"] == "medium"
    assert f["suspected_checker_defect"] is True
    assert "suspected checker defect: mobile_menu fired on 6/10 pages" in f["evidence"]
    assert "verify checker before trusting" in f["evidence"]
    for u in urls[:3]:
        assert u in f["evidence"]
    entries = bug_log._load()
    assert entries[0]["severity"] == "medium"
    assert entries[0]["suspected_checker_defect"] is True
    print("EVIDENCE (c): mobile_menu fired critical on 6/10 (60%) swept pages -> "
          f"ONE medium alert instead of a storm: {f['evidence']!r}")


def test_gate2_exactly_50_percent_does_not_trigger(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)
    urls = [f"https://example.com/p{i}/" for i in range(5)]  # 5 of 10 = 50%, not >50%
    f = _finding(check_id="broken_images", severity="high", urls=urls)

    asyncio.run(run.route(f, dry_run=False, reproduce_fn=_true, total_pages=10))

    assert queued, "exactly 50% must NOT trip the mass-finding gate (spec says >50%)"
    assert f["severity"] == "high"
    print("EVIDENCE: 5/10 (50%, not >50%) did not trip gate 2 -> normal high routing (queued)")


# --- Gate 3: console_errors cross-sweep debounce ----------------------------

def test_gate3_console_errors_first_sighting_then_second_consecutive_escalates(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)

    # --- Sweep 1: first-ever sighting -------------------------------------
    f1 = _finding(check_id="console_errors", severity="high", site="AURA",
                   in_charge="Codi", urls=["https://aura.example/blog/a/"])
    asyncio.run(run.route(f1, dry_run=False, reproduce_fn=_true, total_pages=50))

    assert not queued and not sent and not photos, "first sighting must not page immediately"
    assert f1["severity"] == "high", "bug log must show the TRUE severity, not softened"
    assert f1.get("_deliver_medium") is True
    entries = bug_log._load()
    assert len(entries) == 1
    assert entries[0]["severity"] == "high"
    assert entries[0]["status"] == "open"

    # Confirm it actually lands in the medium digest despite severity=="high".
    digest_sent = []
    monkeypatch.setattr(run.telegram, "send", lambda text, **kw: digest_sent.append(text) or True)
    run.send_digests([f1], "AURA", "critical", dry_run=False, mute=set())
    assert len(digest_sent) == 1
    assert "console_errors".replace("_", " ").title() in digest_sent[0] or "Console Errors" in digest_sent[0]
    print(f"EVIDENCE (d-1): FIRST sighting of console_errors -> bug-log severity="
          f"{entries[0]['severity']} (true) but delivered via medium digest "
          f"(no immediate/queued Telegram); digest message sent: {bool(digest_sent)}")

    # --- Sweep 2: same check_id+site fires again, still open --------------
    sent2, photos2, queued2 = _wire_telegram(monkeypatch)
    f2 = _finding(check_id="console_errors", severity="high", site="AURA",
                   in_charge="Codi", urls=["https://aura.example/blog/a/"])
    asyncio.run(run.route(f2, dry_run=False, reproduce_fn=_true, total_pages=50))

    assert queued2, "second CONSECUTIVE sighting must escalate normally (queued as high)"
    assert f2["severity"] == "high"
    assert not f2.get("_deliver_medium")
    print(f"EVIDENCE (d-2): SECOND consecutive sighting of console_errors -> "
          f"escalated normally (alert_queue.enqueue called: {bool(queued2)})")


def test_gate3_only_applies_to_debounced_check_ids():
    assert run.DEBOUNCE_CHECK_IDS == {"console_errors", "resource_404"}


# --- Gate ordering: a reproducing all-pages console_errors hits gate 2 -----

def test_gate_ordering_mass_console_errors_hits_gate2_not_gate3(tmp_path, monkeypatch):
    _wire_bug_log(tmp_path, monkeypatch)
    sent, photos, queued = _wire_telegram(monkeypatch)
    urls = [f"https://example.com/p{i}/" for i in range(9)]  # 9/10 = 90%
    f = _finding(check_id="console_errors", severity="high", urls=urls)

    asyncio.run(run.route(f, dry_run=False, reproduce_fn=_true, total_pages=10))

    assert f["suspected_checker_defect"] is True, "gate 2 must fire before gate 3 gets a chance"
    assert not f.get("_deliver_medium"), "gate 3's marker must never be set once gate 2 has handled it"
    assert not queued and not sent
    print("EVIDENCE: console_errors on 9/10 pages hit gate 2 (suspected checker defect), "
          "never reached gate 3's debounce path")


# --- Real-browser proof: reproduce_finding() against actual Playwright -----

def test_reproduce_finding_real_browser_broken_persists_and_fixed_does_not():
    """Drives the REAL reproduce_finding() (no mocked reproduce_fn) against
    actual Playwright-rendered pages via data: URLs (no network dependency).
    Proves the browser-based reproduction path itself, not just route()'s
    gating logic around a stubbed-out reproduce_fn."""
    from playwright.async_api import async_playwright

    bad_html = "<html><body><p><script>var a=1;</script></p></body></html>"
    clean_html = "<html><body><p>All clean, no injection here.</p></body></html>"
    bad_url = "data:text/html," + bad_html
    clean_url = "data:text/html," + clean_html

    async def _run():
        async with async_playwright() as pw:
            guard = crawl_guard.SweepGuard(delay=0)
            site = {}
            broken_finding = {
                "check_id": "autop_injection", "severity": "high",
                "urls": [bad_url], "_viewport_by_url": {bad_url: "desktop"},
            }
            fixed_finding = {
                "check_id": "autop_injection", "severity": "high",
                "urls": [clean_url], "_viewport_by_url": {clean_url: "desktop"},
            }
            still_broken = await run.reproduce_finding(pw, broken_finding, site, guard)
            now_clean = await run.reproduce_finding(pw, fixed_finding, site, guard)
            return still_broken, now_clean

    still_broken, now_clean = asyncio.run(_run())
    assert still_broken is True, "a genuinely still-broken page must reproduce on the fresh check"
    assert now_clean is False, "a page that no longer has the defect must NOT reproduce"
    print(f"EVIDENCE (a/b, real browser): autop_injection on wpautop-wrapped script page -> "
          f"reproduce_finding()={still_broken} (still fires); on a clean page -> "
          f"reproduce_finding()={now_clean} (does not fire, would downgrade to flaky/medium)")


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        import tempfile
        import types

        class _MP:
            """Minimal monkeypatch stand-in for the plain-script runner."""
            def __init__(self):
                self._sets = []
                self._env = []

            def setattr(self, obj, name, value):
                self._sets.append((obj, name, getattr(obj, name)))
                setattr(obj, name, value)

            def setenv(self, key, value):
                import os
                self._env.append((key, os.environ.get(key)))
                os.environ[key] = value

            def undo(self):
                import os
                for obj, name, old in self._sets:
                    setattr(obj, name, old)
                for key, old in self._env:
                    if old is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = old

        import inspect
        sig = inspect.signature(fn)
        kwargs = {}
        mp = None
        tmp_ctx = None
        if "tmp_path" in sig.parameters:
            tmp_ctx = tempfile.TemporaryDirectory()
            kwargs["tmp_path"] = Path(tmp_ctx.name)
        if "monkeypatch" in sig.parameters:
            mp = _MP()
            kwargs["monkeypatch"] = mp
        try:
            fn(**kwargs)
        finally:
            if mp:
                mp.undo()
            if tmp_ctx:
                tmp_ctx.cleanup()
        passed += 1
        print(f"  ok  {name}")
    print(f"\n{passed}/{len(fns)} tests passed")
