#!/bin/sh
# Hermes CLI compatibility helpers, sourced by setup.sh (POSIX sh).
#
#   . scripts/hermes-compat.sh
#   sh scripts/hermes-compat.sh <function> [args...]    # tests / manual use
#
# Each helper names the upstream Hermes change it accommodates so a pin bump
# can re-check it (docs/intelio-vps.md, "Hermes pin bump checklist").

# Does `hermes plugins install` accept --allow-live-gateway? Upstream
# 0f0b0a12aa refuses `install --force` while the gateway is live unless it is
# passed; older Hermes has neither the guard nor the flag.
hermes_install_supports_live_gateway() {
  hermes plugins install --help 2>/dev/null | grep -q -- '--allow-live-gateway'
}

# Best-effort: is a Hermes gateway running on this host? Same signals as
# setup.sh's restart step (systemd unit, then a `gateway run` process).
hermes_gateway_running() {
  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet hermes-gateway 2>/dev/null && return 0
    systemctl --user is-active --quiet hermes-gateway 2>/dev/null && return 0
  fi
  command -v pgrep >/dev/null 2>&1 && pgrep -f "gateway run" >/dev/null 2>&1
}

# hermes_plugin_reinstall <source>
# Replace an installed plugin's code. Hermes output is NOT hidden: its refusal
# and error messages are the only diagnosis. Returns the install exit status.
# Sets HERMES_PLUGIN_RESTART_REQUIRED=1 when a gateway was live, because the
# running process keeps the old callbacks (and with --allow-live-gateway they
# may break) until it restarts.
hermes_plugin_reinstall() {
  _src=$1
  HERMES_PLUGIN_RESTART_REQUIRED=0
  if ! hermes_gateway_running; then
    hermes plugins install --force "$_src"
    return
  fi
  HERMES_PLUGIN_RESTART_REQUIRED=1
  if hermes_install_supports_live_gateway; then
    hermes plugins install --force --allow-live-gateway "$_src"
    return
  fi
  # No flag: older Hermes without the guard, or a build whose help we could
  # not read. Stop the gateway first so a guarded build cannot refuse; the
  # caller restarts it.
  hermes gateway stop || printf '  warn %s\n' "could not stop the gateway with 'hermes gateway stop'; installing anyway"
  hermes plugins install --force "$_src"
}

# hermes_allow_gateway_injection <plugin-id> [profile]
# Upstream 7b2ff7a7d4: PluginContext._gateway_injection_allowed() reads
# plugins.entries.<plugin-id>.allow_gateway_injection from the config of the
# plugin manager's own HERMES_HOME (the home the plugin was loaded from),
# never the calling profile. setup.sh installs the plugin into the default
# home, so that config must carry the flag; the bound profile also gets it so
# a profile-scoped install of the plugin reads the same answer. Idempotent.
hermes_allow_gateway_injection() {
  _plugin=$1
  _profile=${2:-}
  hermes config set "plugins.entries.$_plugin.allow_gateway_injection" true >/dev/null || return 1
  if [ -n "$_profile" ] && [ "$_profile" != default ]; then
    hermes -p "$_profile" config set "plugins.entries.$_plugin.allow_gateway_injection" true >/dev/null || return 1
  fi
}

case "${1:-}" in
  hermes_install_supports_live_gateway|hermes_gateway_running|hermes_plugin_reinstall|hermes_allow_gateway_injection)
    _fn=$1
    shift
    "$_fn" "$@"
    _rc=$?
    [ "$_fn" = hermes_plugin_reinstall ] && printf 'restart_required=%s\n' "$HERMES_PLUGIN_RESTART_REQUIRED"
    exit "$_rc"
    ;;
esac
