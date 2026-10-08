"""Fixed own-profile context sources, hashed locally and bounded for review."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import stat

SOURCES = ("memories/MEMORY.md", "memories/USER.md", "cron/jobs.json")
DOCUMENTS = ("SOUL.md", "AGENTS.md", "IDENTITY.md")
MAC_STATE_FILE = "/var/lib/hermes-alans-way/mac-state.json"
WATCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def mac_state():
    try:
        path = Path(os.environ.get("HERMES_MAC_STATE_FILE") or MAC_STATE_FILE)
        if path.is_symlink() or path.stat().st_size > 4096:
            return None
        doc = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("state") not in ("online", "offline"):
        return None
    def field(key):
        value = doc.get(key)
        return value[:64] if isinstance(value, str) else None
    return {"state": doc["state"], "since": field("since"),
            "lastSeenOnline": field("lastSeenOnline")}


def read_source(home: Path, relative: str):
    if relative not in SOURCES + DOCUMENTS:
        raise ValueError("unsupported observation source")
    path = home / relative
    try:
        if path.parent.is_symlink() or path.is_symlink():
            return None
        path.resolve(strict=True).relative_to(home.resolve(strict=True))
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 65536 or before.st_nlink != 1:
                return None
            body = os.read(fd, 65537)
            after = os.fstat(fd)
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                return None
        finally:
            os.close(fd)
        text = body.decode("utf-8")
        if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
            return None
        return {"digest": sha256(body).hexdigest(), "text": text,
                "modified": datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat()}
    except (OSError, UnicodeError, ValueError):
        return None


def capabilities(home: Path):
    try:
        root = home / "skills"
        if root.is_symlink() or not root.is_dir():
            return None
        names = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if len(names) >= 48:
                break
            if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name == entry.name.encode("ascii", "ignore").decode() and \
                    entry.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
                names.append(entry.name[:64])
        return names
    except OSError:
        return None


def collect(home: Path, ledger, ctx=None):
    memory, schedule, documents, signatures = [], [], [], {}
    for source in SOURCES + DOCUMENTS:
        value = read_source(home, source)
        if value is None:
            continue
        signatures[source] = value["digest"]
        if source.startswith("memories/"):
            memory.append({"source": Path(source).name, "text": value["text"][:3072]})
        elif source in DOCUMENTS:
            documents.append({"source": source, "text": value["text"][:3072],
                              "modified": value["modified"]})
        else:
            try:
                document = json.loads(value["text"])
                jobs = document.get("jobs", []) if isinstance(document, dict) else document
                if isinstance(jobs, list):
                    for job in jobs[:12]:
                        if not isinstance(job, dict):
                            continue
                        row = {}
                        for key in ("name", "enabled", "schedule", "next_run", "status"):
                            v = job.get(key)
                            if isinstance(v, (str, bool, int, float)):
                                row[key] = v[:160] if isinstance(v, str) else v
                        if row:
                            schedule.append(row)
            except (ValueError, TypeError):
                pass
    snapshot = ledger.snapshot()
    from .proactive_native import collect as collect_native
    native = collect_native(ctx, snapshot["tasks"]) if ctx is not None else {}
    for watch_id, value in native.items():
        signatures["task:" + watch_id] = sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    mac = mac_state()
    if mac is not None:
        # since identifies the transition, not just the direction, so a repeat
        # offline->online flip is a new event rather than a deduped replay.
        signatures["mac"] = mac["since"] or mac["state"]
    from .proactive_board import collect_board, board_signature
    board = collect_board(home)
    if board:
        signatures["board"] = board_signature(board)
    skills = capabilities(home)
    if skills is not None:
        signatures["skills"] = sha256("\n".join(skills).encode()).hexdigest()
    tasks = []
    now = datetime.now(timezone.utc)
    for task in snapshot["tasks"]:
        try:
            approved_at = datetime.fromisoformat(task["approved_at"])
            if (task.get("approved") is not True or task.get("status") in {"done", "cancelled"}
                    or approved_at.tzinfo is None or (now - approved_at).total_seconds() > 2592000):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        allowed = {"id", "title", "scope", "next_action", "status", "owner", "approved",
                   "native_task_id", "next_review_at", "due_at", "notify_when",
                   "cadence_seconds", "signal", "signal_at", "execution_host"}
        tasks.append({k: (v[:300] if isinstance(v, str) and k not in {"id", "native_task_id"} else v)
                      for k, v in task.items() if k in allowed})
        if task.get("native_task_id"):
            current = native.get(task["id"])
            # Missing, blocked, or a status this plugin does not know: fail closed.
            if current is None or current["status"] in {"blocked", "unknown"}:
                tasks[-1]["status"] = "blocked"
            elif current["status"] in {"done", "archived"}:
                tasks[-1]["status"] = "done"
        if len(tasks) == 4:
            break
    preferences = dict(snapshot["preferences"])
    for key in ("focus", "ignore"):
        preferences[key] = [value[:100] for value in preferences[key][:8]]
    preferences["reviewed_at_utc"] = now.isoformat()
    preferences["native_task_status"] = native
    preferences["workspace_mac"] = mac
    by_id = {task["id"]: task for task in snapshot["tasks"]}
    goals = [{"source": "approved-watch", "summary": task["scope"], "watch_id": task["id"],
              "approved_at": by_id[task["id"]]["approved_at"], "status": task["status"]}
             for task in tasks]
    return {"memory": memory, "schedule": schedule, "tasks": tasks,
            "preferences": preferences, "goals": goals, "board": board,
            "documents": documents, "capabilities": skills or []}, signatures


def _parse_time(value):
    """Aware ISO timestamp to epoch seconds, or None."""
    if type(value) is not str:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment.timestamp()


def _review_tag(lag):
    """An unhandled due review resurfaces twice more across a week, then rests.

    Re-fire windows double as self-healing retries: a wake that expired
    undispatched gets a fresh event instead of silently muting the watch.
    """
    if lag < 86400:
        return ""
    if lag < 3 * 86400:
        return "r1"
    if lag < 7 * 86400:
        return "r2"
    return None


def _deadline_tag(delta):
    """Escalating windows as a watch's due_at approaches, plus overdue nudges."""
    if delta > 7 * 86400:
        return None
    if delta > 3 * 86400:
        return "w"
    if delta > 86400:
        return "d3"
    if delta > 4 * 3600:
        return "d1"
    if delta > 0:
        return "h4"
    if delta > -86400:
        return "over"
    return "gone"


def _due_instance(nra_ts, cadence, now_ts):
    """The schedule instance a watch is currently owed, or None.

    A cadence watch owes its latest missed grid slot, so a week of downtime
    collapses to one wake instead of replaying every slot — and instead of
    never firing because the oldest missed slot fell outside retry buckets.
    Re-arming happens at dispatch; the slot name stays stable meanwhile.
    """
    if nra_ts is None:
        return None
    if type(cadence) is int and 300 <= cadence <= 604800 and nra_ts <= now_ts:
        return nra_ts + int((now_ts - nra_ts) // cadence) * cadence
    return nra_ts


def _watch_eligible(task):
    return (type(task) is dict
            and task.get("approved") is True
            and task.get("status") == "active"
            and type(task.get("id")) is str
            and WATCH_ID.fullmatch(task["id"]) is not None)


def _admit_due(runtime, tasks, now, active, *, limit=4):
    """Admit scheduled watch wakes; returns how many events were queued.

    Evidence names the specific fired instance, so each cadence tick and each
    escalation bucket dedupes independently while a pending wake for the same
    watch suppresses pile-up. Bounded work per tick keeps observation cheap.
    """
    admitted = 0
    now_ts = now.timestamp()
    for task in tasks.values():
        if not _watch_eligible(task):
            continue
        # Re-arming happens at dispatch, not admission: the evidence epoch
        # must still match next_review_at when the wake fires, or the
        # stale-instance check would retire every cadence watch it queued.
        prefix = f"watchdue:{task['id']}:"
        tags = []
        due_ts = _parse_time(task.get("due_at"))
        if due_ts is not None:
            tag = _deadline_tag(due_ts - now_ts)
            if tag is not None:
                tags.append(f"d{int(due_ts)}:{tag}")
        instance = _due_instance(_parse_time(task.get("next_review_at")),
                                 task.get("cadence_seconds"), now_ts)
        if instance is not None and instance <= now_ts:
            tag = _review_tag(now_ts - instance)
            if tag is not None:
                tags.append(f"r{int(instance)}:{tag}")
        if not tags or active or admitted >= limit:
            continue
        if not runtime.store.pending_matching(prefix):
            admitted += int(runtime.store.record_event("watch_due", prefix + tags[0], purpose=True))
    return admitted


def observe(runtime):
    context, signatures = collect(runtime.home, runtime.ledger, runtime.ctx)
    purposeful = bool(context["tasks"])
    counts = runtime.store.status()["counts"]
    active = any(counts.get(name, 0) for name in ("dispatching", "accepted_unverified", "uncertain"))
    admitted = 0
    now = datetime.now(timezone.utc)
    with runtime.ledger.transaction() as state:
        previous = state["observations"]
        for source, digest in signatures.items():
            old = previous.get(source)
            if old is not None and old != digest and not active:
                evidence = sha256((source + ":" + digest).encode()).hexdigest()
                kind = "task_changed" if source.startswith("task:") else "context_changed"
                admitted += int(runtime.store.record_event(kind, evidence, purpose=purposeful))
            previous[source] = digest
        # A reported signal change on an approved watch is an opted-in event:
        # the collector writes state, the observer diffs it, a change wakes.
        for task in state["tasks"].values():
            if not _watch_eligible(task):
                continue
            key = "watch:" + task["id"]
            digest = (sha256(json.dumps([task.get("signal"), task.get("signal_at")],
                                        separators=(",", ":")).encode()).hexdigest()
                      if type(task.get("signal")) is str else None)
            old = previous.get(key)
            if digest is not None and old is not None and old != digest and not active:
                evidence = sha256((key + ":" + digest).encode()).hexdigest()
                admitted += int(runtime.store.record_event("task_changed", evidence, purpose=True))
            previous[key] = digest
        admitted += _admit_due(runtime, state["tasks"], now, active)
        previous["__heartbeat"] = now.isoformat()
    return admitted
