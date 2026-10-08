# Push approval (intelio)

Hayden's rule, Oct 8 2026: *"It can read and drive on computers yes anything push is an approval."*
Agents read files, change files, run commands and drive apps on his computers and the VPS with no
prompts. The one thing that asks him first is a **push**: `git push`, `gh pr merge`, `gh repo sync`,
a `gh api` write to a repository, a push from Claude Code or Codex started by an agent, or a push
through the intelio computers connector. `approvals.mode` stays `off` in all five profiles.

## How Hermes approvals work at the pin (d9ef91e)

| Piece | Where | What it means here |
|---|---|---|
| `approvals.mode` (`manual` / `smart` / `off`) | `tools/approval_context.py` `_get_approval_mode` | `off` = yolo for every approval layer |
| Per-command dangerous patterns | `tools/approval_detection.py` `DANGEROUS_PATTERNS` (git section ~L443) | only **force** push is a pattern; a plain `git push` never matched anything |
| Command gate | `tools/approval.py` `check_all_command_guards` (~L1110) | returns approved as soon as mode is `off` (after the floors) |
| Floors that run even under `off` | `tools/approval.py` `_floor_block`, `tools/approval_floors.py` | hardline catastrophes, `sudo -S` piping, runtime self-delete, and the user's `approvals.deny` globs. Block only, never ask |
| `command_allowlist` / session approvals | `tools/approval.py` `is_approved`, `_command_matches_permanent_allowlist` | skip prompts; nothing in them can make a command ask |
| `request_tool_approval` (plugin `pre_tool_call` returning `{"action": "approve"}`) | `tools/approval.py` ~L1080, `hermes_cli/plugins.py` ~L2051 | goes through `_run_approval_gate`, whose first line approves under `off`, so it **cannot** ask with mode off |
| `smart` mode | `tools/approval_smart.py` | an aux LLM judges flagged commands; still only for pattern matches |
| `request_elicitation_consent` | `tools/approval_prompt.py` ~L287 | **does not read `approvals.mode`**. Routes to the surface that owns the session; built for MCP elicitation and the browser vault |
| Gateway wait | `tools/approval_gateway_wait.py` `_await_gateway_decision` | parks the agent thread until the surface answers or `approvals.timeout` (300 s) passes; silence is a no |
| Surfaces | `gateway/platforms/api_server.py` `_register_session_stream_approval` (~L3743); `gateway/run_turn_runner.py` `_approval_notify_sync` (~L1455); `tui_gateway/server.py` ~L1051 | intelio app: `approval.request` SSE event on `/api/sessions/<id>/chat/stream`, answered with `POST /v1/runs/<run_id>/approval {choice, request_id}`; Telegram: approval buttons; TUI/CLI: the approval panel |
| Nobody can answer | `tools/approval_context.py` `_no_user_can_answer` | cron, `-q` and webhook runs get an instant decline |

So with `mode: off` there is no pattern- or allowlist-based way to make *only* pushes ask, and
switching to `manual`/`smart` would bring prompts back for everything flagged (`rm -r`, `chmod`,
`execute_code`, protected writes...), which is not what Hayden wants. The supported way to ask
under `off` is `request_elicitation_consent`, from a plugin hook.

## Design

Three layers, each catching what the one before cannot see.

1. **Hermes plugin `push-approval`** (`push-approval/__init__.py`, `pre_tool_call`).
   For `terminal` commands (and `execute_code` scripts) that push, it asks Hayden with
   `request_elicitation_consent`: the intelio app shows a small inline *Allow / Don't allow* row,
   Telegram shows its approval buttons. One prompt per command, never remembered. On allow it
   mints a one-time grant and runs the command as `export INTELIO_PUSH_GRANT=<id>; <command>`.
   Deny, timeout, no surface (cron) or any error blocks the tool (fail closed). A push typed into
   a background process is refused with a pointer to the terminal tool. Changing the guard itself
   (`core.hooksPath`, the grants folder, `GIT_CONFIG_*` overrides) asks the same way.
   MCP tools are skipped: the intelio computers relay asks for connector pushes itself (MCP
   elicitation, same Hermes prompt), so nothing is asked twice.
2. **Git backstop: global `core.hooksPath` pre-push guard** (`push-approval/push_guard.py`,
   installed to `~/.config/intelio/push-guard/`). Every `git push` on the VPS runs it, whoever
   started git: the terminal tool, Claude Code, Codex, a script. In an **agent process** a push
   needs a valid grant, which only exists after Hayden allowed that command; otherwise git prints
   a refusal that tells the coding tool to stop and report. All other hooks are pass-through
   dispatchers, so each repo's own `.git/hooks/*` and any previous global hooks path still run.
3. **PATH shims `gh` and `git`** (`~/.config/intelio/push-guard/bin`, prepended to PATH inside
   Hermes by the plugin). They catch what git hooks never see in subprocesses: `gh pr merge`,
   `gh repo sync`, `gh api` writes, and `git push --no-verify` / `-c core.hooksPath=...`.

**Who is an agent** (`push_guard.agent_context`): a process with `INTELIO_AGENT` (exported by the
plugin inside Hermes) or `HERMES_SUPERVISED_CHILD` (set by the hermes-gateway unit), or any process
whose cgroup is under the systemd **user manager** (`user@1000.service/...`: hermes-gateway,
intelio-pwa and its `cloud` computer, `systemd-run --user` scopes). Hayden's SSH login is
`session-N.scope`, never an agent, so his own pushes work exactly as before, with no prompt. There
is no environment switch that turns the guard off, because an agent could set it.

**Grants**: 0600 JSON files in `~/.local/state/intelio/push-grants/` (folder 0700), 128-bit ids,
15-minute expiry, `uses` = the number of pushes in the approved command, deleted when used up.
**Log**: `~/.local/state/intelio/push-guard.jsonl`, one line per decision (agent or person, remote,
refs, outcome). The grant id and URL credentials are never logged.

## Limits (plain words)

- **Same Unix user.** Agents run as `hayden` with `gh` signed in. A determined agent could read the
  gh credential and call the GitHub API directly, use a git library that does not run hooks, or
  `ssh hayden@localhost` into a login session. The plugin and guard stop normal and accidental
  pushes and every push a coding tool makes the usual way; they are not a security boundary
  against a hostile agent. The real boundary is a separate Unix user for agents without push
  credentials (see "Decisions").
- **Repo-local hooks paths win.** A repo that sets its own `core.hooksPath` (husky and similar)
  skips the global guard. The plugin still asks for pushes the agent runs itself, but a coding
  tool's own push in such a repo is not caught.
- **Shims only where PATH is kept.** A coding tool that rebuilds PATH from login files skips the
  `gh`/`git` shims; the pre-push hook still covers its `git push`, but not its `gh pr merge` or
  `--no-verify`.
- **Telegram shows Hermes' standard buttons.** *Always* and *Session* there act like *Allow once*:
  push approvals are never remembered.
- **Cross-surface answering.** A push in a Telegram conversation is answered in Telegram; a push in
  the intelio app is answered in the app.
- **Cron and unattended runs cannot push.** Nobody can answer, so Hermes declines at once.

## Decisions for Hayden

1. A separate `agents` Unix user without GitHub credentials, if pushes must be impossible rather
   than asked (bigger change: file ownership, tools, profiles).
2. Whether the default profile and all five named profiles get the plugin (the setup default).

## Rollout (VPS; needs Hayden's OK; owned by the release worker)

Nothing here changes `approvals.mode` or any other approval setting.

1. On the VPS, check out this branch of alans-way-agents, e.g. in `~/alans-way-agents`.
2. Dry run, then install:
   ```sh
   sh scripts/push-approval.sh install --dry-run
   sh scripts/push-approval.sh install          # profiles: default,intelio,prc,alignment,hhp,arlp
   ```
   Files: `~/.config/intelio/push-guard/{push_guard.py,hooks/*,bin/gh,bin/git,previous-hooks-path}`,
   `~/.local/state/intelio/push-grants/`, `~/.gitconfig` (`core.hooksPath`), and per profile
   `plugins/push-approval/` plus `plugins.enabled += push-approval` in that profile's
   `config.yaml`. The git guard is live as soon as `core.hooksPath` is set (no restart).
   Hermes' install scan rates the plugin "caution" (it reads `/proc/self/cgroup`, runs `git`, and
   lists `sudo`/`env` as wrappers to see through), so the script installs with `--force`, plus
   `--allow-live-gateway` while the gateway runs.
3. **Gateway restart: yes, one graceful restart** to load the plugin
   (`systemctl --user reload hermes-gateway`, drain-aware). Until then the git guard already refuses
   agent pushes, but agents cannot ask, so do it promptly (when Hermes is idle).
   intelio-pwa and intelio-phone-bridge need no restart for this part.
4. Check: `sh scripts/push-approval.sh status`; from an SSH shell, `git push` in a test repo still
   works; in the intelio app ask an agent to push a test branch and see the inline prompt.
5. Undo: `sh scripts/push-approval.sh uninstall` (restores the previous `core.hooksPath`, disables
   the plugin), then a graceful gateway restart.

Hermes pin bumps: re-run `scripts/push_approval_contract.py` (CI hermes-contract job). It fails if
`mode: off` stops bypassing `request_tool_approval` or if `request_elicitation_consent` starts
honouring `mode: off`.
