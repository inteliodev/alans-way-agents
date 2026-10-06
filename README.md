# Alan's Way — agent plugin

**The behavior half of [Intelio](https://github.com/inteliodev/alans-way), Hayden Ashley's fork of [Hermes — Alan's Way](https://github.com/capthvnsen/alans-way) by capthvnsen.**
This repository is `inteliodev/alans-way-agents`, the matching fork of
[capthvnsen's agent plugin](https://github.com/capthvnsen/alans-way-agents).
Install commands below use the fork. The original author remains capthvnsen.

This repo is what you install *on the machine running your Hermes agents*
(usually a VPS). The companion repo holds the Mac desktop app — this one holds
what your agents need to think and act:

- **`alans-way/`** — a native Hermes plugin: one designated primary bot
  gets bounded, event-driven proactivity — reviewing its own work, watching
  approved tasks, and surfacing useful things to do, on a schedule you control.
- **`hooks/alans-way/`** — a gateway startup hook that arms the plugin
  only inside the real gateway process.
- **Workspace browser wiring** — `setup-workspace.sh` writes a managed
  `workspace_browser` block into your Hermes config pointing at
  `scripts/workspace-router.cjs`, which probes your Mac first and falls
  back to the VPS browser when the Mac is asleep.
- **`skills/`** — the `proactive-primary` and `workspace-operations` skills ship
  inside the plugin so agents know how to use the tools correctly.

Works with stock Hermes `>= 0.21`. No Hermes source is patched: your existing
Telegram gateway keeps owning the conversation exactly as before — the plugin
adds tools and an optional review loop inside it, it is not a second gateway.

## What it does and does not do

- **Two browser hosts, no silent migration.** Bots get per-tab Chromium access
  on the Mac (through the desktop app's connector) and on the VPS (the managed
  browser). When the Mac is unreachable, *new* browser work routes to the VPS
  host automatically. Work already in flight in a Mac tab blocks while the Mac
  is asleep and resumes when it returns — the live tab is not moved between
  machines. Watching the VPS desktop from inside the Mac app additionally needs
  a VNC server and a noVNC viewer that you run on the VPS; see the app repo's
  [deployment guide](https://github.com/capthvnsen/alans-way/blob/main/docs/deployment.md).
- **Proactivity is read/research/draft by default.** The designated primary may
  read its own state, research, and draft proposals inside bounded budgets and
  quiet hours. Consequential actions — sending external messages or posts,
  purchases, credential or permission changes, production changes, destructive
  operations, new scope — always ask first. Details in
  [docs/proactivity.md](docs/proactivity.md).
- **No bundled account connections.** Email, calendar, Notion and similar
  connectors exist for an agent only if you install and authorize them
  separately in Hermes; this plugin provisions none of them.

## Install — zero to working

The full path has four pieces. Most are one step each; `setup.sh` detects
what's already done and skips it, so re-running is always safe.

### 1. Hermes on the VPS, with Telegram

You need a stock Hermes `>= 0.21` install whose gateway can answer Telegram.
If your Hermes has never talked to Telegram, that's the first step — and it
doesn't need BotFather: run `hermes gateway setup` on the VPS, choose
**Telegram → Automatic**, and scan the QR code with your phone. Hermes creates
the bot, saves the token, and allowlists your account. (Manual BotFather token
paste works too.)

### 2. The bootstrap (one command)

On the host that runs your Hermes gateway:

```sh
curl -fsSL https://raw.githubusercontent.com/inteliodev/alans-way-agents/main/setup.sh | bash -s -- \
    --bot-id YOUR_NUMERIC_BOT_ID --mac-ssh you@your-mac --restart
```

The desktop checkout defaults to `inteliodev/alans-way` at branch
`cursor/intelio-harness-layer-8db4` until that branch merges. Override with
`ALANS_WAY_REPO`, `ALANS_WAY_REF` (branch, tag, or full SHA),
`ALANS_WAY_AGENTS_REPO`, and `ALANS_WAY_AGENTS_REF`. `--dry-run` prints the
plan and writes nothing. A Hostinger VPS with the Mac on Tailscale is covered
in [docs/intelio-vps.md](docs/intelio-vps.md).

or from a clone: `./setup.sh --bot-id ... --mac-ssh ... --restart`

The bootstrap runs every step in order and says what it did:

- **Preflight** — hermes version, python3, node, HERMES_HOME
- **Telegram check** — if no `TELEGRAM_BOT_TOKEN` is configured it offers to
  launch `hermes gateway setup` right there
- **Plugin + gateway hook** — installs `alans-way`, arms the startup hook, and
  enables the `proactivity` toolset for Telegram sessions (without it,
  `proactive_control` never reaches the bound chat's tool list)
- **VPS browser host** — fetches the companion repo, installs the connector's
  dependencies, writes `config.json`, and installs the Chromium/broker systemd
  units (user units when you're not root)
- **Desktop prerequisites** — detects whether an X11/VNC stack exists and
  prints the exact packages to install if not (guided, never auto-installed)
- **Workspace config** — writes the managed `workspace_browser` block into the
  right profile's config
- **Gateway restart** — through the detected supervisor
- **Primary binding** — lists the Telegram DM routes that exist and asks which
  bot is the primary (message your bot once first if none exist yet, then
  re-run `setup.sh --bind`)
- **Verify** — prints a pass/fail summary of the whole install

Useful flags: `--profile NAME` for a named Hermes profile (toolset
enable/disable then use `hermes -p NAME`), `--mac-mcp-path` and
`--mac-node-path` for the Mac connector and Homebrew node (non-interactive SSH
does not load Homebrew's PATH), `--verify` to audit without changing anything,
`--dry-run` to print the plan, `--non-interactive` for scripted runs,
`--skip-browser` for proactivity-only installs. `--desktop-stack` is opt-in
and installs localhost-only Xvfb/x11vnc/noVNC units for a non-root desktop
user; it does not install apt packages, and the display stack is never
auto-installed.

### Or let your agent do it

If a Hermes agent already has a terminal on the VPS, paste it the prompt in
[docs/setup-prompt.md](docs/setup-prompt.md) — it installs Tailscale between
the machines if needed, runs the same `setup.sh`, and reports back. The
`workspace-setup` skill (bundled in the plugin) teaches it the same playbook.

### 3. The Mac app

Download the Mac app from the Intelio fork
([inteliodev/alans-way](https://github.com/inteliodev/alans-way), branch
`cursor/intelio-harness-layer-8db4` until it merges; the original releases
remain at [capthvnsen/alans-way](https://github.com/capthvnsen/alans-way/releases)).
Intelio looks for `~/Applications/Intelio.app`, `/Applications/Intelio.app`,
or a source checkout at `~/code/alans-way-intelio`. Unzip a release build,
move the bundle to Applications, right-click → Open (it's unsigned). Sign in to Telegram inside
the app, then **Settings → Agent setup**: the checklist shows what's already
done — Telegram sign-in, discovered bots, both SSH addresses, connector
status. Save the two SSH addresses, use **Copy setup command** (the bootstrap
above, pre-filled) or **Copy setup prompt**, then **Test agent path**.

### 4. Verify it end to end

- Mac app: Test agent path → ✓ VPS reaches this Mac over ssh
- Telegram: `/proactivity status` → route bound, gateway armed
- Browser: ask the bot to open a page — a tab appears in the app (Mac host)
  while the Mac is awake, on the VPS host when it isn't

### Upgrading

`git pull` (or re-run the `curl|bash` line), then restart the gateway — a
running gateway keeps already-imported code until restarted. Verify one
ordinary Telegram reply and one bounded browser action before relying on it.

### What each piece does

| Piece | Effect |
|---|---|
| `proactive_control` tool | `/proactivity` pause/resume/status, budgets, quiet hours — bound to one designated chat |
| Observer | 30s check for approved watches, bounded automatic opportunities |
| Gateway hook | Flips the plugin's "armed" flag only when running inside the gateway (not TUI/CLI probes) |
| `workspace_browser` MCP | `status`, `tabs`, `open`, `snapshot`, `screenshot`, `action` — per-bot scoped Chromium tabs on Mac or VPS |
| Router | probes the Mac's ssh alias for ~8s; unreachable → VPS browser host. Mac asleep mid-session → the in-flight Mac call fails visibly and the next MCP connection re-routes to a fresh VPS session; live Mac tabs are never migrated. Tool results carry the serving host and mac-watch state |
| mac-watch | optional systemd watcher (`deploy/`) probes the Mac every 30s and publishes a JSON state file the router and observer read |

## The workspace_browser tools

Each bot needs its own `--bot-id` — it owns that bot's tabs. The router passes
`--bot-name` through so the on-page agent cursor carries the bot's name and
color. Multi-bot setups: run `setup-workspace.sh` once per profile, each with
its own bot id (the script replaces only its own managed block).

Mac path requirements: the alans-way-localapp app running on the Mac, SSH from
this host to it (BatchMode/key auth — the probe uses `StrictHostKeyChecking`),
and the app's bundled `browser-mcp.cjs` (inside the installed `.app`).
VPS-only usage works with no Mac: the router detects the missing/unreachable
host and serves the local VPS browser host directly.

## Mac availability watcher

`alans-way/scripts/mac-watch.sh` probes the Mac over ssh on an
interval (default 30s) and keeps a JSON state file —
`{"state","since","lastSeenOnline","lastTransition"}` — that the router and
the proactive observer read instead of probing themselves. The router adds
the serving host and Mac state to `workspace_browser` results; the observer
turns an offline→online flip into a context event for the lead bot's review,
so kanban cards blocked on Mac-only work can resume. Transitions are logged
to `mac-events.log` beside the state file.

The observer also sweeps the shared `kanban.db` read-only
(`proactive_board.py`): open and recently-closed cards ride into the primary
bot's review context, so a blocked or changed card from another agent can
surface as one bounded opportunity — never a reason to touch someone else's
card. Combined with the connector's overseer role (`HERMES_OVERSEER_BOTS` /
`HERMES_OVERSEER_BOT_IDS`, see `integration.md` in the app repo), the primary
can answer "who is working where" and release a runaway tab.

Review context also carries `documents` — excerpts and modified times of the
primary's own identity files (`SOUL.md`, `AGENTS.md`, `IDENTITY.md`) — and
`capabilities`, the installed skill names. With `schedule` (enabled cron
metadata) that lets the appraiser weigh the wider remit: an upcoming
commitment worth surfacing, a capability gap worth one `ask` or a bounded
skill-building proposal, or a stale identity document worth a draft update.

```sh
sudo cp deploy/mac-watch.service /etc/systemd/system/
sudo mkdir -p /etc/hermes-alans-way
echo 'HERMES_WORKSPACE_MAC_SSH=you@your-mac' | sudo tee /etc/hermes-alans-way/mac-watch.env
sudo systemctl enable --now mac-watch.service
```

Adjust `ExecStart` to the installed script path and `User=` to the account
whose ssh keys reach the Mac. Environment variables:
`HERMES_WORKSPACE_MAC_SSH` (required — the same ssh alias the router probes),
`HERMES_MAC_STATE_FILE` (default `/var/lib/hermes-alans-way/mac-state.json`;
the unit's `StateDirectory` creates the parent directory).

Hermes's stock `browser_exec` (Browser Use) tool connects to its own
`browser.cdp_url` (default `http://127.0.0.1:9222`) — nothing shares it.
Run a dedicated Chromium there via `deploy/browser-exec-chromium.service`
reusing the same `vps-chromium-host.cjs` supervisor with
`deploy/browser-exec-config.json` under
`$HERMES_VPS_BROWSER_DATA/config.json`. It keeps its own
`--user-data-dir`; never point browser_exec at :9223, which the workspace
VPS browser owns.

## Safety model (short version)

- Human takeover wins always: control epochs invalidate queued agent actions.
- Per-tab ownership is cooperative policy between bots sharing the connector —
  not a crypto boundary. See the [security note](https://github.com/capthvnsen/alans-way/blob/main/desktop/docs/integration.md)
  for the trust model before exposing a connector beyond `127.0.0.1`.
- The proactivity observer is read-only about the world until an approved
  opportunity fires inside its one bound conversation. Budgets and quiet
  hours are in `docs/proactivity.md`.

## Layout

```
alans-way/            the plugin (plugin.yaml + tools + observer + skills + router + mac-watch)
deploy/               systemd unit for the Mac availability watcher
hooks/                gateway startup hook (installed by setup.sh)
docs/                 proactivity guide, agent-driven setup prompt, Intelio VPS runbook
tests/                unittest suite — python3 -m unittest discover -s tests
setup.sh              one-command bootstrap (install, wire, restart, bind, verify)
setup-workspace.sh    per-bot mcp_servers config writer (called by setup.sh)
```

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## License

MIT — see [LICENSE](LICENSE).
