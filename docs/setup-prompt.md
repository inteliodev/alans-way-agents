# Let the agent set it up

If you'd rather not run the bootstrap yourself, paste this prompt to a Hermes
agent that already has a terminal on the VPS (the Hermes CLI on the box, the
desktop app, or an existing agent with an ssh tool). It installs Tailscale on
both machines when asked, then runs the real `setup.sh` — the deterministic
script does the actual work; the agent just drives it and reports back.

Replace the two placeholders, then paste:

```text
Set up Alan's Way on this machine and connect it to my Mac.

1. If Tailscale isn't installed or connected here, install it
   (tailscaled + `tailscale up`). Tell me the tailnet name/IP of this machine
   when done. My Mac's Tailscale address is: __MY_MAC_TAILSCALE__
2. Fetch the bootstrap:  git clone https://github.com/inteliodev/alans-way-agents
   (or `git -C alans-way-agents pull` if it's already cloned). This is the
   Intelio fork of capthvnsen's alans-way-agents.
3. Run:  ./alans-way-agents/setup.sh --bot-id __MY_TELEGRAM_BOT_ID__ \
       --mac-ssh me@__MY_MAC_TAILSCALE__ --restart
   Answer its prompts; if it asks to bind a primary route, pick the bot
   matching this chat.
4. When it finishes, report: plugin status, whether the browser host started,
   the workspace_browser block location, and anything it flagged. Then run
   ./alans-way-agents/setup.sh --verify and paste me the summary.
5. If the VPS needs a desktop/VNC stack for the browser host and none exists,
   tell me the exact apt commands it printed — don't install the display stack
   on your own.
```

The prompt deliberately keeps the agent out of the credential and display-stack
decisions — `setup.sh` is idempotent, so a halfway run is safe to re-run.

## What the agent needs to succeed

- A shell on the VPS (it's already the gateway host)
- SSH key auth **from the VPS to the Mac** (Bootstrap can't create this — if the
  agent can drive the Mac too, it can exchange keys itself; otherwise do it once:
  `ssh-copy-id me@__MY_MAC_TAILSCALE__` from the VPS)
- The numeric Telegram bot ID (from the bot's profile link, `@userinfobot`, or
  `getMe` on the token)

## After it reports back

- Mac app: **Settings → Agent setup → Test agent path** should pass.
- Telegram: `/proactivity status` should show the route bound.
- Browser: ask the bot to open a page — a tab appears in the app while the Mac
  is awake, on the VPS host when it isn't.
