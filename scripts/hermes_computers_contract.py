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
import re
from pathlib import Path
import sys

SERVER = "intelio_computers"
TOOLS = ("list_computers", "computer_info", "list_dir", "read_file", "write_file",
         "search_files", "run_command", "screenshot",
         # stage 2: persistent terminal sessions
         "start_session", "send_input", "read_output", "stop_session", "list_sessions")


def call(registry, name, args, failures):
    raw = registry.dispatch(name, args)
    text = raw if isinstance(raw, str) else json.dumps(raw)
    if "fake-intelio-relay" not in text:
        failures.append("%s result did not come from the fake relay: %s" % (name, text[:300]))
        return None
    return text


def session_round_trip(registry, expected, failures):
    """start_session -> read_output (cursor) -> stop_session through Hermes dispatch."""
    computer = "ci-laptop"
    text = call(registry, expected["start_session"], {"computer": computer, "command": "contract-shell"},
                failures)
    # Hermes may wrap the MCP text in its own JSON envelope, escaping the quotes.
    match = text and re.search(r'session_id\\*"\s*:\s*\\*"([A-Za-z0-9_-]+)', text)
    if not match:
        failures.append("start_session returned no session_id: %s" % (text or "")[:300])
        return
    session_id = match.group(1)
    print("ok   %s -> session %s" % (expected["start_session"], session_id))
    text = call(registry, expected["read_output"], {"computer": computer, "session_id": session_id,
                                                    "since": 0, "wait_ms": 0}, failures)
    if text is not None:
        cursor = re.search(r'cursor\\*"\s*:\s*(\d+)', text)
        if "started: contract-shell" not in text or not cursor or int(cursor.group(1)) <= 0:
            failures.append("read_output did not return the session output and an advanced cursor: %s"
                            % text[:300])
        else:
            print("ok   %s -> cursor %s" % (expected["read_output"], cursor.group(1)))
    call(registry, expected["stop_session"], {"computer": computer, "session_id": session_id}, failures)


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
    extra = sorted(set(ours) - set(expected.values()))
    if missing:
        failures.append("tools not discovered: %s" % missing)
    elif extra:
        failures.append("unexpected %s tools: %s" % (SERVER, extra))
    else:
        for tool, args in (("list_computers", {}), ("computer_info", {"computer": "ci-laptop"})):
            text = call(registry, expected[tool], args, failures)
            if text is not None:
                print("ok   %s -> %s" % (expected[tool], text[:200]))
        session_round_trip(registry, expected, failures)
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
