#!/usr/bin/env python3
"""Allow noVNC to bind to loopback or a Tailscale address, nothing public.

Exit 0 when the address is safe to pass to websockify. Exit 1 otherwise.
Unspecified addresses (every interface) are refused. Tailscale's IPv4 CGNAT
range and its IPv6 ULA are allowed so a phone on the tailnet can open the
viewer without a public listener. Ranges are built without embedding device
addresses.
"""
from __future__ import annotations

import ipaddress
import sys

TAILSCALE_V4 = ipaddress.ip_network((0x64400000, 10))
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


def allowed(value: str) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.is_unspecified:
        return False
    if address.is_loopback:
        return True
    return address in TAILSCALE_V4 or address in TAILSCALE_V6


def main() -> int:
    if len(sys.argv) != 2:
        return 1
    return 0 if allowed(sys.argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main())
