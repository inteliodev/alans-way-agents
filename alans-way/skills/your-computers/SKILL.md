---
name: your-computers
description: Use when a task involves the user's own Windows or macOS computers enrolled in the intelio desktop app — their files, folders, screenshots, apps, or coding in a local repo with Claude Code or Codex — through the mcp__intelio_computers__* tools.
---

# Your computers (intelio node)

The user's enrolled computers reach you through the intelio relay as MCP tools
named `mcp__intelio_computers__<tool>`. Every call runs on that computer as its
signed-in user, not on this VPS.

| Tool | Use it to |
| --- | --- |
| `mcp__intelio_computers__list_computers` | see which computers exist and which are online |
| `mcp__intelio_computers__computer_info` | OS, user and volumes of one computer |
| `mcp__intelio_computers__list_dir` | list a directory |
| `mcp__intelio_computers__read_file` | read a file (text or base64, with offset/limit) |
| `mcp__intelio_computers__search_files` | find files by name glob or content |
| `mcp__intelio_computers__write_file` | create, overwrite or append to a file |
| `mcp__intelio_computers__run_command` | run one shell command to completion |
| `mcp__intelio_computers__screenshot` | capture a display |
| `mcp__intelio_computers__start_session` | start a long-lived terminal (PTY) session |
| `mcp__intelio_computers__send_input` | type text or keys into a session |
| `mcp__intelio_computers__read_output` | read new session output from a cursor |
| `mcp__intelio_computers__stop_session` | end a session |
| `mcp__intelio_computers__list_sessions` | see sessions still open on a computer |

## Computer tools or the VPS terminal

Use the computer tools only when the work concerns the user's own machine:
files that live there, apps installed there, a repo checked out there, or
something visible on its screen. Everything else — research, scripts, builds
that do not need the user's machine, your own notes — stays in your own
terminal and file tools on this VPS. Never copy a user's repo or files to the
VPS to work on them unless they ask.

## Pick the computer and say which one

1. Call `mcp__intelio_computers__list_computers` first in every task, even if
   you think you know the name. Names, ids and online state change.
2. Pick the computer the user meant. If more than one fits and the request
   does not say which, ask once.
3. Say which computer you are acting on ("On work-laptop (Windows): …")
   before or with the first action, and pass that `computer` on every call.

## Offline computers

If the computer is offline (`online: false`) or a call fails because it
disconnected, do not retry in a loop and do not switch to another computer on
your own. Tell the user in one line which computer is offline and when it was
last seen, and that the intelio desktop app must be running and "Allow intelio
agents to use this computer" must be on. Continue only after they say so.

## Read and search before writing

- Look before you change anything: `list_dir`, `search_files` and `read_file`
  to find the exact file and its current content.
- Prefer small, targeted writes. Use `write_file` with `mode: "create"` for new
  files so you never clobber one by accident; use `overwrite` only for a file
  you just read.
- Use the computer's own conventions: Windows paths and PowerShell/cmd on
  Windows, POSIX paths and a POSIX shell on macOS (`computer_info` tells you).

## Coding on a computer

Coding agents installed on the user's computer do repo work best there.

- **One-shot task:** `mcp__intelio_computers__run_command` with a
  non-interactive agent call in the repo directory (`cwd`), for example
  `claude -p "<task>"` or `codex exec "<task>"`. Give a generous `timeout_s`.
  Use this when the task is self-contained and needs no back-and-forth.
- **Interactive work** (an agent that asks questions, a REPL, a dev server, a
  long build you want to watch):
  1. `mcp__intelio_computers__start_session` with `command` and `cwd`; keep the
     returned `session_id`.
  2. Loop on `mcp__intelio_computers__read_output` with `since` set to the
     `cursor` from the previous read (omit it only on the first read) and a
     `wait_ms` so you are not polling hot. Stop looping when `exited` is true
     or the output shows the program is waiting for input. If `truncated` is
     true, read again from the new cursor before acting.
  3. Answer with `mcp__intelio_computers__send_input` (`text`, `enter: true`
     to submit; `keys` for control keys such as an interrupt).
  4. When done, `mcp__intelio_computers__stop_session`; use `force: true` only
     if it does not stop.
- Check the result like any coding task: read the changed files or the diff
  (`git status`, `git diff` via `run_command`), run the tests, and report.

## First-run sign-in

The first time `claude`, `codex`, `gh` or a similar tool runs on a computer it
may ask the user to sign in. Relay the device code and URL to the user exactly
as printed and wait for them to finish in their browser. Never type, ask for or
accept passwords, API keys, tokens or one-time codes yourself.

## Never leave sessions running

Stop every session you start before you finish the task, including on errors
and when the user changes topic. Before you report, call
`mcp__intelio_computers__list_sessions` and stop any of yours still open. Only
leave a session running (a dev server, say) when the user asked for it, and say
so with its `session_id`.

## Elevation needs the user

Admin or root actions (UAC prompts, admin password prompts, installers, system
settings) raise a confirm dialog on that computer, which only the user can
approve. Say what you are about to do and that a prompt will appear on their
screen, then wait. If it is declined or times out, stop and report; do not look
for a way around it.

## Secrets stay where they are

Do not read, print or copy secrets — `.env` files, SSH keys, keychains,
credential stores, browser profiles, token files — unless the user asked for
that specific thing. If a task needs a secret, have the tool on the computer
use it in place instead of moving it into the chat or onto the VPS.

## Report what changed

End with a short report: which computer, files created or changed (paths),
commands run that changed state, sessions started and stopped, and anything
left for the user (a sign-in, an elevation prompt, a failing test). Do not
claim a change you did not verify.
