---
name: workspace-setup
description: Set up or onboard Alan's Way on a Hermes gateway host — plugin install, workspace browser wiring, VPS browser host, gateway restart, and proactivity binding. Use when the user asks to install, connect, onboard, or repair the Alan's Way setup on this machine or a new one.
---

# Workspace setup

## Drive the bootstrap; don't reimplement it

The repo ships `setup.sh`, a deterministic bootstrap that owns every step:
preflight, plugin install, gateway hook, VPS browser host and systemd units,
workspace_browser config, gateway restart, and primary-route binding. It is
idempotent — safe to re-run, safe to use `setup.sh --verify` alone to audit.

```sh
git clone https://github.com/inteliodev/alans-way-agents   # or git -C alans-way-agents pull
./alans-way-agents/setup.sh --bot-id <numeric-telegram-bot-id> \
    --mac-ssh <user>@<mac-host> --restart
```

The clone URL is the Intelio fork. Credit for the original plugin stays with
capthvnsen. Desktop checkout defaults are `ALANS_WAY_REPO` /
`ALANS_WAY_REF` (branch `cursor/intelio-harness-layer-8db4` until it merges).

Follow its prompts. If it asks which Telegram route is the primary, pick the
bot matching the conversation you're in unless the user said otherwise — do not
bind a different bot's route silently.

## Order of work

1. **Tailscale first when the two machines can't see each other.** If the VPS
   can't reach the Mac (or vice versa) and neither is on a tailnet, install
   `tailscale`/`tailscaled`, `tailscale up`, and report both tailnet addresses.
   Use the tailnet name for `--mac-ssh`, not a public IP.
2. **Telegram gateway must exist before the plugin matters.** If the script
   reports no `TELEGRAM_BOT_TOKEN`, tell the user to run
   `hermes gateway setup` → Telegram → Automatic (QR scan) — that step needs a
   human holding a phone; don't work around it.
3. **SSH key auth from VPS to Mac is a human step.** If BatchMode ssh fails,
   report the exact `ssh-copy-id` command rather than improvising credentials.
4. **The X11/VNC desktop stack is guided, never auto-installed.** If the
   bootstrap prints display-stack instructions, relay them verbatim. Do not
   pass `--desktop-stack` unless the human asked for those units. Browser
   services without a display fail-loop quietly. noVNC stays on loopback or a
   Tailscale address; never bind it publicly.
5. **Restart the gateway through its real owner.** `hermes gateway restart`
   first; if a PM/launchd supervisor owns it, let that supervisor restart it —
   never kill -9 a gateway mid-message.
6. **Bind only after a real DM route exists.** If no routes are listed, the
   user messages the bot once, then `setup.sh --bind`.

## Report, don't claim

After `setup.sh` finishes, run `setup.sh --verify` and report its summary
verbatim: plugin installed, hook present, browser host state, managed config
block, and any FAIL lines. "Installed" means the verify output says so — not
that the commands ran without visible errors.
