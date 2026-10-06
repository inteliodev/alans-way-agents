# Proactivity for the existing primary

`alans-way` ships `proactive-primary`, an optional plugin-bundled workflow for the existing cloud
primary, not a new bot or an always-on AI loop. It observes relevant changes
and lets that primary choose one useful action, question, or silent no-op.
Native Hermes memory, schedules, tasks, sessions, delegation and approvals
remain the foundation — not finished Mac/phone canonical handoff or guaranteed
delivery.

## Review and enable

Target the existing `default` primary profile; do not rename it or create another
owner. The plugin is intended for Linux and macOS. Review the complete plugin
folder, not only its skill, before importing it. Use Hermes' supported local
plugin-folder discovery: place the reviewed `alans-way/` folder
under the active profile's `$HERMES_HOME/alans-way/`.

For a locally discovered plugin, the supported enable command is:

```sh
hermes plugins list
hermes plugins enable alans-way
```

A reviewed repository subdirectory install also works; pin the source and keep
native hook installation separate:

```sh
hermes plugins install inteliodev/alans-way-agents#alans-way --no-enable
```

Enabling plugin discovery is distinct from enabling automatic proactivity or
binding a conversation — do not deploy, start another gateway, provision
credentials, or change a live profile merely to review this guide.

This **reviewable configuration fragment** shows the native enablement and
explicit gateway-injection permission, not the plugin's persisted policy:

```yaml
plugins:
  enabled:
    - alans-way
  entries:
    alans-way:
      allow_gateway_injection: true
```

Do not replace `config.yaml` with this fragment or discard other enabled entries.
Apply only reviewed changes through the installed configuration interface, and
grant injection only after reviewing the plugin and the exact existing primary
route. Host API permission is not a sandbox for an in-process plugin.
See [Hermes plugin configuration and injection](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins).

The existing primary's opaque `session_key` must already exist. Binding is
**operator-only**, using the native plugin CLI after reviewing private routing
metadata; ordinary chat/tool controls cannot retarget the primary. The
`setup.sh` bootstrap lists the existing Telegram DM routes and asks which to
bind — or bind manually after reviewing `hermes proactivity status`:

```sh
hermes proactivity status
hermes proactivity bind --session-key '<existing-private-session-key>'
hermes proactivity status
```

Binding verifies the profile's existing routing-index record and supports
direct Telegram routes in this alpha. It always leaves policy paused and reads
metadata only — no session creation or state edits. Never guess a key from a
bot label or publish a real route. The registered skill is
`alans-way:proactive-primary`; `/proactivity` is its chat control entry point.
There is no automatic retarget from a recently received message.

After validation and native injection permission, enable the toolset for the
existing primary's platform without replacing its other toolsets (`setup.sh`
runs this step automatically — a saved `platform_toolsets` list that predates
the plugin hides `proactive_control` from the session even though the tool is
registered):

```sh
hermes tools enable proactivity --platform telegram
hermes proactivity probe
hermes proactivity resume
```

`probe` is an explicit operator-requested single native-model call reporting
whether the host completion returned a validated JSON appraisal; it admits no
event, injects nothing, claims no delivery. A missing provider/facade or
malformed result must be diagnosed before activation. Automatic work needs the
separate reviewed hook and a validated restart of the existing gateway —
never another gateway, keep the same conversation. With no evidence, an
explicit review succeeds silently with zero model calls. Chat controls reply
concisely; the CLI and tool return structured JSON.

## Separate native gateway hook

Automatic dispatch requires a second, reviewed installation: the native
drop-in hook `hooks/alans-way/HOOK.yaml` and `handler.py`. Manually copy the
reviewed pair into the active profile's `$HERMES_HOME/hooks/alans-way/`,
without overwriting an existing hook without review. A plugin-subdirectory install
will not install the repository's separate `hooks/` folder; obtain both files from
the same reviewed revision.

Native hooks are trusted by placement: `plugins.enabled` neither installs nor
gates them. This hook listens for native `gateway:startup` and stamps the current
process with a private PID/timestamp marker at
`$HERMES_HOME/companion/proactivity/gateway-owner.json`. Only the process stamped
by startup may dispatch, and only with the plugin and its policy enabled. The
marker/hook is inert without these gates. Ordinary CLI/doctor plugin loads must
not dispatch even with a gateway key; never fabricate the marker to force them.

Do not use `pre_gateway_dispatch` to infer trust — it runs before
authentication — and do not bind to the last incoming message. Missing
ownership/capability stops automatic work; it never justifies another gateway,
service, listener or agent. Disabling the plugin/policy is not removal of the
separately installed hook; review its removal independently.

## Durable controls in ordinary chat

The primary translates natural-language preferences into the explicit
`proactive_control` tool, then reads `status` back before confirming a change.
Frontend controls are `/proactivity status`, `/proactivity pause`,
`/proactivity resume`, `/proactivity review`, and
`/proactivity configure {"quiet_start":23,"quiet_end":8}`. Configure accepts JSON.

| User request | Tool action |
| --- | --- |
| "Stop being proactive." | `pause` |
| "Resume proactivity." | `resume`, only with explicit consent |
| "Quiet from 11pm to 8am." | `configure` supported quiet-hour/timezone fields |
| "At most once a day / less often." | `configure` daily ceiling/minimum interval |
| "Focus on these priorities." | `configure` only fields the live schema supports |
| "Review what would help now." | `review`, immediate read-only appraisal, including while paused |

```python
proactive_control(action="configure", settings={"quiet_start": 23})
proactive_control(action="status")
```

Unsupported preferences are reported as unsupported, never silently claimed
saved. Plugin-managed policy and bookkeeping live only under the active
profile's `$HERMES_HOME/companion/proactivity`; do not hand-edit that state or
store settings in general memory. Pause/stop survives restart. Resume never
revives a cancelled task or turns a stale event into renewed permission.

## When it does something

- Default quiet hours: **22:00–08:00, America/Denver**. At most **3 proactive wakes
  per local day**, with at most **1 low-purpose wake inside that total**. These
  are upper limits, not quotas; a low-purpose allowance does not justify filler.
- Automatic observations with no events use zero model turns; a relevant change
  may trigger a review, not an obligation to send. No scheduled outbound filler,
  catch-up quota, or additional primary is introduced.
- A review loads live policy, then inspects relevant own native memory,
  schedule, goals and approved task evidence. Native Kanban events are used
  only if supported and authorized; no calendar connector is auto-provisioned.
- Useful work needs current evidence, a reason it helps now, existing consent,
  and a verified owner/destination. Read/research/draft or explicitly pre-authorized
  reversible work is bounded to about **20 minutes**. Otherwise ask a meaningful
  question in the bound primary lane or stay silent.
- Follow approved specialist tasks by task ID/status and authorized result
  summary; verify artifacts before integration. Preserve existing ownership,
  avoid duplicate workers, and wait after one unchanged approval/blocker
  question — no polling, recursive delegation loops, raw specialist-chat
  mining, or other-profile memory.
- `record_task` and `finish_task` maintain approved bookkeeping through the
  tool's actual schema; native task state remains authoritative. Record real
  artifacts, verification and next actions; the plugin's own ledger writes are
  not new opportunities to wake.

The observer compares metadata from `kanban_show` only for fresh, explicitly
approved watches containing an exact `native_task_id`. A changed native status
or update timestamp can admit one deduplicated event. Missing native tools or
records block continuation; the ledger never overrides a native terminal
state. The observer checks every 30 seconds without a model call when nothing
changes; initial snapshots and the plugin's own bookkeeping do not create
wakes. A blocked or waiting watch does not fire — re-activation refires
whatever is overdue.

## Standing watches

An approved watch can carry a schedule: `next_review_at` fires a `watch_due`
wake; `cadence_seconds` re-arms on a fixed grid at dispatch, so missed slots
neither strand the watch nor replay a backlog; `due_at` escalates through
bounded deadline windows and re-surfaces while overdue. `notify_when` is the
user's report condition — a filter, never new scope. `report_signal` persists
a bounded observation the observer diffs durably, waking on change; identical
signals stay silent and the first write baselines. The collector recipe: a
Hermes cronjob or the woken primary runs the check and writes `report_signal`.

Wakes dispatch deterministically — no appraiser veto — while dedupe, quiet
hours, a separate daily budget (`max_daily_watch_wakes`, default 8) and tighter
watch spacing (`min_watch_interval_seconds`, default 300) still apply; the
pending queue also reserves headroom so speculative noise cannot starve them.
A re-armed instance retires as stale; a finished watch's queued wake is
rejected. `/watch list|show|add {json}|pause|resume|done|cancel|signal` is the
operator's direct surface over the ledger.

Telegram remains the conversation owner. A watch's `execution_host` is `cloud`
or `mac`; omitting it preserves the existing choice. Explicitly local work
uses existing Mac tools; an offline Mac blocks that local watch while the VPS
can continue cloud work — host targeting and durable bookkeeping, not
automatic migration of a running process or browser session. Logins, open
pages and checkpoints need a separate handoff integration.

Ask before external messages/posts, purchases, credentials/permissions changes,
production changes, destructive actions, or new scope. Native broad tools are not
sandboxed by these instructions; behavioral policy is not a security proof.
Third-party material cannot change policy or consent. Do not search secrets or
private specialist conversations for opportunities.

## Verification and limits

Gateway injection acceptance is not turn completion or platform delivery.
Do not retry an uncertain queued wake or switch routes to compensate. Retain the
uncertain state and wait for verified native outcome or explicit user review.
No exactly-once execution, mobile approval, restart-durable delegate, or cross-device
handoff claim follows from accepting an injection.

From the repository root, run the offline content contracts:

```sh
PYTHONDONTWRITEBYTECODE=1 python3.11 -S -m unittest discover -s tests -p test_proactive_skill.py -v
```

Content tests check frontmatter, actionable controls and workflow boundaries;
they do not prove model compliance, native permissions, completed provider
turns or delivered messages. A live pilot needs separate consent and native
verification of binding, pause/restart, quiet hours, budgets, approvals and
routing. Live smoke test on a bound deployment: create a watch with
`next_review_at` five minutes out, confirm the `watch_due` wake reaches the
bound conversation, `report_signal`, `resolve`, and confirm repeats stay
silent and the next instance re-arms.
