"""Load the installed alans-way plugin through the real Hermes plugin manager.

Run inside a Hermes install (CI job `hermes-contract`) with HERMES_HOME set to
a throwaway home where `hermes plugins install --enable` already placed the
plugin. Exits non-zero with a reason when the contract the plugin relies on
breaks. Never run against a real HERMES_HOME.
"""
import os
from pathlib import Path
import sys

PLUGIN = "alans-way"
COMMANDS = {"proactivity", "watch"}
CLI_COMMANDS = {"proactivity"}


def manifest_tools():
    root = Path(__file__).resolve().parents[1]
    tools, inside = [], False
    for line in (root / PLUGIN / "plugin.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("provides_tools:"):
            inside = True
            continue
        if inside and line.startswith("  - "):
            tools.append(line[4:].strip())
        elif inside and line and not line.startswith(" "):
            break
    return set(tools)


def main():
    home = os.environ.get("HERMES_HOME", "")
    if not home or Path(home).resolve() == (Path.home() / ".hermes").resolve():
        print("refusing: set HERMES_HOME to a throwaway home")
        return 2
    from hermes_cli.plugins import discover_plugins, get_plugin_commands, get_plugin_manager

    discover_plugins()
    manager = get_plugin_manager()
    failures = []
    entries = [p for p in manager.list_plugins() if p.get("key") == PLUGIN or p.get("name") == PLUGIN]
    if not entries:
        print("FAIL plugin %s not discovered in %s" % (PLUGIN, home))
        return 1
    entry = entries[0]
    print("plugin:", {k: entry.get(k) for k in ("key", "version", "enabled", "tools", "commands", "error")})
    if entry.get("enabled") is not True:
        failures.append("plugin not enabled")
    if entry.get("error"):
        failures.append("load error: %s" % entry["error"])
    tool_names = set(getattr(manager, "_plugin_tool_names", set()))
    missing_tools = manifest_tools() - tool_names
    if missing_tools:
        failures.append("tools not registered: %s" % sorted(missing_tools))
    missing_commands = COMMANDS - set(get_plugin_commands())
    if missing_commands:
        failures.append("slash commands not registered: %s" % sorted(missing_commands))
    cli = set(getattr(manager, "_cli_commands", {}) or {})
    missing_cli = CLI_COMMANDS - cli
    if missing_cli:
        failures.append("CLI commands not registered: %s" % sorted(missing_cli))
    for failure in failures:
        print("FAIL", failure)
    if not failures:
        print("ok   %s registers %d tools, commands %s, CLI %s"
              % (PLUGIN, len(tool_names), sorted(COMMANDS), sorted(CLI_COMMANDS)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
