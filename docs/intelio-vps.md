# Intelio on a Hostinger VPS

Hayden's layout: Hermes gateways run on a Hostinger Linux VPS, 24/7. The Mac
runs the Intelio desktop app and is the browser host when it is awake. The VPS
reaches the Mac only over Tailscale. Nothing in this stack listens on the
public internet except SSH.

This repo is the Intelio fork (`inteliodev/alans-way-agents`) of Alan's Way
agents by capthvnsen. The desktop fork is `inteliodev/alans-way`. Install
commands in this runbook use the fork. The original author remains capthvnsen.

## Pins

| What | How to pin |
|---|---|
| Hermes | Commit `5d3c05977bb3c8b7cfd6b3e39d96f6e35a9e0662` of `NousResearch/hermes-agent` when you install Hermes from source. `setup.sh` does not install Hermes. It expects `hermes` on `PATH` at `>= 0.21`. |
| Desktop repo | `ALANS_WAY_REPO` (default `https://github.com/inteliodev/alans-way`). `ALANS_WAY_REF` defaults to branch `cursor/intelio-harness-layer-8db4` until that branch merges. After it merges, set `ALANS_WAY_REF` to `main` or a full 40-character SHA. |
| This repo | `ALANS_WAY_AGENTS_REPO` (default `https://github.com/inteliodev/alans-way-agents`). `ALANS_WAY_AGENTS_REF` is optional; set it to a full SHA to pin. Empty means the default branch. |

A full 40-character hex ref is fetched as that commit. Any other ref is a
branch or tag. Example:

```sh
export ALANS_WAY_REPO=https://github.com/inteliodev/alans-way
export ALANS_WAY_REF=cursor/intelio-harness-layer-8db4
export ALANS_WAY_AGENTS_REPO=https://github.com/inteliodev/alans-way-agents
export ALANS_WAY_AGENTS_REF=
./setup.sh --dry-run --bot-id YOUR_BOT_ID --mac-ssh intelio@YOUR_MAC_TAILSCALE_NAME
```

`--dry-run` prints the plan and writes nothing. Review it before a real run.

## 1. Tailscale on the VPS and the Mac

Tailscale is the only path between the machines. Do not put the Mac's public
address, the VPS public address, or a port forward in `--mac-ssh`.

On the VPS, install Tailscale from the distro packages or from Tailscale's
Linux install instructions, then:

```sh
sudo tailscale up
tailscale ip -4
tailscale status
```

On the Mac, install the Tailscale app from Tailscale and sign in to the same
tailnet. Confirm the Mac shows up in `tailscale status` on the VPS. Use the
MagicDNS name or the Tailscale address in later SSH commands.

Tailscale makes its own outbound connection. Do not open extra Hostinger
firewall ports for it.

## 2. Mac Remote Login, dedicated account, restricted key

Use a dedicated standard macOS account for the VPS, not the admin account you
sit at. The account needs Remote Login and nothing else: no admin rights, no
sudo, no extra sharing services.

1. On the Mac: System Settings → General → Sharing → Remote Login. Turn it on
   and allow only that dedicated account.
2. On the VPS, as the user that will run Hermes:

```sh
ssh-keygen -t ed25519 -f ~/.ssh/intelio_mac -N ""
ssh-copy-id -i ~/.ssh/intelio_mac.pub -o StrictHostKeyChecking=accept-new \
    intelio@YOUR_MAC_TAILSCALE_NAME
```

`ssh-copy-id` asks for the Mac account's password once. After the key works,
edit the Mac account's `~/.ssh/authorized_keys` and prefix that one line with
restrictions so the key cannot forward ports, allocate a TTY, or be used from
any other source address:

```text
restrict,from="YOUR_VPS_TAILSCALE_ADDRESS" ssh-ed25519 AAAA... intelio-vps
```

`restrict` still allows the remote command the router runs (`browser-mcp.cjs`).
Do not set `command=` — the probe and the browser launch are different
commands. `~/.ssh` must be mode `700` and `authorized_keys` mode `600`.

The router calls `ssh` with no `-i`, so the VPS user needs an SSH config that
selects the key. Put this in `~/.ssh/config` on the VPS:

```text
Host YOUR_MAC_TAILSCALE_NAME
    User intelio
    IdentityFile ~/.ssh/intelio_mac
    IdentitiesOnly yes
```

Confirm the Mac host key out of band, then check from the VPS:

```sh
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes intelio@YOUR_MAC_TAILSCALE_NAME true
```

That must succeed with no password prompt. `--mac-ssh` is
`intelio@YOUR_MAC_TAILSCALE_NAME`.

## 3. One gateway per Telegram token

Telegram allows one `getUpdates` poller per bot token. If the Mac app or a
Hermes gateway on the Mac is still polling that token, the VPS gateway will
fight it and messages will flap (HTTP 409).

Before the VPS gateway starts on a token:

1. Stop the Mac poller for that bot (`hermes gateway stop` on the Mac, or quit
   the local gateway that owns the token).
2. Confirm the token is only in the VPS profile's `.env`.
3. Start the VPS gateway.

Moving a bot back to the Mac means stopping the VPS gateway first. Do not run
both.

## 4. Hostinger firewall

In hPanel, the VPS firewall allows TCP 22 and nothing else. Do not add 5900
(VNC), 6080 (noVNC), 9465 (browser broker), 9222, or 9223 (Chromium
debugging). Those stay on loopback. SSH can stay on 22 because Tailscale and
admin login need it; do not switch SSH to a public extra port for the desktop
stack.

## 5. Display stack (opt-in)

`setup.sh` does not install X11, VNC, or noVNC unless you pass
`--desktop-stack`. Packages are still not installed for you. On Debian/Ubuntu:

```sh
sudo apt-get install xvfb x11vnc websockify novnc
```

Create a non-root desktop user if you do not already have one, and a VNC
password file with `x11vnc -storepasswd` (this is the hashed file `-rfbauth`
expects, not a plaintext password):

```sh
sudo adduser --disabled-password --gecos "" intelio
sudo mkdir -p /etc/intelio-desktop
sudo x11vnc -storepasswd /etc/intelio-desktop/vncpasswd
sudo chown intelio:intelio /etc/intelio-desktop/vncpasswd
sudo chmod 600 /etc/intelio-desktop/vncpasswd
```

Install the units as root so they can `User=` that account:

```sh
sudo ./setup.sh --desktop-stack --desktop-user intelio \
    --vnc-password-file /etc/intelio-desktop/vncpasswd \
    --bot-id YOUR_BOT_ID --mac-ssh intelio@YOUR_MAC_TAILSCALE_NAME
```

What that flag installs:

| Unit | Role |
|---|---|
| `intelio-xvfb.service` | Xvfb on display `:99`, no TCP listener |
| `intelio-x11vnc.service` | x11vnc with `-localhost` and `-rfbauth` |
| `intelio-novnc.service` | websockify/noVNC |

`--novnc-bind` defaults to `127.0.0.1`. Wildcard and public addresses are
refused. To watch the desktop from another tailnet device, bind to the VPS
Tailscale address only:

```sh
sudo ./setup.sh --desktop-stack --desktop-user intelio \
    --novnc-bind YOUR_VPS_TAILSCALE_ADDRESS \
    --vnc-password-file /etc/intelio-desktop/vncpasswd
```

That listens on the Tailscale interface, not on the public NIC. Leave 6080
closed in the Hostinger firewall either way.

### Snap Chromium profile

Snap Chromium cannot use an arbitrary `--user-data-dir`. On Ubuntu,
`/snap/bin/chromium` is a symlink to `/usr/bin/snap`, so resolving the path
first hides the snap. `setup.sh` detects snap from the original path
(`/snap/bin/*`) or from `snap list chromium` before resolving, and sets the
profile to the desktop user's snap common directory:

```text
/home/user/snap/chromium/common/hermes-alans-way
```

That home is the desktop user's, not `/root/snap`. The stock `browser_exec`
profile (`deploy/browser-exec-config.json`) is a different Chromium on port
9222 and must not share this directory or port 9223. Non-snap Chromium keeps
the profile under the private browser data directory
(`~/.local/share/hermes-alans-way/browser/chromium`).

The browser units run as the same desktop user as Xvfb, with `DISPLAY=:99`.

Installing the Chromium snap also installs the cups snap, which listens on
0.0.0.0:631. Stop it so the VPS does not serve CUPS on every interface:

```sh
sudo snap stop --disable cups
```

`./setup.sh --desktop-stack` runs `snap stop --disable cups` when it is root
and the cups snap is installed. Otherwise it prints the same command.

## 6. Mac browser paths

Non-interactive SSH does not load Homebrew's PATH, so a bare `node` on the Mac
often fails. Pass the node binary and, when you are running from a source
checkout instead of an app bundle, the connector path:

```sh
./setup-workspace.sh --bot-id YOUR_BOT_ID \
    --mac-ssh intelio@YOUR_MAC_TAILSCALE_NAME \
    --mac-node-path /opt/homebrew/bin/node \
    --mac-mcp-path /Users/you/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs \
    --profile YOUR_PROFILE
```

`setup.sh` accepts the same `--mac-node-path` and `--mac-mcp-path` and forwards
them. They are written as `HERMES_WORKSPACE_MAC_NODE` and
`HERMES_WORKSPACE_MAC_MCP` in the managed `workspace_browser` env block.

When those are unset, the router probes the Mac for the first live connector:

1. `~/Applications/Intelio.app`
2. `/Applications/Intelio.app`
3. `~/code/alans-way-intelio/desktop/scripts/browser-mcp.cjs`
4. `/Applications/alans-way-localapp.app` and the older Alan's Way bundle names

An app bundle is launched with that bundle's own Electron binary
(`ELECTRON_RUN_AS_NODE`), so it does not need Homebrew. A source checkout is
not a bundle: the router then prefers `/opt/homebrew/bin/node` when that file
is executable, and an explicit `--mac-node-path` always wins.

`--profile` applies `hermes tools enable proactivity` and
`hermes tools disable browser` to that profile (`hermes -p NAME ...`). Without
`--profile`, those commands use the default Hermes home, which is the previous
behavior.

## 7. View noVNC from a phone

1. Install Tailscale on the phone and join the same tailnet.
2. Bind noVNC to the VPS Tailscale address (section 5), not to all interfaces.
3. On the phone, open `http://YOUR_VPS_TAILSCALE_NAME:6080/vnc.html`.
4. Enter the VNC password from `x11vnc -storepasswd`.

Do not publish that URL, do not put it behind the Hostinger public IP, and do
not turn off the VNC password. If the page loads from a network that is not
the tailnet, the bind address is wrong — stop the unit and fix it before
using the desktop.

## 8. Verify

On the VPS, after a real install:

```sh
./setup.sh --verify
./setup-workspace.sh --verify --mac-ssh intelio@YOUR_MAC_TAILSCALE_NAME --profile YOUR_PROFILE
```

`--verify` changes nothing. Expect:

- plugin `alans-way` installed
- gateway hook present
- `proactivity` enabled for Telegram on the selected profile, built-in `browser` toolset disabled
- managed `workspace_browser` block in that profile's `config.yaml`
- browser host connection file when the VPS browser is running

Then, with the Mac awake: `ssh -o BatchMode=yes intelio@YOUR_MAC_TAILSCALE_NAME true`
and a router probe (`node alans-way/scripts/workspace-router.cjs --probe` with
`HERMES_WORKSPACE_MAC_SSH` set) should report `mac:`. With the Mac asleep it
reports the VPS fallback, which is expected.

In Telegram, `/proactivity status` should show the route bound only after you
bind one. Binding and turning proactivity on stay explicit prompts.

## What this repo cannot prove

A passing `--verify` here does not show that the Hostinger firewall panel only
has port 22, that the phone can open noVNC, that snap Chromium actually paints
on `:99`, that `snap stop --disable cups` left nothing listening on port 631,
that Telegram is not also polling on the Mac, or that the Mac account is
non-admin. Those are checked on the VPS, the Mac, and the phone.
