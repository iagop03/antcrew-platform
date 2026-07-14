"""Central security utilities — URL and credential validation.

All outbound HTTP destinations (webhooks, repo clones) must pass
validate_external_url() before any request is made. This guards against
SSRF attacks and credential exfiltration to attacker-controlled servers.
"""
from __future__ import annotations

import ipaddress
import urllib.parse


# Hostnames that must never be targeted regardless of IP resolution.
_BLOCKED_HOSTS: frozenset[str] = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",  # nosec B104 — in a blocklist, not binding
    "169.254.169.254",       # AWS / GCP / Azure / DigitalOcean metadata
    "metadata.google.internal",
    "metadata.internal",
})


def validate_external_url(url: str, *, allow_http: bool = False) -> None:
    """Raise ValueError if *url* targets a private or internal network endpoint.

    Validation steps:
    - Scheme must be https (or http if allow_http=True)
    - Hostname must be present and not in the blocked set
    - If the hostname is an IP literal, it must not be private, loopback,
      link-local, reserved, multicast, or unspecified

    Domain names are accepted as-is (DNS resolution happens at request time).
    This means DNS rebinding is not defended here; use network-level egress
    filtering as the authoritative control for that threat.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise ValueError(f"Invalid URL: {url!r}") from exc

    scheme = (parsed.scheme or "").lower()
    allowed = {"https"} if not allow_http else {"http", "https"}
    if scheme not in allowed:
        hint = "https" if not allow_http else "http or https"
        raise ValueError(f"URL must use {hint} scheme, got {scheme!r}: {url!r}")

    # Strip IPv6 brackets before comparison
    hostname = (parsed.hostname or "").lower().strip("[]")
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    if hostname in _BLOCKED_HOSTS:
        raise ValueError(f"URL targets a blocked hostname: {hostname!r}")

    # If the hostname is an IP literal, reject private/reserved ranges
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return  # domain name — not our job to resolve it here

    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_multicast
    ):
        raise ValueError(
            f"URL targets a private/internal/reserved IP address: {addr}"
        )
