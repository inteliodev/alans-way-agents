#!/bin/sh
# setup.sh — one-command Alan's Way bootstrap for the host running your Hermes
# gateway (usually a VPS). Detects what's missing, installs what it can, and
# prints exact guidance for what it can't.
#
#   curl -fsSL https://raw.githubusercontent.com/inteliodev/alans-way-agents/main/setup.sh | bash -s -- --bot-id 123456789 --mac-ssh me@mymac
#   ./setup.sh --bot-id 123456789 --mac-ssh me@mymac            # from a clone
#   ./setup.sh --verify                                       # re-check an install
#   ./setup.sh --dry-run --bot-id 123456789                   # print the plan, write nothing
#
# Repo overrides (Intelio fork of capthvnsen's Alan's Way):
#   ALANS_WAY_AGENTS_REPO   default https://github.com/inteliodev/alans-way-agents
#   ALANS_WAY_AGENTS_REF    optional branch, tag, or full 40-char SHA
#   ALANS_WAY_REPO          default https://github.com/inteliodev/alans-way
#   ALANS_WAY_REF           default branch cursor/intelio-harness-layer-8db4
#                           until that branch merges; then set main or a SHA
#
# Flags: --bot-id ID --bot-name NAME --mac-ssh HOST --profile NAME
#        --mac-mcp-path PATH --mac-node-path PATH
#        --hermes-home DIR --desktop-dir DIR --skip-browser --skip-services
#        --desktop-stack --desktop-user USER --novnc-bind ADDR --vnc-password-file PATH
#        --bind --timezone IANA --restart --non-interactive --verify --dry-run
set -eu

# Single overridable clone locations. Do not clobber a value the caller set,
# including an explicit empty ALANS_WAY_AGENTS_REF (default branch).
: "${ALANS_WAY_AGENTS_REPO:=https://github.com/inteliodev/alans-way-agents}"
: "${ALANS_WAY_REPO:=https://github.com/inteliodev/alans-way}"
if [ "${ALANS_WAY_REF+set}" != set ]; then
  ALANS_WAY_REF="cursor/intelio-harness-layer-8db4"
fi
: "${ALANS_WAY_AGENTS_REF:=}"
REPO_URL="$ALANS_WAY_AGENTS_REPO"
DESKTOP_REPO_URL="$ALANS_WAY_REPO"
PLUGIN_NAME="alans-way"

BOT_ID="" BOT_NAME="" MAC_SSH="" PROFILE="" CONFIG="" TIMEZONE=""
HERMES_HOME="" DESKTOP_DIR="" MAC_MCP="" MAC_NODE=""
DESKTOP_USER="" NOVNC_BIND="127.0.0.1" VNC_PASSFILE=""
SKIP_BROWSER=0 SKIP_SERVICES=0 DO_BIND=0 DO_RESTART=0 NON_INTERACTIVE=0 VERIFY=0
DO_DESKTOP=0 DRY_RUN=0 NOVNC_BIND_SET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --bot-id) BOT_ID="$2"; shift 2;;
    --bot-name) BOT_NAME="$2"; shift 2;;
    --mac-ssh) MAC_SSH="$2"; shift 2;;
    --mac-mcp-path) MAC_MCP="$2"; shift 2;;
    --mac-node-path) MAC_NODE="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    --config) CONFIG="$2"; shift 2;;
    --hermes-home) HERMES_HOME="$2"; shift 2;;
    --desktop-dir) DESKTOP_DIR="$2"; shift 2;;
    --skip-browser) SKIP_BROWSER=1; shift;;
    --skip-services) SKIP_SERVICES=1; shift;;
    --desktop-stack) DO_DESKTOP=1; shift;;
    --desktop-user) DESKTOP_USER="$2"; shift 2;;
    --novnc-bind) NOVNC_BIND="$2"; NOVNC_BIND_SET=1; shift 2;;
    --vnc-password-file) VNC_PASSFILE="$2"; shift 2;;
    --bind) DO_BIND=1; shift;;
    --timezone) TIMEZONE="$2"; shift 2;;
    --restart) DO_RESTART=1; shift;;
    --non-interactive) NON_INTERACTIVE=1; shift;;
    --verify) VERIFY=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help)
      cat <<'EOF'
setup.sh — Alan's Way bootstrap for the Hermes gateway host (usually a VPS).
  --bot-id ID      numeric Telegram bot ID that owns browser tabs
  --bot-name NAME  display name on the agent cursor
  --mac-ssh HOST   how this host reaches your Mac over ssh (Tailscale name/IP)
  --profile NAME   Hermes profile to configure (default: main config).
                   tools enable/disable run against this profile.
  --mac-mcp-path   Mac browser-mcp.cjs, written as HERMES_WORKSPACE_MAC_MCP
  --mac-node-path  Mac node binary (SSH does not load Homebrew's PATH),
                   written as HERMES_WORKSPACE_MAC_NODE. Often
                   /opt/homebrew/bin/node
  --desktop-stack  install Xvfb, localhost x11vnc, and noVNC systemd units.
                   Opt-in. Does not apt-get install packages.
  --desktop-user   non-root account those units run as (required as root)
  --novnc-bind     websockify address (default 127.0.0.1). Loopback or a
                   Tailscale address. Public binds are refused.
  --vnc-password-file
                   x11vnc -storepasswd file installed mode 600 for the desktop user
  --bind           bind proactivity to a Telegram DM route (prompted)
  --timezone IANA  your local zone for proactivity quiet hours, e.g. Europe/Berlin
  --restart        restart the gateway at the end without asking
  --verify         check an existing install without changing anything
  --dry-run        print the install plan and write nothing
  --skip-browser / --skip-services / --non-interactive for constrained runs
Repos: ALANS_WAY_REPO, ALANS_WAY_REF (default cursor/intelio-harness-layer-8db4),
       ALANS_WAY_AGENTS_REPO, ALANS_WAY_AGENTS_REF. See docs/intelio-vps.md.
EOF
      exit 0;;
    *) echo "setup: unknown arg: $1" >&2; exit 2;;
  esac
done

[ -n "$HERMES_HOME" ] || HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*"; }
bad()  { printf '  FAIL %s\n' "$*"; FAILS=$((FAILS + 1)); }
FAILS=0

ask() { # ask <prompt> <default> — reads /dev/tty so curl|bash still prompts
  if [ "$NON_INTERACTIVE" = 1 ] || [ ! -r /dev/tty ]; then printf '%s' "$2"; return; fi
  printf '%s [%s] ' "$1" "$2" > /dev/tty
  read -r reply < /dev/tty || reply=""
  printf '%s' "${reply:-$2}"
}

confirm() { # confirm <prompt> — empty means no
  reply="$(ask "$1 [y/N]" "n")"
  case "$reply" in y|Y|yes) return 0;; *) return 1;; esac
}

have() { command -v "$1" >/dev/null 2>&1; }

safe_name() {
  case "$1" in
    ''|*[!0-9A-Za-z_.-]*) return 1;;
  esac
  return 0
}

if [ -n "$PROFILE" ] && ! safe_name "$PROFILE"; then
  echo "setup: bad --profile" >&2
  exit 2
fi
if [ -n "$DESKTOP_USER" ] && ! safe_name "$DESKTOP_USER"; then
  echo "setup: bad --desktop-user" >&2
  exit 2
fi
if [ "$DRY_RUN" = 1 ]; then
  NON_INTERACTIVE=1
fi

is_full_sha() {
  case "$1" in
    *[!0-9a-fA-F]*) return 1;;
  esac
  [ "${#1}" -eq 40 ]
}

# clone_pinned <url> <dest> <ref>. Empty ref tracks the remote default branch.
# A 40-character hex ref is fetched as that commit; anything else is a branch or tag.
clone_pinned() {
  _url=$1
  _dest=$2
  _ref=$3
  rm -rf "$_dest"
  if [ -z "$_ref" ]; then
    git clone -q --depth 1 "$_url" "$_dest"
    return
  fi
  if is_full_sha "$_ref"; then
    mkdir -p "$_dest"
    git -C "$_dest" init -q
    git -C "$_dest" remote add origin "$_url"
    git -C "$_dest" fetch -q --depth 1 origin "$_ref"
    git -C "$_dest" checkout -q --detach FETCH_HEAD
  else
    git clone -q --depth 1 --branch "$_ref" "$_url" "$_dest"
  fi
}

# update_checkout <dest> <ref> <url>. Retarget origin so an older upstream
# checkout follows the fork instead of pulling a branch that remote lacks.
update_checkout() {
  _dest=$1
  _ref=$2
  _url=$3
  if [ -n "$_url" ]; then
    git -C "$_dest" remote set-url origin "$_url"
  fi
  if [ -z "$_ref" ]; then
    git -C "$_dest" pull --ff-only -q
    return
  fi
  git -C "$_dest" fetch -q origin "$_ref"
  git -C "$_dest" checkout -q --detach FETCH_HEAD
}

# hermes -p applies only when --profile was passed. tools enable/disable must
# use this; a bare `hermes tools` call edits the default home, not the profile.
hermes_profile() {
  if [ -n "$PROFILE" ]; then
    hermes -p "$PROFILE" "$@"
  else
    hermes "$@"
  fi
}

hermes_profile_text() {
  if [ -n "$PROFILE" ]; then
    printf 'hermes -p %s' "$PROFILE"
  else
    printf '%s' "hermes"
  fi
}

# Hermes 5d3c059 writes platform_toolsets as a flow list
# (`telegram: [web, terminal]`). Older files use a block list. Both are read
# here; a later `- browser` outside the telegram entry does not count.
toolset_enabled() { # toolset_enabled <config.yaml> <name>
  _checker=""
  if [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/scripts/platform_toolsets.py" ]; then
    _checker="$REPO_DIR/scripts/platform_toolsets.py"
  elif [ -n "${SCRIPT_DIR:-}" ] && [ -f "$SCRIPT_DIR/scripts/platform_toolsets.py" ]; then
    _checker="$SCRIPT_DIR/scripts/platform_toolsets.py"
  fi
  [ -n "$_checker" ] || return 1
  python3 "$_checker" "$1" telegram "$2"
}

novnc_bind_ok() {
  _checker=""
  if [ -n "${REPO_DIR:-}" ] && [ -f "$REPO_DIR/scripts/novnc_bind.py" ]; then
    _checker="$REPO_DIR/scripts/novnc_bind.py"
  elif [ -n "${SCRIPT_DIR:-}" ] && [ -f "$SCRIPT_DIR/scripts/novnc_bind.py" ]; then
    _checker="$SCRIPT_DIR/scripts/novnc_bind.py"
  fi
  if [ -z "$_checker" ]; then
    echo "setup: novnc bind checker missing" >&2
    return 1
  fi
  python3 "$_checker" "$1"
}

# Resolve the plugin repo: beside this script when run from a clone, else clone.
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "")"
if [ -f "$SCRIPT_DIR/setup-workspace.sh" ] && [ -d "$SCRIPT_DIR/alans-way" ]; then
  REPO_DIR="$SCRIPT_DIR"
else
  REPO_DIR=""
fi

if [ -f "${REPO_DIR:-$SCRIPT_DIR}/scripts/chromium-profile.sh" ]; then
  # shellcheck disable=SC1091
  . "${REPO_DIR:-$SCRIPT_DIR}/scripts/chromium-profile.sh"
fi

if [ "$DO_DESKTOP" = 1 ] || [ "$NOVNC_BIND_SET" = 1 ]; then
  if ! novnc_bind_ok "$NOVNC_BIND"; then
    echo "setup: refusing --novnc-bind ${NOVNC_BIND} (loopback or a Tailscale address only; never a public bind)" >&2
    exit 2
  fi
fi

# ---------------------------------------------------------------- preflight
step "Preflight"
if ! have hermes; then
  bad "hermes not on PATH — install Hermes >= 0.21 first: https://github.com/NousResearch/hermes-agent"
else
  HERMES_V="$(hermes --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1 || true)"
  ok "hermes ${HERMES_V:-unknown version}"
fi
have python3 || bad "python3 required"
have node || warn "node not on PATH — required for the browser connector"
[ -d "$HERMES_HOME" ] && ok "HERMES_HOME: $HERMES_HOME" || warn "HERMES_HOME $HERMES_HOME does not exist yet (created on first hermes run)"

if [ "$DRY_RUN" = 1 ]; then
  step "Dry run"
  say "  agents repo: $ALANS_WAY_AGENTS_REPO${ALANS_WAY_AGENTS_REF:+ @ $ALANS_WAY_AGENTS_REF}"
  if [ -n "$ALANS_WAY_REF" ]; then
    say "  desktop repo: $ALANS_WAY_REPO @ $ALANS_WAY_REF"
  else
    say "  desktop repo: $ALANS_WAY_REPO @ default branch"
  fi
  say "  dry-run: $(hermes_profile_text) tools enable proactivity --platform telegram"
  say "  dry-run: $(hermes_profile_text) tools disable browser --platform telegram"
  if [ -n "$MAC_MCP" ]; then say "  HERMES_WORKSPACE_MAC_MCP: $MAC_MCP"; fi
  if [ -n "$MAC_NODE" ]; then say "  HERMES_WORKSPACE_MAC_NODE: $MAC_NODE"; fi
  if [ "$DO_DESKTOP" = 1 ]; then
    say "  desktop units: intelio-xvfb.service intelio-x11vnc.service intelio-novnc.service"
    say "  x11vnc binds localhost only; novnc bind: $NOVNC_BIND"
    say "  desktop user: ${DESKTOP_USER:-<current user, or required when root>}"
    say "  if the cups snap is installed: snap stop --disable cups"
    if command -v chromium >/dev/null 2>&1 && command -v chromium_user_data_dir >/dev/null 2>&1; then
      _chrome=$(command -v chromium || true)
      _home="$HOME"
      if [ -n "$DESKTOP_USER" ]; then
        _got=$(getent passwd "$DESKTOP_USER" 2>/dev/null | cut -d: -f6 || true)
        [ -n "$_got" ] && _home=$_got
      fi
      say "  chromium user-data-dir: $(chromium_user_data_dir "$_chrome" "$_home" "${HERMES_VPS_BROWSER_DATA:-$HOME/.local/share/hermes-alans-way/browser}/chromium")"
    else
      say "  snap Chromium profile: <desktop-home>/snap/chromium/common/hermes-alans-way"
    fi
  fi
  say "dry-run: no changes made"
  exit 0
fi

if [ "$VERIFY" = 1 ]; then
  # --verify: report state without changing anything
  step "Install state"
  have hermes && hermes plugins list 2>/dev/null | grep -q "$PLUGIN_NAME" \
    && ok "plugin '$PLUGIN_NAME' installed" || bad "plugin '$PLUGIN_NAME' not in hermes plugins list"
  CFG_HOME="${PROFILE:+$HERMES_HOME/profiles/$PROFILE}"; CFG_HOME="${CFG_HOME:-$HERMES_HOME}"
  if [ -f "$CFG_HOME/config.yaml" ]; then
    if toolset_enabled "$CFG_HOME/config.yaml" proactivity; then
      ok "proactivity toolset enabled for telegram"
    else
      warn "proactivity toolset not enabled for telegram — proactive_control won't be callable in Telegram sessions (run: $(hermes_profile_text) tools enable proactivity --platform telegram)"
    fi
    if toolset_enabled "$CFG_HOME/config.yaml" browser; then
      warn "built-in 'browser' toolset still enabled for telegram — the agent may bypass the workspace browser (run: $(hermes_profile_text) tools disable browser --platform telegram)"
    else
      ok "built-in browser toolset disabled for telegram"
    fi
  fi
  if have hermes; then
    PSTATE="$(hermes ${PROFILE:+-p "$PROFILE"} proactivity status 2>/dev/null | python3 -c 'import json,sys
try: s=json.load(sys.stdin)
except Exception: s={}
print("bound" if s.get("route_bound") else "unbound", "on" if s.get("enabled") is True else "paused")' 2>/dev/null)"
    case "$PSTATE" in
      "bound on") ok "proactivity on for the bound primary route";;
      "bound paused") warn "proactivity bound but paused — the bot never messages first (run: hermes proactivity probe, then hermes proactivity resume)";;
      *) warn "no primary route bound — proactivity is off (run: setup.sh --bind)";;
    esac
    [ "$(hermes ${PROFILE:+-p "$PROFILE"} config get "plugins.entries.$PLUGIN_NAME.allow_gateway_injection" 2>/dev/null | tail -1)" = true ] \
      && ok "gateway injection allowed for $PLUGIN_NAME" \
      || warn "gateway injection not allowed — proactive turns are dropped (run: hermes config set plugins.entries.$PLUGIN_NAME.allow_gateway_injection true)"
  fi
  [ -f "$HERMES_HOME/hooks/$PLUGIN_NAME/handler.py" ] \
    && ok "gateway hook present" || bad "gateway hook missing at $HERMES_HOME/hooks/$PLUGIN_NAME/"
  for profile_home in "$HERMES_HOME"/profiles/*/; do
    [ -d "$profile_home" ] || continue
    [ -f "$profile_home/hooks/$PLUGIN_NAME/handler.py" ] \
      && ok "gateway hook present for $(basename "$profile_home")" \
      || warn "gateway hook missing for profile $(basename "$profile_home") — a profile-scoped startup emit cannot arm the gateway"
  done
  CONN_DIR="$HOME/.local/share/hermes-alans-way/browser"
  [ -f "$CONN_DIR/connection.json" ] && ok "browser host connection file present" \
    || warn "browser host connection file absent (browser host not started?)"
  PROFS="$HERMES_HOME ${PROFILE:+$HERMES_HOME/profiles/$PROFILE}"
  for home in $PROFS; do
    if [ -f "$home/config.yaml" ] && { grep -q '>>> alans-way workspace_browser managed block >>>' "$home/config.yaml" \
        || grep -q '^  workspace_browser:' "$home/config.yaml"; }; then
      ok "workspace_browser block in $home/config.yaml"
    else
      warn "no workspace_browser block in $home/config.yaml"
    fi
  done
  [ "$FAILS" = 0 ] && say "setup: all required checks passed" || say "setup: $FAILS check(s) failed"
  exit "$([ "$FAILS" = 0 ] && echo 0 || echo 1)"
fi

# Fetch the repo when running via curl|bash.
if [ -z "$REPO_DIR" ]; then
  step "Fetching alans-way-agents"
  REPO_DIR="$HOME/.local/share/alans-way-agents"
  if [ -d "$REPO_DIR/.git" ]; then
    update_checkout "$REPO_DIR" "$ALANS_WAY_AGENTS_REF" "$REPO_URL" && ok "updated $REPO_DIR" || warn "could not update $REPO_DIR — using existing checkout"
  else
    clone_pinned "$REPO_URL" "$REPO_DIR" "$ALANS_WAY_AGENTS_REF" && ok "cloned to $REPO_DIR" || { bad "git clone failed"; exit 1; }
  fi
  if [ -f "$REPO_DIR/scripts/chromium-profile.sh" ]; then
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/chromium-profile.sh"
  fi
fi

# ---------------------------------------------------------------- telegram
step "Telegram gateway"
ENV_FILE="$HERMES_HOME/.env"
if [ -n "$PROFILE" ]; then ENV_FILE="$HERMES_HOME/profiles/$PROFILE/.env"; fi
if [ -f "$ENV_FILE" ] && grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$ENV_FILE" 2>/dev/null; then
  ok "TELEGRAM_BOT_TOKEN configured in $ENV_FILE"
elif [ -f "$HERMES_HOME/.env" ] && grep -q '^TELEGRAM_BOT_TOKEN=.\+' "$HERMES_HOME/.env" 2>/dev/null; then
  ok "TELEGRAM_BOT_TOKEN configured in $HERMES_HOME/.env"
else
  warn "No TELEGRAM_BOT_TOKEN found — the gateway has no bot to answer yet."
  say "  Hermes can create one without BotFather: run"
  say "      hermes gateway setup"
  say "  choose Telegram → 'Automatic' → scan the QR code in Telegram."
  if confirm "  Run 'hermes gateway setup' now?"; then
    hermes gateway setup || warn "gateway setup exited non-zero — re-run setup.sh after configuring"
  else
    say "  Skipping. Re-run setup.sh once Telegram is configured."
  fi
fi

# ---------------------------------------------------------------- plugin
step "Plugin"
if hermes plugins list 2>/dev/null | grep -q "$PLUGIN_NAME"; then
  if diff -rq -x __pycache__ "$REPO_DIR/$PLUGIN_NAME" "$HERMES_HOME/plugins/$PLUGIN_NAME" >/dev/null 2>&1; then
    ok "plugin already installed and current"
  else
    hermes plugins install --force "file://$REPO_DIR#$PLUGIN_NAME" >/dev/null 2>&1 \
      && ok "plugin updated from $REPO_DIR (restart the gateway to load it)" \
      || warn "could not update the installed plugin — run: hermes plugins install --force file://$REPO_DIR#$PLUGIN_NAME"
  fi
else
  hermes plugins install "file://$REPO_DIR#$PLUGIN_NAME" && ok "plugin installed from $REPO_DIR" \
    || { bad "plugin install failed"; exit 1; }
fi
hermes plugins enable "$PLUGIN_NAME" >/dev/null 2>&1 || true
# The control tool must be loaded into each messaging session's platform —
# plugin toolsets are skipped when the platform's saved list predates the
# plugin (recorded under known_plugin_toolsets). Enabling is idempotent.
# One platform today. Kept as a straight call so --profile is always applied
# (hermes -p must precede the subcommand).
hermes_profile tools enable proactivity --platform telegram >/dev/null 2>&1 \
  && ok "proactivity toolset enabled for telegram" \
  || warn "could not enable the proactivity toolset for telegram — proactive_control will not be callable in those sessions (run: $(hermes_profile_text) tools enable proactivity --platform telegram)"
# The workspace browser replaces Hermes' built-in browser tool: leaving both
# enabled lets the agent pick a different browser than the user's app.
# Reversible with: hermes tools enable browser --platform telegram
hermes_profile tools disable browser --platform telegram >/dev/null 2>&1 \
  && ok "built-in browser toolset disabled for telegram (workspace browser is the browser)" \
  || warn "could not disable the built-in browser toolset for telegram — the agent may bypass the workspace browser (run: $(hermes_profile_text) tools disable browser --platform telegram)"

# ---------------------------------------------------------------- hook
step "Gateway hook"
mkdir -p "$HERMES_HOME/hooks"
install_hook() {
  target="$1"
  mkdir -p "$target"
  if [ -f "$target/handler.py" ] && cmp -s "$REPO_DIR/hooks/$PLUGIN_NAME/handler.py" "$target/handler.py" \
      && cmp -s "$REPO_DIR/hooks/$PLUGIN_NAME/HOOK.yaml" "$target/HOOK.yaml"; then
    ok "hook current in $target"
  else
    cp -r "$REPO_DIR/hooks/$PLUGIN_NAME/." "$target/" && ok "hook installed to $target" \
      || bad "hook copy failed"
  fi
}
install_hook "$HERMES_HOME/hooks/$PLUGIN_NAME"
# Under gateway.multiplex_profiles the single gateway:startup emit can resolve
# a served profile's hooks dir instead of the launch home's — an empty profile
# hooks dir silently leaves the gateway unarmed. Seed every profile so any
# scope the emit lands in still stamps the owner marker.
for profile_home in "$HERMES_HOME"/profiles/*/; do
  [ -d "$profile_home" ] || continue
  install_hook "$profile_home/hooks/$PLUGIN_NAME"
done

# Opt-in Xvfb + localhost x11vnc + noVNC. Packages are never installed here.
install_desktop_stack() {
  [ "${DESKTOP_STACK_DONE:-0}" = 1 ] && return 0
  DESKTOP_STACK_DONE=1
  step "Desktop stack"
  if [ "$(id -u)" = 0 ] && [ -z "$DESKTOP_USER" ]; then
    bad "--desktop-stack as root needs --desktop-user (a non-root account)"
    return 1
  fi
  if [ -z "$DESKTOP_USER" ]; then
    DESKTOP_USER=$(id -un)
  fi
  if ! getent passwd "$DESKTOP_USER" >/dev/null 2>&1; then
    bad "desktop user $DESKTOP_USER does not exist — create it, then re-run (see docs/intelio-vps.md)"
    return 1
  fi
  if [ "$(id -u)" != 0 ] && [ "$DESKTOP_USER" != "$(id -un)" ]; then
    bad "only root can install units for $DESKTOP_USER — re-run with sudo"
    return 1
  fi
  # The Chromium snap pulls in cups, which listens on every interface.
  if command -v snap >/dev/null 2>&1 && snap list cups >/dev/null 2>&1; then
    if [ "$(id -u)" = 0 ]; then
      if snap stop --disable cups >/dev/null 2>&1; then
        ok "disabled the cups snap"
      else
        warn "could not disable the cups snap — run: snap stop --disable cups"
      fi
    else
      warn "cups snap is installed — run: sudo snap stop --disable cups"
    fi
  fi
  _unit_dir="/etc/systemd/system"
  _sysctl="systemctl"
  _user_line="User=$DESKTOP_USER"
  _wanted="multi-user.target"
  if [ "$(id -u)" != 0 ]; then
    _unit_dir="$HOME/.config/systemd/user"
    _sysctl="systemctl --user"
    _user_line=""
    _wanted="default.target"
    mkdir -p "$_unit_dir"
  fi
  if [ -z "$VNC_PASSFILE" ]; then
    if [ "$(id -u)" = 0 ]; then
      VNC_PASSFILE="/etc/intelio-desktop/vncpasswd"
    else
      VNC_PASSFILE="$HOME/.config/intelio/vncpasswd"
    fi
  fi
  _xvfb=$(command -v Xvfb || echo /usr/bin/Xvfb)
  _x11vnc=$(command -v x11vnc || echo /usr/bin/x11vnc)
  _websockify=$(command -v websockify || echo /usr/bin/websockify)
  _novnc_web="/usr/share/novnc"
  [ -d "$_novnc_web" ] || warn "noVNC web root $_novnc_web is missing (package novnc)"
  _tpl="$REPO_DIR/deploy"
  if [ ! -f "$_tpl/intelio-xvfb.service.in" ]; then
    bad "desktop unit templates missing in $_tpl"
    return 1
  fi
  UNIT_USER_LINE="$_user_line" UNIT_WANTED_BY="$_wanted" \
    BIN_XVFB="$_xvfb" BIN_X11VNC="$_x11vnc" BIN_WEBSOCKIFY="$_websockify" \
    NOVNC_WEB="$_novnc_web" NOVNC_BIND="$NOVNC_BIND" VNC_PASSWD_FILE="$VNC_PASSFILE" \
    python3 - "$_tpl" "$_unit_dir" <<'PY'
import os, pathlib, sys
src_dir, dest_dir = sys.argv[1], sys.argv[2]
repl = {
    "@USER_LINE@": os.environ.get("UNIT_USER_LINE", ""),
    "@WANTED_BY@": os.environ["UNIT_WANTED_BY"],
    "@XVFB@": os.environ["BIN_XVFB"],
    "@X11VNC@": os.environ["BIN_X11VNC"],
    "@WEBSOCKIFY@": os.environ["BIN_WEBSOCKIFY"],
    "@NOVNC_WEB@": os.environ["NOVNC_WEB"],
    "@NOVNC_BIND@": os.environ["NOVNC_BIND"],
    "@VNC_PASSWD_FILE@": os.environ["VNC_PASSWD_FILE"],
}
for key, value in repl.items():
    if "\n" in value or "\r" in value:
        raise SystemExit("refusing newline in unit substitution")
for name in ("intelio-xvfb", "intelio-x11vnc", "intelio-novnc"):
    text = pathlib.Path(src_dir, name + ".service.in").read_text()
    for key, value in repl.items():
        text = text.replace(key, value)
    pathlib.Path(dest_dir, name + ".service").write_text(text)
    print("  ok   wrote %s/%s.service" % (dest_dir, name))
PY
  _start_vnc=0
  if [ -f "$VNC_PASSFILE" ]; then
    chmod 600 "$VNC_PASSFILE" 2>/dev/null || true
    if [ "$(id -u)" = 0 ]; then
      chown "$DESKTOP_USER" "$VNC_PASSFILE" 2>/dev/null || true
    fi
    _start_vnc=1
  else
    warn "VNC password file missing at $VNC_PASSFILE"
    warn "create it with: x11vnc -storepasswd $VNC_PASSFILE && chmod 600 $VNC_PASSFILE"
    warn "x11vnc and noVNC were written but not started (no passwordless VNC)"
  fi
  if ! have Xvfb || ! have x11vnc || ! have websockify; then
    warn "display packages missing — apt-get install xvfb x11vnc websockify novnc"
    warn "units are installed; start them after the packages are present. See docs/intelio-vps.md"
    return 0
  fi
  # shellcheck disable=SC2086
  $_sysctl daemon-reload 2>/dev/null || true
  # shellcheck disable=SC2086
  $_sysctl enable --now intelio-xvfb.service >/dev/null 2>&1 \
    && ok "Xvfb enabled on :99" \
    || warn "could not start intelio-xvfb.service"
  if [ "$_start_vnc" = 1 ]; then
    # shellcheck disable=SC2086
    $_sysctl enable --now intelio-x11vnc.service intelio-novnc.service >/dev/null 2>&1 \
      && ok "x11vnc (localhost) and noVNC (${NOVNC_BIND}:6080) enabled" \
      || warn "could not start x11vnc/noVNC — check the password file and binaries"
  fi
  if [ "$(id -u)" != 0 ]; then
    say "  user units stop at logout. For a 24/7 desktop: sudo loginctl enable-linger $DESKTOP_USER"
  fi
}

# ------------------------------------------------------- browser host (VPS)
if [ "$SKIP_BROWSER" = 0 ]; then
  step "VPS browser host"
  [ -n "$DESKTOP_DIR" ] || DESKTOP_DIR="$([ "$(id -u)" = 0 ] && echo /opt/hermes-alans-way/browser || echo "$HOME/.local/share/hermes-alans-way/app")"
  if [ -f "$DESKTOP_DIR/desktop/scripts/browser-mcp.cjs" ]; then
    if [ -d "$DESKTOP_DIR/.git" ]; then
      OLD_LOCK="$(cksum < "$DESKTOP_DIR/desktop/package-lock.json" 2>/dev/null || true)"
      OLD_REV="$(git -C "$DESKTOP_DIR" rev-parse HEAD 2>/dev/null || true)"
      if update_checkout "$DESKTOP_DIR" "$ALANS_WAY_REF" "$DESKTOP_REPO_URL" 2>/dev/null; then
        if [ "$OLD_REV" != "$(git -C "$DESKTOP_DIR" rev-parse HEAD)" ]; then
          BROWSER_UPDATED=1; ok "updated browser scripts in $DESKTOP_DIR"
          [ "$OLD_LOCK" = "$(cksum < "$DESKTOP_DIR/desktop/package-lock.json")" ] \
            || rm -rf "$DESKTOP_DIR/desktop/node_modules"
        else
          ok "browser scripts at $DESKTOP_DIR are current"
        fi
      else
        warn "could not update $DESKTOP_DIR (local changes?) — using it as is"
      fi
    else
      warn "browser scripts at $DESKTOP_DIR are not a git checkout, so setup can't update them — move it aside and re-run to reinstall"
    fi
  else
    mkdir -p "$(dirname "$DESKTOP_DIR")"
    if [ -d "$SCRIPT_DIR/../hermes-companion/desktop/scripts" ]; then
      mkdir -p "$DESKTOP_DIR"
      cp -R "$SCRIPT_DIR/../hermes-companion/desktop" "$DESKTOP_DIR/" && ok "copied desktop checkout to $DESKTOP_DIR"
    else
      rm -rf "$DESKTOP_DIR.tmp"
      clone_pinned "$DESKTOP_REPO_URL" "$DESKTOP_DIR.tmp" "$ALANS_WAY_REF" \
        && mv "$DESKTOP_DIR.tmp" "$DESKTOP_DIR" && ok "cloned companion repo to $DESKTOP_DIR (${ALANS_WAY_REF:-default branch})" \
        || { rm -rf "$DESKTOP_DIR.tmp"; warn "could not fetch desktop repo — browser host skipped"; }
    fi
  fi
  if [ -f "$DESKTOP_DIR/desktop/scripts/browser-mcp.cjs" ] && [ ! -d "$DESKTOP_DIR/desktop/node_modules/@modelcontextprotocol" ]; then
    say "  installing connector dependencies (npm ci --omit=dev)"
    (cd "$DESKTOP_DIR/desktop" && npm ci --omit=dev --ignore-scripts >/dev/null 2>&1) \
      && ok "dependencies installed" || warn "npm ci failed — connector may not start"
  fi

  DATA_DIR="${HERMES_VPS_BROWSER_DATA:-$HOME/.local/share/hermes-alans-way/browser}"
  CHROME_HOME="$HOME"
  if [ -n "$DESKTOP_USER" ]; then
    _chrome_home=$(getent passwd "$DESKTOP_USER" 2>/dev/null | cut -d: -f6 || true)
    if [ -n "$_chrome_home" ]; then
      CHROME_HOME="$_chrome_home"
      if [ -z "${HERMES_VPS_BROWSER_DATA:-}" ] && [ "$DO_DESKTOP" = 1 ]; then
        DATA_DIR="$CHROME_HOME/.local/share/hermes-alans-way/browser"
      fi
    fi
  fi
  mkdir -p "$DATA_DIR" && chmod 700 "$DATA_DIR"
  if [ "$CHROME_HOME" != "$HOME" ] && [ "$(id -u)" = 0 ]; then
    chown "$DESKTOP_USER" "$DATA_DIR" 2>/dev/null || true
  fi
  if [ ! -f "$DATA_DIR/config.json" ]; then
    CHROMIUM="$(command -v chromium || command -v chromium-browser || command -v google-chrome || echo /snap/bin/chromium)"
    if command -v chromium_user_data_dir >/dev/null 2>&1; then
      CHROME_PROFILE=$(chromium_user_data_dir "$CHROMIUM" "$CHROME_HOME" "$DATA_DIR/chromium")
    else
      case "$CHROMIUM" in
        */snap/*) CHROME_PROFILE="$CHROME_HOME/snap/chromium/common/hermes-alans-way";;
        *) CHROME_PROFILE="$DATA_DIR/chromium";;
      esac
    fi
    mkdir -p "$CHROME_PROFILE" && chmod 700 "$CHROME_PROFILE"
    if [ "$CHROME_HOME" != "$HOME" ] && [ "$(id -u)" = 0 ]; then
      chown -R "$DESKTOP_USER" "$CHROME_PROFILE" 2>/dev/null || true
    fi
    cat > "$DATA_DIR/config.json" <<EOF
{
  "port": 9465,
  "cdpUrl": "http://127.0.0.1:9223",
  "browserCommand": "$CHROMIUM",
  "browserArgs": [
    "--user-data-dir=$CHROME_PROFILE",
    "--remote-debugging-port=9223",
    "--remote-debugging-address=127.0.0.1",
    "--no-first-run",
    "--start-maximized",
    "about:blank"
  ]
}
EOF
    chmod 600 "$DATA_DIR/config.json"
    ok "wrote $DATA_DIR/config.json (chromium: $CHROMIUM, profile: $CHROME_PROFILE)"
  else
    ok "config.json already present"
  fi

  if [ "$SKIP_SERVICES" = 0 ] && have systemctl; then
    NODE_BIN="$(command -v node || echo /usr/bin/node)"
    UNIT_DIR="/etc/systemd/system"; SYSCTL="systemctl"; UNIT_USER="User=$(id -un)"
    if [ "$(id -u)" != 0 ] && systemctl --user list-units >/dev/null 2>&1; then
      UNIT_DIR="$HOME/.config/systemd/user"; SYSCTL="systemctl --user"; UNIT_USER=""
      mkdir -p "$UNIT_DIR"
    fi
    if [ "$DO_DESKTOP" = 1 ]; then
      install_desktop_stack || true
      if [ -n "$DESKTOP_USER" ] && [ "$(id -u)" = 0 ]; then
        UNIT_USER="User=$DESKTOP_USER"
      fi
    fi
    UNIT_AFTER="network.target"
    if [ "$DO_DESKTOP" = 1 ]; then
      UNIT_AFTER="network.target intelio-xvfb.service"
    fi
    write_unit() { # write_unit <name> <exec> <extra>
      _f="$UNIT_DIR/$1"
      if [ -f "$_f" ]; then return 0; fi
      cat > "$_f" <<EOF
[Unit]
Description=Hermes Alan's Way $1
After=$UNIT_AFTER

[Service]
Type=simple
$UNIT_USER
Environment=DISPLAY=:99
Environment=HERMES_VPS_BROWSER_DATA=$DATA_DIR
$3
ExecStart=$NODE_BIN $2
Restart=on-failure
RestartSec=5

[Install]
WantedBy=$([ "$SYSCTL" = "systemctl" ] && echo multi-user.target || echo default.target)
EOF
      ok "wrote $_f"
    }
    write_unit hermes-alans-way-chromium.service "$DESKTOP_DIR/desktop/scripts/vps-chromium-host.cjs" ""
    write_unit hermes-alans-way-browser.service "$DESKTOP_DIR/desktop/scripts/vps-browser-host.cjs serve" ""
    $SYSCTL daemon-reload 2>/dev/null || true
    $SYSCTL enable --now hermes-alans-way-chromium.service hermes-alans-way-browser.service >/dev/null 2>&1 \
      && ok "browser services enabled" \
      || warn "units written but not started — start them after your X11/VNC desktop is up (needs DISPLAY=:99)"
    # Chromium keeps running so open tabs and sign-ins survive an update.
    if [ "${BROWSER_UPDATED:-0}" = 1 ]; then
      $SYSCTL restart hermes-alans-way-browser.service >/dev/null 2>&1 \
        && ok "browser host restarted on the new scripts" \
        || warn "could not restart hermes-alans-way-browser.service — restart it to load the update"
    fi
    # The observer and router read the watcher's default state file under
    # /var/lib, which only a root system unit's StateDirectory provides.
    if [ -n "$MAC_SSH" ] && [ "$SYSCTL" = "systemctl" ] && [ "$(id -u)" = 0 ]; then
      if [ ! -f "$UNIT_DIR/mac-watch.service" ]; then
        mkdir -p /etc/hermes-alans-way
        printf 'HERMES_WORKSPACE_MAC_SSH=%s\n' "$MAC_SSH" > /etc/hermes-alans-way/mac-watch.env
        cat > "$UNIT_DIR/mac-watch.service" <<EOF
[Unit]
Description=Hermes Alan's Way Mac availability watcher
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=/etc/hermes-alans-way/mac-watch.env
ExecStart=/bin/sh $REPO_DIR/alans-way/scripts/mac-watch.sh --interval 30
Restart=always
RestartSec=5
StateDirectory=hermes-alans-way

[Install]
WantedBy=multi-user.target
EOF
        ok "wrote $UNIT_DIR/mac-watch.service"
      fi
      systemctl daemon-reload 2>/dev/null || true
      systemctl enable --now mac-watch.service >/dev/null 2>&1 \
        && ok "Mac availability watcher enabled" \
        || warn "could not start mac-watch.service — the bot won't notice the Mac going on or offline"
    elif [ -n "$MAC_SSH" ]; then
      warn "Mac availability watcher not installed (needs root + systemd) — see README 'mac-watch'"
    fi
  else
    warn "systemd unavailable or skipped — see docs/vps-browser.md for manual unit setup"
  fi
fi

# ------------------------------------------------------------- desktop prereqs (guided)
if [ "$SKIP_BROWSER" = 1 ] && [ "$DO_DESKTOP" = 1 ] && [ "$SKIP_SERVICES" = 0 ]; then
  install_desktop_stack || true
fi
if [ "$SKIP_BROWSER" = 0 ]; then
  step "Desktop prerequisites (guided)"
  if [ "$DO_DESKTOP" = 1 ]; then
    ok "desktop stack requested (--desktop-stack); units run as a non-root user"
  elif have Xvfb || pgrep -f Xvfb >/dev/null 2>&1 || pgrep -f x11vnc >/dev/null 2>&1; then
    ok "an X display stack is present"
  else
    say "  no Xvfb/x11vnc detected — the display stack is guided, never auto-installed."
    say "    apt-get install xvfb x11vnc websockify novnc"
    say "  Then re-run with --desktop-stack --desktop-user USER (see docs/intelio-vps.md)."
    say "  x11vnc stays on localhost; noVNC defaults to 127.0.0.1. Browser units expect DISPLAY=:99."
  fi
fi

# ------------------------------------------------------------- workspace config
step "Workspace browser config"
if [ -z "$BOT_ID" ] && [ "$NON_INTERACTIVE" = 0 ] && [ -r /dev/tty ]; then
  BOT_ID="$(ask "  Numeric Telegram bot ID for this agent (empty to skip)" "")"
fi
if [ -n "$BOT_ID" ]; then
  set -- --bot-id "$BOT_ID"
  [ -n "$BOT_NAME" ] && set -- "$@" --bot-name "$BOT_NAME"
  [ -n "$MAC_SSH" ] && set -- "$@" --mac-ssh "$MAC_SSH"
  [ -n "$MAC_MCP" ] && set -- "$@" --mac-mcp-path "$MAC_MCP"
  [ -n "$MAC_NODE" ] && set -- "$@" --mac-node-path "$MAC_NODE"
  if [ -n "$PROFILE" ]; then set -- "$@" --profile "$PROFILE"
  elif [ -n "$CONFIG" ]; then set -- "$@" --config "$CONFIG"
  else set -- "$@" --config "$HERMES_HOME/config.yaml"; fi
  sh "$REPO_DIR/setup-workspace.sh" "$@" && ok "workspace_browser configured" || bad "setup-workspace.sh failed"
else
  say "  skipped (no --bot-id). Re-run with --bot-id <numeric-telegram-bot-id>."
fi

# ------------------------------------------------------------- gateway restart
step "Gateway restart"
GW_HINT="A running gateway holds already-imported code — restart it to load the plugin."
if [ "$DO_RESTART" = 1 ] || { [ "$NON_INTERACTIVE" = 0 ] && [ -r /dev/tty ] && confirm "  Restart the Hermes gateway now?"; }; then
  if hermes gateway restart >/dev/null 2>&1; then
    ok "hermes gateway restart"
  elif systemctl is-active --quiet hermes-gateway 2>/dev/null; then
    systemctl restart hermes-gateway && ok "systemctl restart hermes-gateway"
  elif systemctl --user is-active --quiet hermes-gateway 2>/dev/null; then
    systemctl --user restart hermes-gateway && ok "systemctl --user restart hermes-gateway"
  elif pgrep -f "gateway run" >/dev/null 2>&1; then
    warn "a supervisor-managed gateway is running — restart it through its owner (PM/launchd), not here"
  else
    warn "no live gateway found — $GW_HINT"
  fi
else
  say "  $GW_HINT"
fi

# ------------------------------------------------------------- bind proactivity
step "Primary bot binding"
ROUTES="$(python3 - "$HERMES_HOME" <<'PY'
import json, sys, pathlib
home = pathlib.Path(sys.argv[1])
for idx in sorted(home.glob("sessions/sessions.json")) + sorted(home.glob("profiles/*/sessions/sessions.json")):
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        continue
    file_profile = idx.parent.parent.name if "/profiles/" in str(idx) else "default"
    for key, entry in (data.items() if isinstance(data, dict) else []):
        if (isinstance(entry, dict) and entry.get("platform") == "telegram"
                and entry.get("chat_type") == "dm" and entry.get("suspended") is not True):
            agent = key.split(":")[1] if key.count(":") >= 2 else file_profile
            print(f"{file_profile}\t{agent}\t{key}")
PY
)"
if [ -z "$ROUTES" ]; then
  say "  no Telegram DM sessions yet. Message your bot once on Telegram,"
  say "  then re-run:  setup.sh --bind${PROFILE:+ --profile $PROFILE}"
else
  say "  existing Telegram routes (pick which bot is the primary):"
  echo "$ROUTES" | python3 -c 'import sys
for i, line in enumerate(sys.stdin, 1):
    p, a, k = line.rstrip("\n").split("\t", 2)
    print(f"    [{i}] {a}  (home profile: {p})")'
  if [ "$DO_BIND" = 1 ] && [ "$NON_INTERACTIVE" = 1 ]; then
    if [ "$(echo "$ROUTES" | wc -l)" = 1 ]; then
      SEL=1
    else
      bad "--bind --non-interactive needs exactly one route; found $(echo "$ROUTES" | wc -l). Bind manually with: hermes proactivity bind --session-key <key>"
      SEL=""
    fi
  else
    SEL="$(ask "  Bind a primary route? [1..N/N]" "N")"
  fi
  case "$SEL" in
    ''|n|N|no) say "  skipped binding — proactivity stays paused.";;
    *[!0-9]*) warn "invalid selection — bind manually with: hermes proactivity bind --session-key <key>";;
    *) SEL_KEY="$(echo "$ROUTES" | sed -n "${SEL}p" | cut -f3)"
       SEL_PROF="$(echo "$ROUTES" | sed -n "${SEL}p" | cut -f1)"
       SEL_AGENT="$(echo "$ROUTES" | sed -n "${SEL}p" | cut -f2)"
       if [ -n "$SEL_KEY" ]; then
         if [ "$SEL_PROF" = "default" ]; then BIND_PROF=""; else BIND_PROF="-p $SEL_PROF"; fi
         if hermes $BIND_PROF proactivity bind --session-key "$SEL_KEY" >/dev/null 2>&1; then
           ok "bound primary route: $SEL_AGENT"
           if [ -n "$TIMEZONE" ]; then
             hermes $BIND_PROF proactivity configure --settings "{\"timezone\": \"$TIMEZONE\"}" >/dev/null 2>&1 \
               && ok "quiet hours use $TIMEZONE" \
               || warn "could not set timezone $TIMEZONE — quiet hours stay on the default zone"
           else
             warn "no --timezone given — quiet hours use the default zone (America/Denver)"
           fi
           # Binding always leaves policy paused; turning it on grants the gateway
           # injection permission, so it stays an explicit human choice.
           ON="$(ask "  Turn proactive messages on now? The bot may then message this chat first. [y/N]" "N")"
           case "$ON" in
             y|Y|yes)
               if hermes $BIND_PROF config set "plugins.entries.$PLUGIN_NAME.allow_gateway_injection" true >/dev/null 2>&1 \
                   && hermes $BIND_PROF proactivity probe >/dev/null 2>&1 \
                   && hermes $BIND_PROF proactivity resume >/dev/null 2>&1; then
                 ok "proactivity on"
               else
                 bad "could not turn proactivity on — run: hermes $BIND_PROF proactivity probe, then hermes $BIND_PROF proactivity resume"
               fi;;
             *) say "  proactivity stays paused — turn it on later with /proactivity resume";;
           esac
         else
           bad "bind failed — run manually: hermes $BIND_PROF proactivity bind --session-key <key>"
         fi
       else
         warn "invalid selection — bind manually with: hermes proactivity bind --session-key <key>"
       fi;;
  esac
fi

# ---------------------------------------------------------------- verify
step "Verify"
have hermes && hermes plugins list 2>/dev/null | grep -q "$PLUGIN_NAME" && ok "plugin enabled" || bad "plugin missing"
if [ -n "$BOT_ID" ]; then
  CFG="$CONFIG"; [ -n "$CFG" ] || { [ -n "$PROFILE" ] && CFG="$HERMES_HOME/profiles/$PROFILE/config.yaml" || CFG="$HERMES_HOME/config.yaml"; }
  [ -f "$CFG" ] && { grep -q '>>> alans-way workspace_browser managed block >>>' "$CFG" \
    || grep -q '^  workspace_browser:' "$CFG"; } \
    && ok "workspace_browser block in $CFG" || warn "workspace_browser block not found in $CFG"
fi
[ -f "$HOME/.local/share/hermes-alans-way/browser/connection.json" ] && ok "browser host running" \
  || warn "browser host not running yet (start the service or desktop session)"

step "Done"
cat <<'EOF'
  Next:
  • On your Mac: open Hermes — Alan's Way → Settings → Agent setup → save this
    Mac's SSH address → Test agent path.
  • In Telegram: message your primary bot — /proactivity status should report
    'bound' once you've bound a route, and /proactivity resume turns it on.
  • Browser work routes to the Mac while it's reachable, else the VPS host.
EOF
[ "$FAILS" = 0 ] && exit 0 || { say "setup: $FAILS check(s) failed — see above."; exit 1; }
