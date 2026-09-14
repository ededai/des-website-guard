"""
TLS certificate health for a site: real handshake, chain verification against
the runner's system root store, hostname match, and expiry window. No browser.

Why this exists (2026-09-14): Ed's phone showed NET::ERR_CERT_AUTHORITY_INVALID
("'Fortinet' is stopping Chrome from safely connecting") on auraanimalrehab.com.
The site's own certificate was fine (Google Trust Services WR1, expiring
2026-10-20, covering apex + www); the phone was on a Wi-Fi network whose
FortiGate firewall does SSL inspection and swaps in its own certificate. That is
a network-side problem Des cannot fix, but until now Des could not SAY "the site
is fine" either, and it would also have missed a genuine expiry or chain break
until customers reported it. This check closes both gaps: it fires only on
faults the site actually serves, and a clean result is proof the site is not
the cause of a browser certificate warning.

Emitted check ids (all site-level, one URL per probed host):
  tls_cert_invalid     critical  chain/hostname/expiry verification failed
  tls_handshake_failed critical  could not complete a TLS handshake at all
  tls_cert_expiring    high      <= EXPIRY_HIGH_DAYS days left
                       medium    <= EXPIRY_MEDIUM_DAYS days left
"""
import socket
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

CHECK_IDS = {"tls_cert_invalid", "tls_handshake_failed", "tls_cert_expiring"}

# Automattic serves both sites from shared 90-day Google Trust Services certs
# and reissues them roughly 30 days before expiry (observed 2026-09-14: TRW at
# 29 days left, AURA at 36, both routine). So 30 days is normal, not a fault:
# medium at 21 days means renewal is at least a week late, high at 10 days
# means it is badly late and someone has to chase WordPress.com support.
EXPIRY_HIGH_DAYS = 10
EXPIRY_MEDIUM_DAYS = 21
TIMEOUT_SECONDS = 15


def hosts_for(site):
    """Apex + www (whichever the site url is, plus its twin), then any
    `tls_extra_hosts` from sites/<site>.yaml. Both apex and www matter: the
    site redirects one to the other, and a cert missing either SAN breaks the
    first hop before any redirect can happen."""
    host = urlparse(site["url"]).hostname
    twin = host[4:] if host.startswith("www.") else "www." + host
    hosts = [host, twin]
    for extra in site.get("tls_extra_hosts") or []:
        if extra and extra not in hosts:
            hosts.append(extra)
    return hosts


def probe_host(host, port=443, timeout=TIMEOUT_SECONDS):
    """Full verified handshake. Raises ssl.SSLCertVerificationError on any
    chain/hostname/expiry problem, other OSError on connect/handshake failure.
    Returns {not_after, issuer, subject, sans} on success."""
    ctx = ssl.create_default_context()  # verifies chain + hostname against system roots
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
            cert = tls_sock.getpeercert()
    issuer = {k: v for rdn in cert.get("issuer", ()) for k, v in rdn}
    subject = {k: v for rdn in cert.get("subject", ()) for k, v in rdn}
    return {
        "not_after": datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc),
        "issuer": issuer.get("organizationName") or issuer.get("commonName") or "?",
        "subject": subject.get("commonName", "?"),
        "sans": [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"],
    }


def check_site_tls(site, now=None, probe=probe_host):
    """Return raw findings in the same shape render_and_check() emits, so they
    flow through dedupe -> route -> bug_log unchanged. `probe` is injectable
    for tests and for the reproduce gate."""
    now = now or datetime.now(timezone.utc)
    findings = []
    for host in hosts_for(site):
        url = f"https://{host}/"
        try:
            info = probe(host)
        except ssl.SSLCertVerificationError as e:
            findings.append({
                "url": url, "viewport": "n/a", "check": "tls_cert_invalid", "severity": "critical",
                "evidence": f"certificate for {host} failed verification: {getattr(e, 'verify_message', None) or e}",
            })
            continue
        except (OSError, ssl.SSLError) as e:  # includes socket.timeout, ConnectionRefusedError, gaierror
            findings.append({
                "url": url, "viewport": "n/a", "check": "tls_handshake_failed", "severity": "critical",
                "evidence": f"TLS handshake to {host}:443 failed: {e.__class__.__name__}: {e}",
            })
            continue

        days_left = (info["not_after"] - now).days
        expiry = info["not_after"].strftime("%Y-%m-%d")
        if days_left < 0:
            # Unreachable with the real probe (verification already fails), kept
            # so an injected probe / clock skew can never read as healthy.
            findings.append({
                "url": url, "viewport": "n/a", "check": "tls_cert_invalid", "severity": "critical",
                "evidence": f"certificate for {host} expired {expiry} (issuer {info['issuer']})",
            })
        elif days_left <= EXPIRY_HIGH_DAYS:
            findings.append({
                "url": url, "viewport": "n/a", "check": "tls_cert_expiring", "severity": "high",
                "evidence": f"certificate for {host} expires {expiry} ({days_left}d left, issuer {info['issuer']}); auto-renewal is late",
            })
        elif days_left <= EXPIRY_MEDIUM_DAYS:
            findings.append({
                "url": url, "viewport": "n/a", "check": "tls_cert_expiring", "severity": "medium",
                "evidence": f"certificate for {host} expires {expiry} ({days_left}d left, issuer {info['issuer']})",
            })
        else:
            print(f"DES: tls ok {host} — issuer {info['issuer']}, expires {expiry} ({days_left}d)")
    return findings
