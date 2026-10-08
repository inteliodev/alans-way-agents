#!/usr/bin/env python3
"""Read one Hermes platform_toolsets entry.

Hermes 5d3c059 stores the value as a flow list on one line:

    platform_toolsets:
      telegram: [web, terminal, file, todo]

Hand edits and older saves use a block list, at either indent:

    platform_toolsets:
      telegram:
        - browser
        - terminal

A quoted string of a list is accepted too:

    telegram: '["proactivity", "terminal"]'

Exit 0 when TOOLSET is in that platform's list, 1 when it is absent.
The scan stops at the end of the platform entry, so a later `- browser`
under another key is not treated as enabled.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _strip_comment(text: str) -> str:
    out = []
    quote = ""
    for char in text:
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out).rstrip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        inner = value[1:-1]
        if value[0] == "'":
            inner = inner.replace("''", "'")
        else:
            inner = inner.replace(r"\"", '"').replace(r"\\", "\\")
        return inner.strip()
    return value


def _split_items(body: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    quote = ""
    for char in body:
        if quote:
            buf.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            buf.append(char)
            continue
        if char == ",":
            items.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return [_unquote(item) for item in items if item]


def _parse_flow(raw: str) -> list[str]:
    value = _unquote(raw.strip())
    if len(value) >= 2 and value[0] == "[" and value[-1] == "]":
        return _split_items(value[1:-1])
    return []


def platform_toolset_names(text: str, platform: str) -> list[str] | None:
    """Return toolset names for platform, or None when that key is absent."""
    lines = text.splitlines()
    in_section = False
    index = 0
    platform_re = re.compile(rf"^  {re.escape(platform)}:(.*)$")
    sibling_re = re.compile(r"^  [A-Za-z0-9_].*:")
    top_re = re.compile(r"^[A-Za-z0-9_].*:")
    item_re = re.compile(r"^\s+-\s+([^#\s]+)\s*(?:#.*)?$")
    while index < len(lines):
        line = lines[index]
        if not in_section:
            if re.match(r"^platform_toolsets:\s*(?:#.*)?$", line):
                in_section = True
            index += 1
            continue
        if top_re.match(line):
            return None
        match = platform_re.match(line)
        if not match:
            index += 1
            continue
        rest = _strip_comment(match.group(1)).strip()
        if rest:
            return _parse_flow(rest)
        names: list[str] = []
        index += 1
        while index < len(lines):
            item = lines[index]
            stripped = item.strip()
            if stripped == "" or stripped.startswith("#"):
                index += 1
                continue
            if sibling_re.match(item) or top_re.match(item):
                break
            item_match = item_re.match(item)
            if item_match:
                names.append(_unquote(item_match.group(1)))
                index += 1
                continue
            break
        return names
    return None


def toolset_enabled(text: str, platform: str, toolset: str) -> bool:
    names = platform_toolset_names(text, platform)
    return bool(names) and toolset in names


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: platform_toolsets.py CONFIG PLATFORM TOOLSET", file=sys.stderr)
        return 2
    path, platform, toolset = argv[1], argv[2], argv[3]
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return 1
    return 0 if toolset_enabled(text, platform, toolset) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
