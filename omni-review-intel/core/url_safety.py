"""
core/url_safety.py — Public URL validation to prevent SSRF on public web deployments.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".lan", ".home", ".arpa")


def is_safe_public_url(url: str) -> tuple[bool, str]:
    """Reject non-HTTP schemes and loopback/private/link-local targets on public deployments."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme.lower() not in ("http", "https"):
        return False, "Only http:// and https:// URLs are supported."
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or "." not in host:
        return False, f"Invalid URL host: {url}"
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False, f"Local or internal hostnames are not permitted: {host}"
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False, f"Private or reserved IP addresses are not permitted: {host}"
    except ValueError:
        pass
    return True, ""
