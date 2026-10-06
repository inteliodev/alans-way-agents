#!/usr/bin/env python3
"""Check tracked files before publication; findings never echo matched secrets.

A conservative repository policy check, not a complete secret detector.
Run after git add and before committing/pushing. CI runs it on a clean checkout.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
import re
import stat
import subprocess
import sys

RULES = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("github-credential", re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}")),
    ("telegram-bot-token", re.compile(r"\d{9,10}:[A-Za-z0-9_-]{35}")),
    ("personal-home-path", re.compile(
        "/" + r"Users/(?!user/|macuser/|your-name/|you/)[^/\s'\"<>]+/"
        + "|/" + r"home/(?!user/|macuser/|your-name/|you/)[^/\s'\"<>]+/"
    )),
)

IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
TAILNET = ipaddress.ip_network((0x64400000, 10))
EXAMPLE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    (0xC0000200, 24), (0xC6336400, 24), (0xCB007100, 24),
))

FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pem", ".key", ".patch", ".diff", ".log"}
FORBIDDEN_NAMES = {".env", "credentials.json", "auth.json", "tokens.json"}


def inspect_text(path: str, text: str) -> list[dict]:
    """Return locations/rule labels only, never matching text or secret values."""
    item = Path(path)
    name = item.name.casefold()
    findings = []
    if (item.suffix.casefold() in FORBIDDEN_SUFFIXES
            or name in FORBIDDEN_NAMES
            or name.startswith(".env.")):
        findings.append({"file": path, "line": 0, "rule": "runtime-or-private-artifact"})
    for number, line in enumerate(text.splitlines(), 1):
        for label, pattern in RULES:
            if pattern.search(line):
                findings.append({"file": path, "line": number, "rule": label})
        for match in IPV4.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            # Unspecified addresses are not private device addresses. Loopback and
            # documentation example ranges are allowed the same way.
            if address.is_loopback or address.is_unspecified or any(address in network for network in EXAMPLE_NETWORKS):
                continue
            if address.is_private or address in TAILNET:
                findings.append({"file": path, "line": number, "rule": "private-device-address"})
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    paths = [value.decode("utf-8") for value in result.stdout.split(b"\x00") if value]
    if not paths:
        print("Publication check refused: no tracked files.", file=sys.stderr)
        return 1
    findings = []
    for path in paths:
        item = root / path
        mode = item.lstat().st_mode
        if not stat.S_ISREG(mode):
            findings.append({"file": path, "line": 0, "rule": "nonregular-file"})
            continue
        try:
            text = item.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            findings.append({"file": path, "line": 0, "rule": "unreadable-or-binary"})
            continue
        findings.extend(inspect_text(path, text))
    for finding in findings:
        print(f"{finding['file']}:{finding['line']}: {finding['rule']}", file=sys.stderr)
    if findings:
        print(f"Publication check failed: {len(findings)} finding(s).", file=sys.stderr)
        return 1
    print(f"Publication policy passed for {len(paths)} tracked files. Not a complete secret audit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
