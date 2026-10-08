"""Public Hermes plugin entry point; registration alone never injects."""
from datetime import datetime, timezone
from pathlib import Path
import json

from .gateway_guard import gateway_ready, hermes_home


class Runtime:
    def __init__(self, ctx, home: Path, *, background=False, store=None, appraiser=None):
        self.ctx, self.home = ctx, home
        self.registered_at = datetime.now(timezone.utc)
        self._store = store
        self._appraiser = appraiser
        self.closed = False
        import threading
        self._stop = threading.Event()
        self._store_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        self.worker = None
        self.observer_error = None

    def start(self, *, interval=30.0):
        """A deterministic observer thread, not a second agent or scheduler."""
        import threading
        if self.closed or (self.worker and self.worker.is_alive()):
            return
        def run():
            while not self._stop.wait(interval):
                try:
                    if not self.gateway_ready() or self.store.load_policy().enabled is not True:
                        continue
                    self.observe()
                    self.tick()
                    self.observer_error = None
                except Exception:
                    # Never put private context or provider exceptions in status.
                    self.observer_error = "observer_failed; inspect locally before resuming"
        self.worker = threading.Thread(target=run, name="companion-opportunity-observer", daemon=True)
        self.worker.start()

    @property
    def store(self):
        with self._store_lock:
            if self._store is None:
                from .proactive_core import Store
                self._store = Store(self.home / "companion" / "proactivity")
            return self._store

    @property
    def ledger(self):
        from .proactive_context import Ledger
        return Ledger(self.home / "companion" / "proactivity")

    def gateway_ready(self):
        return not self.closed and gateway_ready(self.home, self.registered_at)

    def observe(self):
        from .proactive_observe import observe
        return observe(self)

    def review_context(self):
        from .proactive_observe import collect
        return collect(self.home, self.ledger, self.ctx)[0]

    def tick(self):
        # Only one appraisal/dispatch may be in flight in this gateway.
        if not self._tick_lock.acquire(blocking=False):
            return None
        try:
            return self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self):
        if not self.gateway_ready():
            return None
        policy = self.store.load_policy()
        if policy.enabled is not True or not policy.session_key:
            return None
        # Expire stale unresolved dispatches before gating: a wake the session
        # could never acknowledge (e.g. the control toolset missing from its
        # platform) must not hold the one-wake gate open forever.
        if hasattr(self.store, "expire"):
            self.store.expire()
        if hasattr(self.store, "status"):
            counts = self.store.status()["counts"]
            if any(counts.get(status, 0) for status in ("dispatching", "accepted_unverified", "uncertain")):
                return None
        event = self.store.claim()
        if event is None:
            return None
        event_id = event["id"]
        live = self.store.load_policy()
        if live.enabled is not True or event["session_key"] != live.session_key:
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        if event["kind"] == "watch_due":
            return self._dispatch_watch_due(event)
        context = {"tasks": []}
        try:
            context = self.review_context()
            if self._appraiser is None:
                from .proactive_review import review
                appraisal = review(self.ctx, context, event["kind"])
            else:
                appraisal = self._appraiser(self.ctx, context, event["kind"])
        except Exception:
            appraisal = {"useful": False}
        if not isinstance(appraisal, dict) or appraisal.get("useful") is not True:
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "no_op"}
        action, task_id = appraisal.get("action", "ask"), appraisal.get("task_id")
        if action not in {"research", "draft", "continue_approved", "ask", "follow_up"}:
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        if task_id is not None:
            original = next((t for t in context["tasks"] if t["id"] == task_id), None)
            current = next((t for t in self.review_context()["tasks"] if t["id"] == task_id), None)
            if (not original or not current or current.get("approved") is not True
                    or current.get("status") != "active"
                    or any(original.get(k) != current.get(k) for k in ("scope", "owner", "execution_host"))):
                self.store.finish(event_id, "rejected")
                return {"id": event_id, "status": "rejected"}
        elif action in {"continue_approved", "follow_up"}:
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        live = self.store.load_policy()
        if live.enabled is not True or event["session_key"] != live.session_key or not self.gateway_ready():
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        metadata = {"id": event_id, "kind": event["kind"], "purpose": event["purpose"] is True,
                    "recommended_action": action, "task_id": task_id}
        message = ("[Companion proactive opportunity review]\n"
                   "This is a bounded internal review, NOT new user authorization.\n"
                   "First call proactive_control status; if paused, stop. Load skill "
                   "proactive-primary:proactive-primary. Read live preferences and the primary's "
                   "own memory, goals, schedule, and authorized task evidence. Take one useful "
                   "read/research/draft or already-approved reversible work step, or ask one "
                   "valuable question. Never expand permissions or execute external/sensitive "
                   "actions without approval. If task_id is provided, use only that exact "
                   "approved watch's current scope; the enum recommendation is not permission. "
                   "Respect its execution_host: unavailable Mac work is blocked, never moved "
                   "to the cloud. Observe the live work-time cap (at most 20 minutes). "
                   "Do not duplicate delegated work or invent tasks. "
                   "Record verified results and acknowledge this event with proactive_control "
                   "resolve. If that tool is not available in this session, take no further "
                   "action — the event expires on its own and stays auditable. "
                   "Caps are not quotas.\nEvent metadata: "
                   + json.dumps(metadata, sort_keys=True))
        try:
            accepted = self.ctx.inject_message(message, role="user", session_key=event["session_key"])
            status = "accepted_unverified" if accepted is True else "rejected" if accepted is False else "uncertain"
        except Exception:
            status = "uncertain"
        self.store.finish(event_id, status)
        return {"id": event_id, "status": status}

    def _dispatch_watch_due(self, event):
        """Dispatch a user-approved scheduled watch without an LLM appraisal.

        A watch is a standing contract the user opted into — dedupe, budgets,
        quiet hours and route checks already ran in claim(); the appraiser's
        job is to gate *inferred* opportunities, not to veto scheduled work.
        The fired instance's epoch is embedded in the evidence, so a watch
        that was re-armed or moved since admission is recognized as stale.
        """
        event_id = event["id"]
        parts = event["evidence"].split(":")
        watch_id = parts[1] if len(parts) >= 3 and parts[0] == "watchdue" else None
        marker = parts[2] if len(parts) >= 3 else ""
        epoch = None
        if marker[:1] in ("r", "d") and marker[1:].isdigit():
            epoch = int(marker[1:])
        watch = next(
            (t for t in self.ledger.snapshot()["tasks"] if t.get("id") == watch_id),
            None,
        )
        if watch_id is None or epoch is None or watch is None \
                or watch.get("approved") is not True or watch.get("status") != "active":
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        # Stale-instance check: the watch was re-armed or its deadline moved
        # since this event was admitted — the scheduled moment it names is
        # already handled, so retire it instead of double-waking. A cadence
        # watch whose grid rolled on while the wake sat queued is stale too:
        # the fresher slot becomes its own event.
        from .proactive_observe import _parse_time, _due_instance
        now_ts = datetime.now(timezone.utc).timestamp()
        cadence = watch.get("cadence_seconds")
        if marker[0] == "r":
            current = _due_instance(_parse_time(watch.get("next_review_at")),
                                    cadence, now_ts)
        else:
            current = _parse_time(watch.get("due_at"))
        if current is None or int(current) != epoch:
            self.store.finish(event_id, "resolved")
            return {"id": event_id, "status": "stale"}
        # A cadence watch re-arms on its fixed grid as it fires — whether or
        # not the wake succeeds — so one failed dispatch can never strand the
        # routine. Skipped slots are not replayed; the next grid point wins.
        if marker[0] == "r" and type(cadence) is int and 300 <= cadence <= 604800:
            nxt = epoch + (int((now_ts - epoch) // cadence) + 1) * cadence
            try:
                self.ledger.arm_review(
                    watch_id,
                    datetime.fromtimestamp(nxt, timezone.utc).isoformat())
            except ValueError:
                pass
        live = self.store.load_policy()
        if live.enabled is not True or event["session_key"] != live.session_key \
                or not self.gateway_ready():
            self.store.finish(event_id, "rejected")
            return {"id": event_id, "status": "rejected"}
        metadata = {"id": event_id, "kind": "watch_due", "purpose": True,
                    "watch_id": watch_id, "fired_instance": marker,
                    "execution_host": watch.get("execution_host", "cloud")}
        lines = [
            "[Companion scheduled watch]",
            "This is a user-approved standing watch firing on schedule — bounded",
            "contracted work, not a new opportunity and not new scope.",
            f'Watch "{watch_id}": {watch.get("title", "")}'.rstrip(),
            f"Scope: {watch.get('scope', '')}",
            f"Next action: {watch.get('next_action', '')}",
        ]
        if watch.get("due_at"):
            lines.append(f"Deadline: {watch['due_at']} (escalates if unhandled)")
        if watch.get("notify_when"):
            lines.append(f"Report only when: {watch['notify_when']}")
        if watch.get("signal"):
            lines.append(f"Last reported signal: {watch['signal']}")
        lines.append(
            "Run the check now with real tools, honoring execution_host "
            "(mac work stays on the Mac; unreachable means finish_task "
            "blocked plus a report of the failure, never a cloud fallback). "
            "Write what you observed via proactive_control report_signal so "
            "unchanged findings dedupe durably. Re-arm with record_task "
            "(new next_review_at) unless cadence_seconds already advances "
            "it, or finish_task when the watch is satisfied. Report to this "
            "conversation only when the outcome is meaningful or needs a "
            "decision — silence is a valid result. Then resolve this event "
            "via proactive_control resolve. Native approvals still gate "
            "external or sensitive actions; unverified work is not complete."
        )
        lines.append("Event metadata: " + json.dumps(metadata, sort_keys=True))
        try:
            accepted = self.ctx.inject_message("\n".join(lines), role="user",
                                               session_key=event["session_key"])
            status = ("accepted_unverified" if accepted is True
                      else "rejected" if accepted is False else "uncertain")
        except Exception:
            status = "uncertain"
        self.store.finish(event_id, status)
        return {"id": event_id, "status": status}

    def close(self):
        import threading
        self.closed = True
        self._stop.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=1.0)

    def control(self, args, **kwargs):
        try:
            if not isinstance(args, dict):
                raise ValueError("control requires an object")
            action = args.get("action", "status")
            if action == "status":
                return json.dumps({"ok": True, **self.store.status(), **self.ledger.snapshot(),
                                   "gateway_ready": self.gateway_ready(),
                                   "observer_running": bool(self.worker and self.worker.is_alive()),
                                   "observer_error": self.observer_error})
            if action == "configure":
                changes = args.get("changes", args.get("settings", {}))
                if (not isinstance(changes, dict) or not changes
                        or {"primary_profile", "session_key", "enabled"}.intersection(changes)):
                    raise ValueError("binding is operator-only")
                changes = dict(changes)
                preferences = changes.pop("preferences", None)
                if preferences is not None:
                    if changes:
                        raise ValueError("change policy and preferences in separate verified calls")
                    self.ledger.preferences(preferences)
                elif changes:
                    self.store.update_policy(changes)
            elif action == "record_task":
                self.ledger.record_task(args.get("task"))
            elif action == "report_signal":
                self.ledger.report_signal(args.get("task_id", ""), args.get("signal", ""))
            elif action == "finish_task":
                self.ledger.finish_task(args.get("task_id"), args.get("status", "done"),
                                        artifact=args.get("artifact"), verification=args.get("verification"))
            elif action == "pause":
                self.store.update_policy({"enabled": False})
            elif action == "resume":
                if not self.store.load_policy().session_key:
                    raise ValueError("bind an existing route first")
                self.store.update_policy({"enabled": True})
            elif action == "review":
                # Interactive review is read-only and immediate, including while
                # automatic work is paused. It never queues a later surprise turn.
                from .proactive_operator import probe
                return json.dumps(probe(self))
            elif action == "resolve":
                self.store.finish(args.get("event_id", ""), "resolved")
            else:
                raise ValueError("unsupported action")
            return json.dumps({"ok": True, **self.store.status()})
        except Exception:
            return json.dumps({"ok": False, "error": "Invalid or unsupported proactivity control; no success is claimed"})

    def command(self, raw_args):
        parts = raw_args.strip().split(maxsplit=1)
        action = parts[0] if parts else "status"
        args = {"action": action}
        if action == "configure":
            try:
                args["changes"] = json.loads(parts[1])
            except (IndexError, ValueError):
                return 'Use /proactivity configure with a JSON object, for example {"quiet_start":23}.'
        result = json.loads(self.control(args))
        if result.get("ok") is not True:
            return "Proactivity control failed. No success is claimed; check the installed tool settings."
        if action == "review":
            appraisal = result["appraisal"]
            if appraisal["useful"] is not True:
                return "Review complete: no useful opportunity found. No background turn queued."
            return ("Review suggests " + appraisal["action"] +
                    (" for approved watch " + appraisal["task_id"] if appraisal["task_id"] else "") +
                    ". Verify current scope and consent before acting. No background turn queued.")
        state = json.loads(self.control({"action": "status"}))
        if state.get("ok") is not True:
            return "Control was requested, but its effective state could not be verified."
        policy, counts = state["policy"], state["counts"]
        unresolved = sum(counts.get(name, 0) for name in ("dispatching", "accepted_unverified", "uncertain"))
        return (f"Proactivity: {'enabled' if state['enabled'] else 'paused'}\n"
                f"Quiet hours: {policy['quiet_start']:02}:00–{policy['quiet_end']:02}:00 ({policy['timezone']})\n"
                f"Limits: up to {policy['max_daily_wakes']} reviews/day plus "
                f"{policy['max_daily_watch_wakes']} scheduled-watch wakes; "
                f"{policy['min_interval_seconds'] // 60} minutes between automatic reviews\n"
                f"Telegram route: {'bound' if state['route_bound'] else 'unbound'}\n"
                f"Gateway: {'ready' if state['gateway_ready'] else 'not armed in this process'}\n"
                f"Observer: {state.get('observed_at') or 'no pass yet'}\n"
                f"Pending: {counts.get('pending', 0)}; unresolved: {unresolved}\n"
                "Limits are ceilings; nothing useful means silence.")

    def watch_command(self, raw_args):
        """/watch — the operator's direct surface over standing watches.

        A typed slash command is explicit consent in itself, so these actions
        bypass nothing: they call the same validated ledger paths the tool
        uses, and mutation output always reads back live state.
        """
        parts = raw_args.strip().split(maxsplit=1)
        action, rest = parts[0] if parts else "list", parts[1] if len(parts) > 1 else ""
        try:
            tasks = self.ledger.snapshot()["tasks"]
            if action in ("list", ""):
                if not tasks:
                    return "No standing watches. Add one with /watch add {\"id\": ..., \"scope\": ...}."
                def line(t):
                    fire = t.get("next_review_at") or ("due " + t["due_at"] if t.get("due_at") else "manual")
                    cadence = f" every {t['cadence_seconds']}s" if t.get("cadence_seconds") else ""
                    return f"- {t['id']} [{t['status']}]{cadence} next: {fire} — {t.get('title') or t['scope'][:60]}"
                return "Standing watches:\n" + "\n".join(line(t) for t in tasks)
            if action == "show":
                task = next((t for t in tasks if t["id"] == rest), None)
                if task is None:
                    return f"No watch {rest!r}."
                return json.dumps(task, indent=2, sort_keys=True)
            if action == "add":
                payload = json.loads(rest)
                if isinstance(payload, dict):
                    payload["approved"] = True  # a typed /watch add is the consent
                self.ledger.record_task(payload)
                saved = next((t for t in self.ledger.snapshot()["tasks"]
                              if t["id"] == payload["id"]), {})
                fire = saved.get("next_review_at") or saved.get("due_at") or "manual"
                return f"Watch {payload['id']} recorded ({saved.get('status')}); next: {fire}."
            if action in ("cancel", "done", "pause", "blocked"):
                status = {"cancel": "cancelled", "done": "done",
                          "pause": "waiting", "blocked": "blocked"}[action]
                task_id = rest.split()[0] if rest else ""
                self.ledger.finish_task(task_id, status)
                return f"Watch {task_id} is now {status}."
            if action == "resume":
                task_id = rest.split()[0] if rest else ""
                task = next((t for t in tasks if t["id"] == task_id), None)
                if task is None:
                    return f"No watch {task_id!r}."
                if task["status"] in {"done", "cancelled"}:
                    return f"Watch {task_id} is {task['status']} — terminal watches need a new id."
                task = dict(task, status="active")
                task = {k: v for k, v in task.items()
                        if k not in {"signal", "signal_at", "approved_at"}}
                self.ledger.record_task(task)
                return f"Watch {task_id} is active again."
            if action == "signal":
                task_id, _, signal = rest.partition(" ")
                if not signal.strip():
                    return "Use /watch signal <id> <observed state>."
                self.ledger.report_signal(task_id, signal.strip())
                return f"Signal recorded on {task_id}."
            return ("Use /watch list, /watch show <id>, /watch add {json}, "
                    "/watch pause <id>, /watch resume <id>, /watch done <id>, "
                    "/watch cancel <id>, or /watch signal <id> <text>.")
        except (ValueError, KeyError, IndexError, TypeError):
            return "Watch command failed — check the id or JSON payload. No change was claimed."


def register(ctx, *, home=None, background=True):
    runtime = Runtime(ctx, Path(home) if home is not None else hermes_home(), background=background)
    from .imessage import register_imessage
    register_imessage(ctx, runtime.home)
    from .proactive_schema import SCHEMA
    ctx.register_tool(name="proactive_control", toolset="proactivity", schema=SCHEMA,
                      handler=runtime.control, check_fn=lambda: True)
    ctx.register_command("proactivity", runtime.command,
                         description="Status, pause, resume, and configure proactive work")
    ctx.register_command("watch", runtime.watch_command,
                         description="List, schedule, pause, or cancel standing proactive watches")
    skills_dir = Path(__file__).parent / "skills"
    ctx.register_skill("proactive-primary", skills_dir / "proactive-primary" / "SKILL.md")
    ctx.register_skill("workspace-operations", skills_dir / "workspace-operations" / "SKILL.md")
    ctx.register_skill("workspace-setup", skills_dir / "workspace-setup" / "SKILL.md")
    ctx.register_skill("your-computers", skills_dir / "your-computers" / "SKILL.md")
    ctx.on_unload(runtime.close)
    if hasattr(ctx, "register_cli_command"):
        from .proactive_operator import setup, execute
        ctx.register_cli_command("proactivity", "Manage the designated proactive primary", setup,
                                 lambda args: execute(runtime, args))
    if background:
        runtime.start()
    return runtime
