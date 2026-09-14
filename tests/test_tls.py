"""
Unit tests for checks/tls.py and its wiring into src/run.py.

The probe is injected, so no network is needed: each test hands
check_site_tls() a fake `probe` that returns a cert summary or raises the
exception the real ssl/socket stack would raise. One opt-in live test at the
bottom hits the real sites (skipped unless DES_LIVE_TLS=1).

    ./.venv/bin/python -m pytest tests/test_tls.py -q
"""
import asyncio
import os
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from checks import tls
from src import run

NOW = datetime(2026, 9, 14, tzinfo=timezone.utc)
SITE = {"name": "AURA", "url": "https://auraanimalrehab.com"}


def _ok(days):
    def probe(host):
        return {"not_after": NOW + timedelta(days=days), "issuer": "Google Trust Services",
                "subject": "tls.automattic.com", "sans": [host]}
    return probe


def test_hosts_for_probes_apex_and_www_plus_extras():
    assert tls.hosts_for(SITE) == ["auraanimalrehab.com", "www.auraanimalrehab.com"]
    assert tls.hosts_for({"url": "https://www.example.com"}) == ["www.example.com", "example.com"]
    extra = dict(SITE, tls_extra_hosts=["app.auraanimalrehab.com", "auraanimalrehab.com"])
    assert tls.hosts_for(extra)[-1] == "app.auraanimalrehab.com"
    assert len(tls.hosts_for(extra)) == 3  # duplicate not re-added


def test_healthy_cert_emits_nothing():
    assert tls.check_site_tls(SITE, now=NOW, probe=_ok(36)) == []


def test_verification_failure_is_critical_invalid_per_host():
    def probe(host):
        err = ssl.SSLCertVerificationError(1, "certificate verify failed")
        err.verify_message = "unable to get local issuer certificate"
        raise err
    out = tls.check_site_tls(SITE, now=NOW, probe=probe)
    assert [f["check"] for f in out] == ["tls_cert_invalid", "tls_cert_invalid"]
    assert all(f["severity"] == "critical" for f in out)
    assert {f["url"] for f in out} == {"https://auraanimalrehab.com/", "https://www.auraanimalrehab.com/"}
    assert "unable to get local issuer certificate" in out[0]["evidence"]


def test_connect_failure_is_critical_handshake_failed():
    def probe(host):
        raise socket.timeout("timed out")
    out = tls.check_site_tls(SITE, now=NOW, probe=probe)
    assert {f["check"] for f in out} == {"tls_handshake_failed"}
    assert all(f["severity"] == "critical" for f in out)


def test_expiry_windows():
    assert {f["severity"] for f in tls.check_site_tls(SITE, now=NOW, probe=_ok(9))} == {"high"}
    assert {f["severity"] for f in tls.check_site_tls(SITE, now=NOW, probe=_ok(15))} == {"medium"}
    # 29 days left is a routine Automattic reissue window (TRW, 2026-09-14): silent.
    assert tls.check_site_tls(SITE, now=NOW, probe=_ok(29)) == []
    expired = tls.check_site_tls(SITE, now=NOW, probe=_ok(-1))
    assert {f["check"] for f in expired} == {"tls_cert_invalid"}


def test_mixed_hosts_only_flag_the_broken_one():
    def probe(host):
        if host.startswith("www."):
            raise ConnectionRefusedError("refused")
        return _ok(60)(host)
    out = tls.check_site_tls(SITE, now=NOW, probe=probe)
    assert len(out) == 1 and out[0]["url"] == "https://www.auraanimalrehab.com/"


def test_reproduce_gate_reprobes_tls_instead_of_rendering(monkeypatch):
    finding = {"check_id": "tls_cert_invalid", "severity": "critical",
               "urls": ["https://auraanimalrehab.com/"]}
    calls = []
    monkeypatch.setattr(run.tls, "check_site_tls",
                        lambda site: calls.append(site) or
                        [{"check": "tls_cert_invalid", "url": site["url"], "severity": "critical"}])
    assert asyncio.run(run.reproduce_finding(None, finding, SITE, None)) is True
    assert calls == [SITE]
    monkeypatch.setattr(run.tls, "check_site_tls", lambda site: [])
    assert asyncio.run(run.reproduce_finding(None, finding, SITE, None)) is False


def test_gate2_does_not_bury_tls_on_small_runs():
    async def yes(_): return True
    finding = {"check_id": "tls_cert_invalid", "severity": "critical", "site": "AURA",
               "title": "Tls Cert Invalid", "urls": ["https://a/", "https://www.a/"], "evidence": "x"}
    asyncio.run(run.route(finding, dry_run=True, reproduce_fn=yes, total_pages=2))
    assert finding["severity"] == "critical"
    assert not finding.get("suspected_checker_defect")


@pytest.mark.skipif(os.environ.get("DES_LIVE_TLS") != "1", reason="set DES_LIVE_TLS=1 for a live probe")
def test_live_sites_are_clean():
    for name in ("aura", "trw"):
        site = run.load_site(name)
        assert tls.check_site_tls(site) == [], name
