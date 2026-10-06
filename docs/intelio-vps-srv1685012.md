# Runbook of record: Intelio VPS (Hostinger srv1685012)

State as installed on 2026-10-05 (America/Chicago). Contains no secrets, public IP
addresses, tokens, chat IDs or tailnet addresses. Look those up on the host
(`tailscale ip -4`, the profile `.env`), never in this repo. General procedure:
[intelio-vps.md](intelio-vps.md).

## Host

| Item | Value |
|---|---|
| OS | Ubuntu 24.04.4 LTS, 2 vCPU, 8 GB RAM |
| Admin user | `hayden` (uid 1000, sudo). All Intelio services run as this user. |
| SSH | Port 22, key-only, root login disabled. sshd config unchanged by this install. |
| Firewall (ufw) | Default deny incoming. `22/tcp LIMIT` from anywhere; `6080/tcp ALLOW IN on tailscale0` only. 80/443 were removed. |
| Tailscale | 1.102.5 from `pkgs.tailscale.com` (noble), hostname `intelio-vps`, `--ssh=false`. |
| Linger | `loginctl enable-linger hayden`, so user units run at boot without a login. |

## Installed software

| Component | Version / pin | Source |
|---|---|---|
| Node.js | 22.x (22.23.3 at install) | NodeSource apt repo, signed keyring `/etc/apt/keyrings/nodesource.gpg` |
| Chromium | snap `chromium` (154.0.8037.57 at install) | snap store |
| cups snap | installed as a Chromium dependency, **disabled** (`snap stop --disable cups`) so nothing listens on 631 |
| Display | xvfb, x11vnc, websockify, novnc, fonts-noto-cjk, fonts-noto-color-emoji | Ubuntu apt |
| Hermes Agent | v0.21.5, commit `5d3c05977bb3c8b7cfd6b3e39d96f6e35a9e0662` (detached) | that commit's `scripts/install.sh --commit … --skip-setup --skip-browser --skip-computer-use`; install dir `~/.hermes/hermes-agent` |
| alans-way (desktop/browser host) | branch `cursor/intelio-harness-layer-8db4` (08361e2 at install) | `~/intelio/alans-way` |
| alans-way-agents (plugin) | branch `cursor/intelio-vps-harness-1162` | `~/intelio/alans-way-agents` |
| Plugin `alans-way` | 0.4.1, installed and enabled in the default profile **and** in profile `intelio` | `file://~/intelio/alans-way-agents#alans-way` |

Do not run `hermes update`. Hermes stays on the pinned commit until someone
deliberately moves the pin.

`/opt/hermes-alans-way/browser` is a symlink to `~/intelio/alans-way`. The
workspace router (`alans-way/scripts/workspace-router.cjs`) looks for the VPS
connector `browser-mcp.cjs` only at that path when the router has no sibling copy.

## Services (systemd **user** units for `hayden`, all enabled)

| Unit | What | Listens on |
|---|---|---|
| `intelio-xvfb.service` | Xvfb `:99`, 1920x1080x24, `-nolisten tcp` | none |
| `intelio-x11vnc.service` | x11vnc on `:99`, `-localhost`, `-rfbauth ~/.config/intelio/vncpasswd` | 127.0.0.1:5900, [::1]:5900 |
| `intelio-novnc.service` | websockify + noVNC web root | `<vps-tailscale-ip>`:6080 only |
| `hermes-alans-way-chromium.service` | keeps snap Chromium running, profile `~/snap/chromium/common/hermes-alans-way` | 127.0.0.1:9223 (CDP) |
| `hermes-alans-way-browser.service` | VPS tab broker (`vps-browser-host.cjs serve`) | 127.0.0.1:9465 |
| `hermes-gateway.service` | Hermes host gateway (`hermes gateway run`); serves every profile, including `intelio` | no inbound port; Telegram long polling |

Drop-ins:

- `hermes-alans-way-browser.service.d/intelio-sidecar.conf` sets
  `INTELIO_SIDECAR=~/intelio/alans-way.yaml`.
- `intelio-novnc.service.d/wait-tailscale.conf` waits for the Tailscale address
  on `tailscale0` before websockify binds, with `Restart=always`,
  `StartLimitIntervalSec=0`. A user unit cannot order on the system
  `tailscaled.service`, so it polls instead.

Nothing new listens on 0.0.0.0 or [::] except sshd on 22. tailscaled also
binds its own UDP 41641, and ufw blocks inbound there.

## Browser host and safety

- Broker config: `~/.local/share/hermes-alans-way/browser/config.json` (0600).
  CDP is `http://127.0.0.1:9223` with `--remote-debugging-address=127.0.0.1`, and
  `intelioSidecar` points at the sidecar below.
- Sidecar `~/intelio/alans-way.yaml` (0600):

  ```yaml
  hermes_profile: intelio
  browsing_origins: []
  safety:
    yolo: false
    consequential: ask
  ```

- Verified: agent opens of `/checkout`, `/billing` and paypal.com return
  `409 approval_required`. A sidecar with `yolo: true` or
  `consequential: auto` is refused at load.

## Hermes profile `intelio` (bot @inteliodevbot)

- Home: `~/.hermes/profiles/intelio/`.
- `.env` (0600) holds `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USERS` (Hayden's
  Telegram user ID only). Never commit it.
- Telegram toolsets: `proactivity` enabled, built-in `browser` disabled.
  `terminal` and `computer_use` are still enabled; whether to disable them is
  open (they can reach loopback CDP directly).
- `mcp_servers.workspace_browser` managed block: the router with the bot's
  `--bot-id`, `HERMES_WORKSPACE_MAC_SSH` empty (VPS-only routing until the Mac
  link exists), `timeout: 120`.
- `plugins.entries.alans-way.allow_gateway_injection: true`. Proactivity stays
  paused until someone explicitly resumes it.
- Model: provider `openai-codex` (ChatGPT/Codex subscription via OAuth, no API
  key), model `gpt-6-sol`. The OAuth credential lives in the profile's
  `auth.json`. Never commit it. Log in with
  `hermes -p intelio auth add openai-codex --type oauth --no-browser` (device code).
- Only one Telegram poller may use the bot token. Do not run the bot on the
  Mac at the same time.

## Operations

```sh
export PATH=$HOME/.local/bin:$PATH          # non-interactive SSH needs this for hermes
systemctl --user status intelio-xvfb intelio-x11vnc intelio-novnc \
  hermes-alans-way-chromium hermes-alans-way-browser hermes-gateway
systemctl --user restart hermes-gateway       # after .env, plugin or config changes
systemctl --user restart hermes-alans-way-browser   # after sidecar changes
journalctl --user -u hermes-gateway -f
cd ~/intelio/alans-way-agents && ./setup.sh --verify --profile intelio
sudo ss -tlnp                                  # expect only :22 on 0.0.0.0/[::]
```

- **noVNC:** `http://intelio-vps.<tailnet>.ts.net:6080/vnc.html` from a tailnet
  device. The VNC password is in `~/.config/intelio/vnc-password.txt` on the
  host (0600).
- **Re-run the setup.** Use the same flags as the install. Do not pass
  `--desktop-stack` unless you mean to rewrite the display units, and pass
  `--novnc-bind <vps-tailscale-ip>` if you do.

## Known gaps found during this install

- `setup.sh --profile NAME` installs and enables the plugin only in the default
  Hermes home. Hermes profiles keep separate plugin directories, so the plugin
  was also installed with `hermes -p intelio plugins install …` and
  `hermes -p intelio plugins enable alans-way`, and proactivity was enabled
  again afterwards.
- `hermes -p NAME gateway install` is refused at Hermes 5d3c059 (one host
  gateway per host), so `hermes gateway install` from the default profile is
  used instead.
- The router has no non-root default for the VPS connector path. The `/opt`
  symlink above covers it.

## Pending

- Mac SSH link over Tailscale (runbook sections 1–2). The Mac was offline in
  the tailnet at install time.
- `setup.sh --bind --profile intelio`, once Hayden's Telegram DM session exists.
- Decision on the `terminal` and `computer_use` toolsets for Telegram.
