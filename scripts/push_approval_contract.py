#!/usr/bin/env python3
"""Pinned-Hermes contract for push approval (CI hermes-contract job; run with Hermes importable).

Proves, against the real Hermes code in a throwaway HERMES_HOME with ``approvals.mode: off``:
  1. mode off short-circuits the pattern gate and request_tool_approval (why a plugin is needed);
  2. request_elicitation_consent still asks under mode off, through the same gateway notify /
     resolve machinery the intelio app (approval.request + POST /v1/runs/<id>/approval) and
     Telegram use, and maps once -> accept, deny -> decline;
  3. the push-approval plugin, loaded by the real plugin manager, asks for a push only and turns
     an approval into a one-time grant on the command; a denial blocks the tool.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def setup_home() -> Path:
    home = Path(os.environ.get("HERMES_HOME") or tempfile.mkdtemp(prefix="hermes-push-"))
    os.environ["HERMES_HOME"] = str(home)
    state = Path(tempfile.mkdtemp(prefix="push-state-"))
    os.environ["XDG_STATE_HOME"] = str(state)
    os.environ["XDG_CONFIG_HOME"] = str(state / "config")
    target = home / "plugins" / "push-approval"
    if not (target / "plugin.yaml").exists():
        shutil.copytree(REPO / "push-approval", target, ignore=shutil.ignore_patterns("__pycache__"))
    cfg = home / "config.yaml"
    try:
        import hermes_yaml as yaml  # Hermes ships ruamel, not always PyYAML
    except ImportError:
        import yaml
    data = yaml.safe_load(cfg.read_text()) if cfg.exists() else {}
    data = data or {}
    data.setdefault("approvals", {})["mode"] = "off"
    plugins = data.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if "push-approval" not in enabled:
        enabled.append("push-approval")
    plugins["enabled"] = enabled
    cfg.write_text(yaml.safe_dump(data))
    return home


def answer_next(session_key: str, choice: str, seen: list) -> threading.Thread:
    """Resolve the next approval for session_key like POST /v1/runs/<id>/approval would."""
    from tools.approval import resolve_gateway_approval

    def worker():
        deadline = time.time() + 20
        while time.time() < deadline:
            if seen and resolve_gateway_approval(session_key, choice) > 0:
                return
            time.sleep(0.05)
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def main() -> int:
    setup_home()
    os.environ["HERMES_SESSION_PLATFORM"] = "api_server"  # the intelio app's session stream surface
    from tools import approval
    from tools.approval_context import _get_approval_mode, set_current_session_key, reset_current_session_key
    from tools.approval_prompt import request_elicitation_consent

    assert _get_approval_mode() == "off", _get_approval_mode()
    print("ok   approvals.mode is off in the throwaway home")
    assert approval.check_all_command_guards("git push --force origin main", "local")["approved"] is True
    assert approval.request_tool_approval("terminal", "push")["approved"] is True
    print("ok   mode off: pattern gate and request_tool_approval approve without asking")

    for choice, expected in (("once", "accept"), ("deny", "decline")):
        key = f"run_contract_{choice}"
        seen: list = []
        approval.register_gateway_notify(key, lambda data, seen=seen: seen.append(data))
        token = set_current_session_key(key)
        try:
            t = answer_next(key, choice, seen)
            got = request_elicitation_consent("prc wants to push: git push", "intelio push approval",
                                              surface="push-approval")
            t.join(5)
        finally:
            reset_current_session_key(token)
            approval.unregister_gateway_notify(key)
        assert seen and "git push" in seen[0].get("command", ""), seen
        assert got == expected, (choice, got)
        print(f"ok   mode off: elicitation consent asked the session surface; {choice} -> {got}")

    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks, discover_plugins, get_plugin_manager
    discover_plugins()
    loaded = get_plugin_manager()._plugins.get("push-approval")
    assert loaded is not None and loaded.enabled, sorted(get_plugin_manager()._plugins)
    print("ok   push-approval loaded by the real plugin manager")

    key = "run_contract_plugin"
    seen = []
    approval.register_gateway_notify(key, lambda data: seen.append(data))
    token = set_current_session_key(key)
    try:
        block, modified = _dispatch_pre_tool_call_hooks("terminal", {"command": "ls -la"})
        assert block is None and modified is None and not seen, (block, modified, seen)
        print("ok   non-push terminal call: no prompt, unchanged")
        t = answer_next(key, "once", seen)
        block, modified = _dispatch_pre_tool_call_hooks("terminal", {"command": "git push origin main"})
        t.join(5)
        assert block is None, block
        assert modified and modified["command"].startswith("export INTELIO_PUSH_GRANT="), modified
        print("ok   push allowed once: command carries a one-time grant")
        seen.clear()
        t = answer_next(key, "deny", seen)
        block, modified = _dispatch_pre_tool_call_hooks("terminal", {"command": "gh pr merge 29"})
        t.join(5)
        assert block and "not approved" in block, block
        print("ok   push denied: tool blocked")
    finally:
        reset_current_session_key(token)
        approval.unregister_gateway_notify(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
