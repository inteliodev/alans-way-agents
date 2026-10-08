"""Prove pinned Hermes discovers and calls the intelio computers MCP tools.

Run with the Hermes venv's python, HERMES_HOME set to the throwaway profile
home that `scripts/intelio-computers.sh apply` configured, and
scripts/fake_nodes_mcp.py serving on 127.0.0.1:8645 (CI job hermes-contract).

Uses Hermes' real discovery entry point (tools/mcp_tool_discovery.py
discover_mcp_tools) and its tool registry dispatch, so it covers: config load
for the profile, ${INTELIO_NODES_MCP_TOKEN} expansion from that profile's
.env, the streamable-HTTP transport, tool naming, and a tool call round trip.
Never run against a real HERMES_HOME.
"""
import json
import os
from pathlib import Path
import sys

SERVER = "intelio_computers"
TOOLS = ("list_computers", "computer_info", "list_dir", "read_file", "write_file",
         "search_files", "run_command", "screenshot")


def main():
    home = os.environ.get("HERMES_HOME", "")
    if not home or Path(home).resolve() == (Path.home() / ".hermes").resolve():
        print("refusing: set HERMES_HOME to a throwaway profile home")
        return 2
    # The token must come from the profile's .env through Hermes' own ${VAR}
    # expansion, not from the process environment.
    if "INTELIO_NODES_MCP_TOKEN" in os.environ:
        print("refusing: INTELIO_NODES_MCP_TOKEN is in the environment; the contract is the .env path")
        return 2
    from tools.mcp_tool_discovery import discover_mcp_tools
    from tools.mcp_tool_schema import mcp_prefixed_tool_name
    from tools.registry import registry

    failures = []
    names = discover_mcp_tools()
    expected = {tool: mcp_prefixed_tool_name(SERVER, tool) for tool in TOOLS}
    ours = sorted(n for n in names if SERVER in n)
    print("hermes tool names for %s: %s" % (SERVER, ", ".join(ours) or "<none>"))
    missing = sorted(set(expected.values()) - set(names))
    if missing:
        failures.append("tools not discovered: %s" % missing)
    else:
        for tool, args in (("list_computers", {}), ("computer_info", {"computer": "ci-laptop"})):
            raw = registry.dispatch(expected[tool], args)
            text = raw if isinstance(raw, str) else json.dumps(raw)
            if "fake-intelio-relay" not in text:
                failures.append("%s result did not come from the fake relay: %s" % (tool, text[:300]))
            else:
                print("ok   %s -> %s" % (expected[tool], text[:200]))
    try:
        from tools.mcp_tool_lifecycle import shutdown_mcp_servers
        shutdown_mcp_servers()
    except Exception as exc:  # teardown only
        print("note shutdown: %s" % exc)
    for failure in failures:
        print("FAIL", failure)
    if not failures:
        print("ok   pinned Hermes discovered %d %s tools and called them over MCP" % (len(expected), SERVER))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
