"""Drafts-only iMessage: mocked BlueBubbles, approval required before any send."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import importlib
import importlib.util
import json
import sys
import threading
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "alans-way"
PASSWORD = "pw-do-not-leak"


def load_modules():
    name = "imessage_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    imessage = importlib.import_module(name + ".imessage")
    return module, imessage


def tailnet_host():
    return ".".join(["100", "102", "67", "114"])


def public_host():
    return ".".join(["203", "0", "113", "10"])


class BlueBubbles:
    def __init__(self):
        self.requests = []
        self.redirect = False
        self.password = PASSWORD
        self.chats = [{
            "guid": "iMessage;-;+15551212",
            "displayName": "Alice",
            "chatIdentifier": "+15551212",
            "participants": [{"address": "+15551212"}],
            "lastMessage": {"text": "ping", "isFromMe": False, "dateRead": None},
        }, {
            "guid": "iMessage;-;bob@example.com",
            "displayName": "Bob",
            "chatIdentifier": "bob@example.com",
            "participants": [{"address": "bob@example.com"}],
            "lastMessage": {"text": "ok", "isFromMe": True, "dateRead": 1},
        }]
        self.messages = [{
            "text": "ping", "isFromMe": False, "dateCreated": 1,
            "handle": {"address": "+15551212"},
        }, {
            "text": "pong", "isFromMe": True, "dateCreated": 2,
            "handle": {"address": "+15551212"},
        }]
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    def paths(self):
        return [item["path"] for item in self.requests]

    def _handler(self):
        box = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def _json(self, code, payload):
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _accepted(self):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if query.get("password", [None])[0] != box.password:
                    self._json(401, {"status": 401, "message": "unauthorized"})
                    return None
                if box.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://%s/away" % public_host())
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                body = self._body()
                box.requests.append({
                    "method": self.command,
                    "path": urllib.parse.unquote(parsed.path),
                    "query_has_password": "password=" in parsed.query,
                    "body": body.decode(),
                })
                return parsed

            def do_POST(self):
                parsed = self._accepted()
                if parsed is None:
                    return
                path = urllib.parse.unquote(parsed.path)
                if path == "/api/v1/chat/query":
                    self._json(200, {"status": 200, "data": box.chats})
                elif path == "/api/v1/message/query":
                    self._json(200, {"status": 200, "data": box.messages})
                elif path == "/api/v1/message/text":
                    self._json(200, {"status": 200, "data": {"guid": "sent"}})
                else:
                    self._json(404, {"status": 404})

            def do_GET(self):
                parsed = self._accepted()
                if parsed is None:
                    return
                path = urllib.parse.unquote(parsed.path)
                if path.startswith("/api/v1/chat/") and path.endswith("/message"):
                    self._json(200, {"status": 200, "data": list(reversed(box.messages))})
                else:
                    self._json(404, {"status": 404})

        return Handler


class URLPolicyTests(unittest.TestCase):
    def setUp(self):
        _, self.imessage = load_modules()

    def test_tailnet_and_loopback_are_accepted(self):
        self.assertEqual(
            self.imessage.allowed_base_url("http://%s:1234" % tailnet_host()),
            "http://%s:1234" % tailnet_host())
        self.assertTrue(self.imessage.allowed_base_url("http://127.0.0.1:9").startswith("http://127.0.0.1"))

    def test_public_names_and_unspecified_addresses_are_refused(self):
        for raw in (
            "http://%s:1234" % public_host(),
            "http://example.com:1234",
            "http://" + ".".join(["0", "0", "0", "0"]) + ":1234",
            "http://user:secret@127.0.0.1:1234",
            "ftp://127.0.0.1:1234",
            "http://127.0.0.1:1234/api",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(self.imessage.IMessageError):
                    self.imessage.allowed_base_url(raw)


class DraftSendTests(unittest.TestCase):
    def setUp(self):
        _, self.imessage = load_modules()
        self.server = BlueBubbles()

    def tearDown(self):
        self.server.close()

    def service(self, home, **kwargs):
        kwargs.setdefault("approve", lambda draft: {"approved": False, "outcome": "timeout"})
        return self.imessage.IMessage(
            home, url=self.server.url, password=PASSWORD, **kwargs)

    def test_reads_and_drafts_never_post_a_message(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            listed = service.list_chats()
            self.assertEqual(listed["chats"][0]["display_name"], "Alice")
            self.assertTrue(listed["chats"][0]["unread"])
            self.assertFalse(listed["chats"][1]["unread"])
            read = service.read_chat("Alice")
            self.assertEqual([item["text"] for item in read["messages"]], ["ping", "pong"])
            found = service.search("ping")
            self.assertEqual(found["messages"][0]["text"], "ping")
            contacts = service.search("Alice")
            self.assertTrue(any(item["display_name"] == "Alice" for item in contacts["contacts"]))
            draft = service.draft_reply("Alice", "Hello there")
            self.assertEqual(draft["draft"]["status"], "pending")
            self.assertNotIn("/api/v1/message/text", self.server.paths())
            self.assertNotIn(PASSWORD, json.dumps(draft))
            self.assertTrue(all(item["query_has_password"] for item in self.server.requests))

    def test_send_without_approval_is_impossible(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            draft = service.draft_reply("+15551212", "Hello there")["draft"]
            result = service.send_draft(draft["id"])
            self.assertFalse(result["sent"])
            self.assertEqual(result["error"], "not approved; nothing sent")
            self.assertNotIn("/api/v1/message/text", self.server.paths())
            stored = json.loads((Path(directory) / "imessage" / "drafts" / (draft["id"] + ".json")).read_text())
            self.assertEqual(stored["status"], "pending")
            self.assertEqual(stored["text"], "Hello there")
            log_text = (Path(directory) / "imessage" / "sends.jsonl").read_text()
            self.assertIn('"outcome": "denied"', log_text)
            self.assertIn('"approval": "timeout"', log_text)
            self.assertNotIn(PASSWORD, log_text)
            self.assertNotIn(PASSWORD, json.dumps(result))

    def test_approved_send_posts_the_stored_text_once(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            moment = {"now": datetime(2026, 10, 6, tzinfo=timezone.utc)}
            service = self.service(
                directory,
                approve=lambda draft: {"approved": True, "outcome": "approved"},
                now=lambda: moment["now"],
            )
            first = service.draft_reply("Alice", "Hello there")["draft"]
            revised = service.draft_reply("Alice", "Different words")["draft"]
            self.assertNotEqual(first["id"], revised["id"])
            self.assertEqual(
                json.loads((Path(directory) / "imessage" / "drafts" / (first["id"] + ".json")).read_text())["text"],
                "Hello there")
            result = service.send_draft(first["id"])
            self.assertTrue(result["sent"])
            self.assertEqual(result["text"], "Hello there")
            posted = [json.loads(item["body"]) for item in self.server.requests
                      if item["path"] == "/api/v1/message/text"]
            self.assertEqual(len(posted), 1)
            self.assertEqual(posted[0]["message"], "Hello there")
            self.assertEqual(posted[0]["chatGuid"], "iMessage;-;+15551212")
            again = service.send_draft(first["id"])
            self.assertFalse(again["sent"])
            self.assertEqual(len([item for item in self.server.requests if item["path"] == "/api/v1/message/text"]), 1)
            moment["now"] = moment["now"] + timedelta(seconds=5)
            limited = service.send_draft(revised["id"])
            self.assertFalse(limited["sent"])
            self.assertIn("rate limit", limited["error"])
            self.assertEqual(len([item for item in self.server.requests if item["path"] == "/api/v1/message/text"]), 1)

    def test_ambiguous_chat_does_not_draft(self):
        import tempfile
        self.server.chats.append(dict(self.server.chats[0], guid="iMessage;-;other"))
        self.server.chats[2]["displayName"] = "Alice"
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            result = service.draft_reply("Alice", "Hi")
            self.assertFalse(result["ok"])
            self.assertFalse((Path(directory) / "imessage" / "drafts").exists())
            self.assertNotIn("/api/v1/message/text", self.server.paths())

    def test_redirect_and_rejection_hide_the_password(self):
        import tempfile
        self.server.redirect = True
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            with self.assertRaises(self.imessage.IMessageError) as caught:
                service.list_chats()
            self.assertNotIn(PASSWORD, str(caught.exception))
            self.assertNotIn("/api/v1/message/text", self.server.paths())

    def test_native_gate_refuses_bypass_and_timeout(self):
        draft = {
            "id": "d-" + "ab" * 8, "chat_label": "Alice",
            "chat_guid": "iMessage;-;+15551212", "text": "Hello there",
        }
        calls = []

        def install(*, yolo=False, mode="manual", result=None, missing=False):
            saved = {key: sys.modules.get(key) for key in ("tools", "tools.approval", "tools.approval_context")}
            if missing:
                sys.modules["tools"] = None
                sys.modules["tools.approval"] = None
                sys.modules["tools.approval_context"] = None
                return saved
            tools = type(sys)("tools")
            approval = type(sys)("tools.approval")
            context = type(sys)("tools.approval_context")

            def request_tool_approval(name, reason, rule_key=""):
                calls.append({"name": name, "reason": reason, "rule_key": rule_key})
                return result

            approval.request_tool_approval = request_tool_approval
            approval.is_current_session_yolo_enabled = lambda: yolo
            context._get_approval_mode = lambda: mode
            sys.modules["tools"] = tools
            sys.modules["tools.approval"] = approval
            sys.modules["tools.approval_context"] = context
            return saved

        def restore(saved):
            for key, value in saved.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

        saved = install(yolo=True, result={"approved": True})
        try:
            refused = self.imessage.hermes_approve(draft)
        finally:
            restore(saved)
        self.assertFalse(refused["approved"])
        self.assertEqual(calls, [])

        saved = install(mode="off", result={"approved": True})
        try:
            refused = self.imessage.hermes_approve(draft)
        finally:
            restore(saved)
        self.assertEqual(refused["outcome"], "bypass_refused")
        self.assertEqual(calls, [])

        saved = install(result={"approved": False, "outcome": "timeout"})
        try:
            refused = self.imessage.hermes_approve(draft)
        finally:
            restore(saved)
        self.assertFalse(refused["approved"])
        self.assertEqual(refused["outcome"], "timeout")
        self.assertEqual(calls[0]["name"], "imessage_send_draft")
        self.assertIn("Alice", calls[0]["reason"])
        self.assertIn("Hello there", calls[0]["reason"])
        self.assertEqual(calls[0]["rule_key"], "imessage-draft:" + draft["id"])

        saved = install(result={"approved": True, "message": None})
        try:
            allowed = self.imessage.hermes_approve(draft)
        finally:
            restore(saved)
        self.assertTrue(allowed["approved"])

        saved = install(missing=True)
        try:
            missing = self.imessage.hermes_approve(draft)
        finally:
            restore(saved)
        self.assertEqual(missing["outcome"], "unavailable")

    def test_tools_have_no_free_form_send_and_registration_is_quiet(self):
        plugin, imessage = load_modules()
        send = next(item for item in imessage.SCHEMAS if item["name"] == "imessage_send_draft")
        self.assertEqual(list(send["parameters"]["properties"]), ["draft_id"])
        self.assertFalse(send["parameters"]["additionalProperties"])
        self.assertNotIn("text", send["parameters"]["properties"])
        names = [item["name"] for item in imessage.SCHEMAS]
        self.assertEqual(names, [
            "imessage_list_chats", "imessage_read_chat", "imessage_search",
            "imessage_draft_reply", "imessage_send_draft",
        ])

        class Facade:
            def __init__(self):
                self.tools = []
            def register_tool(self, **kwargs):
                self.tools.append(kwargs)
            def register_command(self, *args, **kwargs):
                pass
            def register_skill(self, *args, **kwargs):
                self.skill = args
            def register_hook(self, *args, **kwargs):
                pass
            def on_unload(self, *args):
                pass
            def inject_message(self, *args, **kwargs):
                raise AssertionError("registration must not inject")

        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            facade = Facade()
            plugin.register(facade, home=Path(directory), background=False)
        self.assertEqual(
            [item["name"] for item in facade.tools if item["name"].startswith("imessage_")],
            names)
        self.assertTrue(all(item["toolset"] == "imessage" for item in facade.tools if item["name"].startswith("imessage_")))
        skill = (PLUGIN / "skills" / "imessage-drafts" / "SKILL.md").read_text()
        self.assertIn("never sends", skill.casefold())
        self.assertIn("Never contact anyone proactively", skill)
        self.assertIn("Show every draft back", skill)
        self.assertIn("bluebubbles", skill.casefold())
        self.assertIn("Do not enable that platform", skill)


class MacSetupScriptTests(unittest.TestCase):
    def test_script_is_idempotent_and_non_gui(self):
        script = (ROOT / "scripts" / "mac-bluebubbles-setup.sh").read_text()
        self.assertIn("brew list --cask bluebubbles", script)
        self.assertIn("brew install --cask bluebubbles", script)
        self.assertIn("pmset -c sleep 0", script)
        self.assertIn("open -a BlueBubbles", script)
        self.assertIn("Darwin", script)
        import shutil
        import subprocess
        sh = shutil.which("sh")
        result = subprocess.run([sh, "-n", str(ROOT / "scripts" / "mac-bluebubbles-setup.sh")])
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
