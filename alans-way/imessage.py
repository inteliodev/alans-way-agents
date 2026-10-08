"""Drafts-only iMessage through a BlueBubbles server on the tailnet.

This is not Hermes' bluebubbles gateway platform. That adapter would make the
Mac's Apple ID the bot and answer incoming texts. These tools call the REST
API directly: reads and drafts are free, and the only send posts one stored
draft after Hermes' native approval gate says yes. A timeout, a deny, yolo,
or approvals turned off sends nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import fcntl
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .gateway_guard import write_private_json

log = logging.getLogger("alans-way.imessage")

# Tailscale IPv4 CGNAT, prefix 10, built without a dotted device address.
TAILNET_V4 = ipaddress.ip_network((0x64400000, 10))
ENV_KEYS = ("BLUEBUBBLES_URL", "BLUEBUBBLES_PASSWORD")
DRAFT_ID = re.compile(r"^d-[0-9a-f]{16}$")
MAX_TEXT = 4000
MAX_CHAT = 500


class IMessageError(Exception):
    """A failure safe to show the model. It never carries the server password."""


@dataclass(frozen=True)
class SendLimits:
    min_interval_seconds: int = 30
    per_hour: int = 5
    per_day: int = 20


def approval_reason(draft: dict) -> str:
    """Text Hermes puts on the Approve/Deny card. The stored body, unchanged."""
    return (
        "Send this stored iMessage draft exactly as written. "
        "Silence denies the send.\n"
        f"To: {draft['chat_label']}\n"
        f"Chat: {draft['chat_guid']}\n"
        f"Draft: {draft['id']}\n"
        f"Text:\n{draft['text']}"
    )


def hermes_approve(draft: dict) -> dict:
    """Ask tools.approval.request_tool_approval. Fail closed on every other path.

    The action gate's human grant and its yolo/off bypass both return
    ``approved: True``. Yolo and ``approvals.mode: off`` are refused here,
    before the gate is asked, so they cannot send.
    """
    try:
        from tools.approval import is_current_session_yolo_enabled, request_tool_approval
        from tools.approval_context import _get_approval_mode
    except Exception:
        return {"approved": False, "outcome": "unavailable"}
    try:
        yolo = bool(is_current_session_yolo_enabled())
        mode = _get_approval_mode()
    except Exception:
        return {"approved": False, "outcome": "unavailable"}
    if yolo or mode == "off":
        log.info("imessage send refused: approval bypass draft=%s", draft.get("id"))
        return {"approved": False, "outcome": "bypass_refused"}
    try:
        result = request_tool_approval(
            "imessage_send_draft",
            approval_reason(draft),
            rule_key="imessage-draft:" + str(draft.get("id") or ""),
        )
    except Exception:
        log.info("imessage send refused: approval gate failed draft=%s", draft.get("id"))
        return {"approved": False, "outcome": "unavailable"}
    if isinstance(result, dict) and result.get("approved") is True:
        return {"approved": True, "outcome": "approved"}
    outcome = "denied"
    if isinstance(result, dict) and result.get("outcome"):
        outcome = str(result["outcome"])
    log.info("imessage send refused: %s draft=%s", outcome, draft.get("id"))
    return {"approved": False, "outcome": outcome}


def allowed_base_url(raw: str) -> str:
    """Accept only an http(s) origin on loopback or the Tailscale IPv4 CGNAT range."""
    value = (raw or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IMessageError("refusing BlueBubbles URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IMessageError("refusing BlueBubbles URL")
    if parsed.path not in {"", "/"}:
        raise IMessageError("refusing BlueBubbles URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        raise IMessageError("refusing BlueBubbles URL") from None
    if address.is_unspecified or not (address.is_loopback or address in TAILNET_V4):
        raise IMessageError("refusing BlueBubbles URL")
    host = parsed.hostname
    if address.version == 6:
        host = f"[{host}]"
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}"


def _read_profile_env(home: Path) -> dict:
    path = home / ".env"
    found = {}
    try:
        if not path.is_file():
            return found
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in ENV_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        found[key] = value
    return found


def load_config(home: Path) -> tuple[str, str]:
    """Profile .env, overridden by the process environment. Never logged."""
    file_values = _read_profile_env(home)
    url = os.environ.get("BLUEBUBBLES_URL", file_values.get("BLUEBUBBLES_URL", ""))
    password = os.environ.get("BLUEBUBBLES_PASSWORD", file_values.get("BLUEBUBBLES_PASSWORD", ""))
    return url, password


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IMessageError("BlueBubbles redirect refused")


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _label(chat: dict) -> str:
    name = str(chat.get("displayName") or "").strip()
    if name:
        return name
    addresses = []
    for participant in chat.get("participants") or []:
        if isinstance(participant, dict) and participant.get("address"):
            addresses.append(str(participant["address"]))
    if addresses:
        return ", ".join(addresses)
    return str(chat.get("chatIdentifier") or chat.get("guid") or "chat")


def _last_message(chat: dict) -> dict:
    last = chat.get("lastMessage") or {}
    if isinstance(last, list):
        last = last[-1] if last else {}
    return last if isinstance(last, dict) else {}


def _unread(chat: dict) -> bool:
    if "unreadCount" in chat:
        try:
            return int(chat["unreadCount"]) > 0
        except (TypeError, ValueError):
            return False
    last = _last_message(chat)
    if not last:
        return False
    return last.get("isFromMe") is False and not last.get("dateRead")


def _chat_summary(chat: dict) -> dict:
    last = _last_message(chat)
    return {
        "guid": chat.get("guid") or "",
        "display_name": _label(chat),
        "chat_identifier": chat.get("chatIdentifier") or "",
        "last_message": last.get("text") or "",
        "last_from_me": last.get("isFromMe") is True,
        "unread": _unread(chat),
    }


def _message_summary(message: dict) -> dict:
    handle = message.get("handle") if isinstance(message.get("handle"), dict) else {}
    return {
        "text": message.get("text") or "",
        "from_me": message.get("isFromMe") is True,
        "handle": handle.get("address") or "",
        "date": message.get("dateCreated"),
    }


class IMessage:
    def __init__(self, home: Path, *, url: str | None = None, password: str | None = None,
                 approve=None, now=None, limits: SendLimits | None = None):
        self.home = Path(home)
        self._url = url
        self._password = password
        self.approve = approve or hermes_approve
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.limits = limits or SendLimits()

    def list_chats(self, limit: int = 15) -> dict:
        limit = _bound(limit, 1, 50, 15)
        payload = self._request("POST", "/api/v1/chat/query", {
            "limit": limit, "offset": 0, "with": ["lastMessage", "participants"],
        })
        chats = [_chat_summary(chat) for chat in _data_list(payload)]
        return {"ok": True, "chats": chats}

    def read_chat(self, chat: str, limit: int = 20) -> dict:
        limit = _bound(limit, 1, 100, 20)
        found = self._resolve(chat)
        if "error" in found:
            return {"ok": False, "error": found["error"], "matches": found.get("matches", [])}
        guid = found["guid"]
        encoded = urllib.parse.quote(guid, safe="")
        payload = self._request(
            "GET",
            f"/api/v1/chat/{encoded}/message?limit={limit}&sort=DESC&with=handle",
        )
        messages = list(reversed([_message_summary(item) for item in _data_list(payload)]))
        return {"ok": True, "chat": found, "messages": messages}

    def search(self, query: str, limit: int = 25) -> dict:
        query = _text_arg(query, MAX_CHAT, "query")
        limit = _bound(limit, 1, 50, 25)
        payload = self._request("POST", "/api/v1/message/query", {
            "limit": limit,
            "offset": 0,
            "sort": "DESC",
            "with": ["handle", "chats"],
            "where": [{
                "statement": "message.text LIKE :q ESCAPE '\\'",
                "args": {"q": _like(query)},
            }],
        })
        messages = [_message_summary(item) for item in _data_list(payload)]
        needle = query.casefold()
        contacts = []
        for chat in self._fetch_chats(100):
            summary = _chat_summary(chat)
            haystack = " ".join([
                summary["display_name"], summary["chat_identifier"], summary["guid"],
            ]).casefold()
            if needle in haystack:
                contacts.append({
                    "guid": summary["guid"],
                    "display_name": summary["display_name"],
                    "chat_identifier": summary["chat_identifier"],
                })
        return {"ok": True, "messages": messages, "contacts": contacts[:limit]}

    def draft_reply(self, chat: str, text: str) -> dict:
        body = _message_text(text)
        found = self._resolve(chat)
        if "error" in found:
            return {"ok": False, "error": found["error"], "matches": found.get("matches", [])}
        draft = {
            "id": "d-" + secrets.token_hex(8),
            "created": self.now().isoformat(),
            "status": "pending",
            "chat_guid": found["guid"],
            "chat_label": found["display_name"],
            "text": body,
        }
        self._write_draft(draft)
        log.info("imessage draft stored id=%s chat=%s", draft["id"], draft["chat_guid"])
        return {"ok": True, "draft": draft}

    def send_draft(self, draft_id: str) -> dict:
        if not DRAFT_ID.match(draft_id or ""):
            return {"ok": False, "error": "unknown draft", "sent": False}
        draft = self._read_draft(draft_id)
        if draft is None or draft.get("status") != "pending":
            return {"ok": False, "error": "draft is not pending", "sent": False}
        if self._rate_limited(self._sent_times()):
            self._record(draft, "rate_limited")
            return {"ok": False, "error": "send rate limit reached", "sent": False}
        decision = self.approve(draft)
        if not isinstance(decision, dict) or decision.get("approved") is not True:
            outcome = decision.get("outcome") if isinstance(decision, dict) else "denied"
            self._record(draft, "denied", approval=str(outcome or "denied"))
            return {"ok": False, "error": "not approved; nothing sent", "sent": False, "outcome": outcome}
        with self._exclusive():
            current = self._read_draft(draft_id)
            if current is None or current.get("status") != "pending" or current.get("text") != draft["text"]:
                self._log_send(draft, "aborted")
                return {"ok": False, "error": "draft changed before send; nothing sent", "sent": False}
            if self._rate_limited(self._sent_times()):
                self._log_send(current, "rate_limited")
                return {"ok": False, "error": "send rate limit reached", "sent": False}
            try:
                self._request("POST", "/api/v1/message/text", {
                    "chatGuid": current["chat_guid"],
                    "tempGuid": "temp-" + uuid.uuid4().hex,
                    "message": current["text"],
                })
            except IMessageError:
                self._log_send(current, "send_failed")
                return {"ok": False, "error": "BlueBubbles send failed; draft still pending", "sent": False}
            current["status"] = "sent"
            current["sent_at"] = self.now().isoformat()
            self._write_draft(current)
            self._log_send(current, "sent")
        return {
            "ok": True, "sent": True, "draft_id": current["id"],
            "chat_label": current["chat_label"], "chat_guid": current["chat_guid"],
            "text": current["text"],
        }

    def _resolve(self, chat: str) -> dict:
        query = _text_arg(chat, MAX_CHAT, "chat")
        chats = self._fetch_chats(200)
        summaries = [_chat_summary(item) for item in chats]
        if ";" in query:
            matches = [item for item in summaries if item["guid"] == query]
        else:
            needle = query.casefold()
            matches = []
            for item in summaries:
                names = {item["display_name"].casefold(), item["chat_identifier"].casefold(), item["guid"].casefold()}
                if needle in names:
                    matches.append(item)
        if len(matches) == 1:
            return matches[0]
        if not matches:
            return {"error": "no matching chat"}
        return {"error": "chat is ambiguous", "matches": matches[:10]}

    def _fetch_chats(self, limit: int) -> list:
        payload = self._request("POST", "/api/v1/chat/query", {
            "limit": limit, "offset": 0, "with": ["lastMessage", "participants"],
        })
        return _data_list(payload)

    def _credentials(self) -> tuple[str, str]:
        if self._url is None or self._password is None:
            url, password = load_config(self.home)
        else:
            url, password = self._url, self._password
        base = allowed_base_url(url)
        if not password:
            raise IMessageError("BlueBubbles password is not configured")
        return base, password

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        base, password = self._credentials()
        separator = "&" if "?" in path else "?"
        url = base + path + separator + urllib.parse.urlencode({"password": password})
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read(1_000_000)
                status = getattr(response, "status", 200)
        except IMessageError:
            raise
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(8000)
            except Exception:
                raw = b""
        except Exception:
            log.info("imessage request failed method=%s path=%s", method, path.split("?", 1)[0])
            raise IMessageError("BlueBubbles request failed") from None
        if status >= 400:
            log.info("imessage request rejected method=%s path=%s status=%s", method, path.split("?", 1)[0], status)
            raise IMessageError("BlueBubbles rejected the request")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise IMessageError("BlueBubbles returned a non-JSON response") from None
        if isinstance(body, dict) and isinstance(body.get("status"), int) and body["status"] >= 400:
            raise IMessageError("BlueBubbles rejected the request")
        if not isinstance(body, dict):
            raise IMessageError("BlueBubbles returned an unexpected response")
        return body

    def _draft_path(self, draft_id: str) -> Path:
        if not DRAFT_ID.match(draft_id):
            raise IMessageError("unknown draft")
        return self.home / "imessage" / "drafts" / f"{draft_id}.json"

    def _write_draft(self, draft: dict) -> None:
        write_private_json(self._draft_path(draft["id"]), draft)

    def _read_draft(self, draft_id: str) -> dict | None:
        path = self._draft_path(draft_id)
        try:
            if path.is_symlink() or not path.is_file():
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _log_path(self) -> Path:
        return self.home / "imessage" / "sends.jsonl"

    def _record(self, draft: dict, outcome: str, **extra) -> None:
        with self._exclusive():
            self._log_send(draft, outcome, **extra)

    def _log_send(self, draft: dict, outcome: str, **extra) -> None:
        text = str(draft.get("text") or "")
        record = {
            "at": self.now().isoformat(),
            "id": draft.get("id"),
            "chat_guid": draft.get("chat_guid"),
            "chat_label": draft.get("chat_label"),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text": text,
            "outcome": outcome,
        }
        record.update(extra)
        path = self._log_path()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        log.info("imessage send outcome=%s id=%s", outcome, draft.get("id"))

    def _sent_times(self) -> list[datetime]:
        path = self._log_path()
        times = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return times
        for line in lines:
            try:
                record = json.loads(line)
                stamped = datetime.fromisoformat(record["at"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            if record.get("outcome") == "sent" and stamped.tzinfo is not None:
                times.append(stamped)
        return times

    def _rate_limited(self, times: list[datetime]) -> bool:
        if not times:
            return False
        moment = self.now()
        latest = max(times)
        if moment - latest < timedelta(seconds=self.limits.min_interval_seconds):
            return True
        hour = moment - timedelta(hours=1)
        day = moment - timedelta(days=1)
        return (sum(item >= hour for item in times) >= self.limits.per_hour
                or sum(item >= day for item in times) >= self.limits.per_day)

    def _exclusive(self):
        return _FileLock(self.home / "imessage" / ".lock")


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.handle = open(self.path, "a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self.handle

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _bound(value, low: int, high: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(high, max(low, value))


def _text_arg(value, limit: int, name: str) -> str:
    if not isinstance(value, str):
        raise IMessageError(f"{name} is required")
    text = value.strip()
    if not text or len(text) > limit:
        raise IMessageError(f"{name} is required")
    return text


def _message_text(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise IMessageError("text is required")
    return value


def _data_list(payload: dict) -> list:
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _tool_result(func):
    def handler(args, **kwargs):
        if not isinstance(args, dict):
            return json.dumps({"ok": False, "error": "invalid arguments"})
        try:
            return json.dumps(func(args))
        except IMessageError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
    return handler


SCHEMAS = (
    {
        "name": "imessage_list_chats",
        "description": "List Hayden's recent iMessage chats with display names, last message, and unread. Read-only. Never sends.",
        "parameters": {
            "type": "object", "additionalProperties": False,
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    },
    {
        "name": "imessage_read_chat",
        "description": "Read the last N messages of one existing chat, by chat guid or one exact contact name or handle. Read-only. Never sends.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["chat"],
            "properties": {
                "chat": {"type": "string", "minLength": 1, "maxLength": MAX_CHAT},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "imessage_search",
        "description": "Search Hayden's iMessage messages and contacts. Read-only. Never sends.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["query"],
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": MAX_CHAT},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "imessage_draft_reply",
        "description": "Store a new pending iMessage draft for one existing chat. Does not send. A revision is a new draft; stored drafts are never edited.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["chat", "text"],
            "properties": {
                "chat": {"type": "string", "minLength": 1, "maxLength": MAX_CHAT},
                "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT},
            },
        },
    },
    {
        "name": "imessage_send_draft",
        "description": "Ask Hayden to approve sending one stored draft by id, exactly as stored. No free-form send. Timeout or denial sends nothing.",
        "parameters": {
            "type": "object", "additionalProperties": False, "required": ["draft_id"],
            "properties": {"draft_id": {"type": "string", "minLength": 1, "maxLength": 80}},
        },
    },
)


def register_imessage(ctx, home: Path) -> IMessage:
    service = IMessage(home)

    @_tool_result
    def list_chats(args):
        return service.list_chats(args.get("limit", 15))

    @_tool_result
    def read_chat(args):
        return service.read_chat(args.get("chat", ""), args.get("limit", 20))

    @_tool_result
    def search(args):
        return service.search(args.get("query", ""), args.get("limit", 25))

    @_tool_result
    def draft_reply(args):
        return service.draft_reply(args.get("chat", ""), args.get("text", ""))

    @_tool_result
    def send_draft(args):
        return service.send_draft(str(args.get("draft_id", "")))

    handlers = {
        "imessage_list_chats": list_chats,
        "imessage_read_chat": read_chat,
        "imessage_search": search,
        "imessage_draft_reply": draft_reply,
        "imessage_send_draft": send_draft,
    }
    for schema in SCHEMAS:
        ctx.register_tool(
            name=schema["name"], toolset="imessage", schema=schema,
            handler=handlers[schema["name"]], check_fn=lambda: True,
        )
    ctx.register_skill("imessage-drafts", Path(__file__).parent / "skills" / "imessage-drafts" / "SKILL.md")
    return service
