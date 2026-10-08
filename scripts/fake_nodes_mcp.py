#!/usr/bin/env python3
"""Stand-in for the intelio relay's aggregated MCP server (CI contract only).

Streamable HTTP with plain JSON responses, as the relay contract specifies:
POST /mcp with JSON-RPC 2.0 (`initialize`, `notifications/*` -> 202,
`tools/list`, `tools/call`, `ping`); a missing or wrong bearer -> 401.
Stdlib only; binds loopback. Never use it as a real relay.

    python3 scripts/fake_nodes_mcp.py --token-file PATH [--port 8645]
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MARKER = "fake-intelio-relay"
COMPUTER = {"type": "string", "description": "device name or id"}


def _schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


TOOLS = [
    ("list_computers", "List enrolled computers.", _schema()),
    ("computer_info", "OS, user and volumes of one computer.",
     _schema({"computer": COMPUTER}, ["computer"])),
    ("list_dir", "List a directory.",
     _schema({"computer": COMPUTER, "path": {"type": "string"},
              "show_hidden": {"type": "boolean"}}, ["computer", "path"])),
    ("read_file", "Read a file.",
     _schema({"computer": COMPUTER, "path": {"type": "string"}, "offset": {"type": "integer"},
              "limit": {"type": "integer"}, "encoding": {"type": "string", "enum": ["text", "base64"]}},
             ["computer", "path"])),
    ("write_file", "Write a file.",
     _schema({"computer": COMPUTER, "path": {"type": "string"}, "content": {"type": "string"},
              "encoding": {"type": "string", "enum": ["text", "base64"]},
              "mode": {"type": "string", "enum": ["create", "overwrite", "append"]},
              "make_dirs": {"type": "boolean"}}, ["computer", "path", "content"])),
    ("search_files", "Search files by name or content.",
     _schema({"computer": COMPUTER, "root": {"type": "string"}, "pattern": {"type": "string"},
              "name_glob": {"type": "string"}, "max_results": {"type": "integer"}},
             ["computer", "root"])),
    ("run_command", "Run a shell command as the signed-in user.",
     _schema({"computer": COMPUTER, "command": {"type": "string"}, "cwd": {"type": "string"},
              "timeout_s": {"type": "integer"}, "shell": {"type": "string"}},
             ["computer", "command"])),
    ("screenshot", "Capture a display.",
     _schema({"computer": COMPUTER, "display": {"type": "integer"}}, ["computer"])),
    # Stage 2: persistent terminal sessions.
    ("start_session", "Start a terminal session.",
     _schema({"computer": COMPUTER, "command": {"type": "string"}, "cwd": {"type": "string"},
              "cols": {"type": "integer"}, "rows": {"type": "integer"},
              "env": {"type": "object", "additionalProperties": {"type": "string"}}},
             ["computer"])),
    ("send_input", "Send text or keys to a session.",
     _schema({"computer": COMPUTER, "session_id": {"type": "string"}, "text": {"type": "string"},
              "enter": {"type": "boolean"}, "keys": {"type": "array", "items": {"type": "string"}}},
             ["computer", "session_id"])),
    ("read_output", "Read session output after a cursor.",
     _schema({"computer": COMPUTER, "session_id": {"type": "string"}, "since": {"type": "integer"},
              "wait_ms": {"type": "integer"}, "max_bytes": {"type": "integer"}},
             ["computer", "session_id"])),
    ("stop_session", "Stop a session.",
     _schema({"computer": COMPUTER, "session_id": {"type": "string"}, "force": {"type": "boolean"}},
             ["computer", "session_id"])),
    ("list_sessions", "List open sessions on a computer.",
     _schema({"computer": COMPUTER}, ["computer"])),
]
TOOL_NAMES = [name for name, _desc, _schema_ in TOOLS]
SESSION_TOOLS = ("start_session", "send_input", "read_output", "stop_session", "list_sessions")


SESSIONS: dict = {}  # session_id -> {"output": str, "exited": bool, "exit_code": int|None}
_LOCK = threading.Lock()


def _error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _session_tool(name, args):
    """Deterministic in-memory sessions: no process runs; input is echoed back."""
    computer = args.get("computer")
    if name == "start_session":
        session_id = "s%d" % (len(SESSIONS) + 1)
        command = args.get("command") or "shell"
        SESSIONS[session_id] = {"computer": computer, "command": command,
                                "output": "%s started: %s\n" % (MARKER, command),
                                "exited": False, "exit_code": None}
        return {"marker": MARKER, "session_id": session_id, "pid": 4242, "pty": True}
    if name == "list_sessions":
        return {"marker": MARKER, "sessions": [
            {"session_id": sid, "command": s["command"], "exited": s["exited"]}
            for sid, s in SESSIONS.items() if s["computer"] == computer and not s["exited"]]}
    session = SESSIONS.get(args.get("session_id"))
    if session is None or session["computer"] != computer:
        return None
    if name == "send_input":
        if session["exited"]:
            return {"marker": MARKER, "ok": False, "error": "session exited"}
        session["output"] += (args.get("text") or "") + ("\n" if args.get("enter") else "")
        session["output"] += "".join("<%s>" % key for key in args.get("keys") or [])
        return {"marker": MARKER, "ok": True}
    if name == "read_output":
        data = session["output"].encode("utf-8")
        since = max(0, min(int(args.get("since") or 0), len(data)))
        limit = int(args.get("max_bytes") or 65536)
        chunk = data[since:since + limit]
        return {"marker": MARKER, "output": chunk.decode("utf-8", "replace"), "cursor": since + len(chunk),
                "exited": session["exited"], "exit_code": session["exit_code"],
                "truncated": since + len(chunk) < len(data)}
    # stop_session
    session["exited"] = True
    session["exit_code"] = 137 if args.get("force") else 0
    return {"marker": MARKER, "exit_code": session["exit_code"]}


def call_tool(name, args):
    if name not in TOOL_NAMES:
        return _error("unknown tool %s" % name)
    if name == "list_computers":
        payload = {"marker": MARKER, "computers": [{
            "name": "ci-laptop", "id": "dev-ci", "os": "linux", "user": "ci",
            "online": True, "last_seen": "2026-01-01T00:00:00Z", "version": "0"}]}
    elif name in SESSION_TOOLS:
        with _LOCK:
            payload = _session_tool(name, args)
        if payload is None:
            return _error("unknown session %s on %s" % (args.get("session_id"), args.get("computer")))
    else:
        payload = {"marker": MARKER, "tool": name, "computer": args.get("computer")}
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}


class Handler(BaseHTTPRequestHandler):
    token = ""
    calls: list = []

    def log_message(self, format, *args):  # quiet; the CI log shows the contract output instead
        pass

    def _send(self, status, body=None, headers=None):
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        self._send(405, {"error": "SSE stream not offered"}, {"Allow": "POST"})

    def do_DELETE(self):
        self._send(200 if self._authorized() else 401, {} if self._authorized() else {"error": "unauthorized"})

    def _authorized(self):
        return self.headers.get("Authorization", "") == "Bearer " + self.token

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/mcp":
            self._send(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            message = json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            return
        if not isinstance(message, dict):
            self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch not supported"}})
            return
        method, rid, params = message.get("method"), message.get("id"), message.get("params") or {}
        if rid is None:  # notification or a response from the client
            self._send(202)
            return
        if method == "initialize":
            wanted = params.get("protocolVersion")
            version = wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            result = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "intelio-computers-fake", "version": "0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s in TOOLS]}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            Handler.calls.append(name)
            result = call_tool(name, args)
        else:
            self._send(200, {"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32601, "message": "method not found: %s" % method}})
            return
        self._send(200, {"jsonrpc": "2.0", "id": rid, "result": result})


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--port", type=int, default=8645)
    args = parser.parse_args(argv)
    with open(args.token_file, encoding="utf-8") as fh:
        Handler.token = fh.read().strip()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("fake intelio relay MCP on http://127.0.0.1:%d/mcp" % args.port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
