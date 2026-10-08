"""Read-only native metadata snapshots; host-owned watches are not execution grants."""
from datetime import datetime, timedelta, timezone
import json
import re

# Hermes kanban statuses (hermes_cli.kanban_workflow.DEFAULT_STATUSES; upstream
# 96db175da7 added scheduled and review to the tool surface).
KNOWN_STATUSES = frozenset({"triage", "todo", "scheduled", "ready", "running", "blocked",
                            "review", "done", "archived"})

def _eligible(task, now):
    if (type(task) is not dict or task.get("approved") is not True
            or type(task.get("status")) is not str
            or task["status"] not in {"active", "waiting", "blocked"}
            or type(task.get("id")) is not str
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", task["id"]) is None):
        return False
    native_id = task.get("native_task_id")
    if (type(native_id) is not str or not 1 <= len(native_id) <= 1000
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in native_id)):
        return False
    if "native_board" in task:
        board = task["native_board"]
        if (type(board) is not str
                or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", board) is None):
            return False
    approved_at = task.get("approved_at")
    if type(approved_at) is not str or len(approved_at) > 64:
        return False
    try:
        approved = datetime.fromisoformat(approved_at)
        return (approved.tzinfo is not None and approved.utcoffset() is not None
                and timedelta(0) <= now - approved <= timedelta(days=30))
    except (TypeError, ValueError, OverflowError):
        return False


def _rejected(value):
    return ("error" in value
            or ("success" in value and value["success"] is not True)
            or value.get("isError", False) is not False
            or value.get("denied", False) is not False
            or value.get("available", True) is not True)


def _updated_at(value):
    if (type(value) is not str or not 1 <= len(value) <= 64
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return None
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous JSON object")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("nonstandard JSON constant")


def _snapshot(document, native_id):
    if type(document) is str:
        document = json.loads(document, object_pairs_hook=_unique_object,
                              parse_constant=_invalid_constant)
    if type(document) is not dict or _rejected(document):
        return None
    card = document.get("task")
    if (type(card) is not dict or _rejected(card)
            or type(card.get("id")) is not str or card["id"] != native_id
            or type(card.get("status")) is not str):
        return None
    # A status this plugin does not know (a newer Hermes workflow column) is kept
    # as "unknown" instead of dropping the card; callers treat it fail-closed.
    status = card["status"] if card["status"] in KNOWN_STATUSES else "unknown"
    return {"status": status, "updated_at": _updated_at(card.get("updated_at"))}


def collect(ctx, tasks: list) -> dict:
    if type(tasks) is not list:
        return {}
    snapshots = {}
    now = datetime.now(timezone.utc)
    calls = 0
    for task in tasks:
        if calls == 4:
            break
        if not _eligible(task, now):
            continue
        calls += 1
        args = {"task_id": task["native_task_id"]}
        if "native_board" in task:
            args["board"] = task["native_board"]
        try:
            document = ctx.dispatch_tool("kanban_show", args)
            snapshot = _snapshot(document, task["native_task_id"])
        except Exception:
            continue
        if snapshot is not None:
            snapshots[task["id"]] = snapshot
    return snapshots
