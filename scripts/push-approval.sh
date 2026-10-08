#!/bin/sh
# intelio push approval: install / remove / check the push guard (POSIX sh).
#
#   sh scripts/push-approval.sh install   [--profiles intelio,prc,...] [--hermes-home DIR] [--no-hermes] [--dry-run]
#   sh scripts/push-approval.sh uninstall [--profiles ...] [--hermes-home DIR] [--no-hermes] [--dry-run]
#   sh scripts/push-approval.sh status    [--profiles ...] [--hermes-home DIR]
#
# What install does (docs/push-approval.md has the why):
#   1. copies push-approval/push_guard.py to ~/.config/intelio/push-guard/ and writes
#      hooks/ (pre-push guard + pass-through dispatchers for every other git hook) and
#      bin/gh, bin/git shims;
#   2. points `git config --global core.hooksPath` at hooks/, remembering any previous value
#      so its hooks keep running (and uninstall puts it back);
#   3. installs and enables the push-approval Hermes plugin in each profile
#      (`hermes -p P plugins install --enable`); the gateway picks it up on its next
#      (graceful) restart. approvals.mode is not touched.
# Idempotent. --dry-run prints the plan and changes nothing. No secret is read or printed.

set -u

REPO_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
# shellcheck source=scripts/hermes-compat.sh
. "$REPO_DIR/scripts/hermes-compat.sh"
PLUGIN_NAME=push-approval
GUARD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/intelio/push-guard"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/intelio"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
PROFILES="default,intelio,prc,alignment,hhp,arlp"
DO_HERMES=1
DRY=0
ACTION="${1:-}"
[ $# -gt 0 ] && shift

while [ $# -gt 0 ]; do
  case "$1" in
    --profiles) PROFILES="${2:-}"; shift 2;;
    --profiles=*) PROFILES="${1#*=}"; shift;;
    --hermes-home) HERMES_HOME_DIR="${2:-}"; shift 2;;
    --hermes-home=*) HERMES_HOME_DIR="${1#*=}"; shift;;
    --no-hermes) DO_HERMES=0; shift;;
    --dry-run) DRY=1; shift;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) printf 'unknown option: %s\n' "$1" >&2; exit 2;;
  esac
done

ok()   { printf '  ok   %s\n' "$*"; }
warn() { printf '  warn %s\n' "$*"; }
bad()  { printf '  FAIL %s\n' "$*"; FAILED=1; }
plan() { printf '  plan %s\n' "$*"; }
FAILED=0

run() {
  if [ "$DRY" = 1 ]; then plan "$*"; return 0; fi
  "$@"
}

PYTHON=$(command -v python3 || true)

profile_ok() {
  case "$1" in ''|*[!0-9A-Za-z_.-]*|.*) return 1;; esac
  return 0
}

hermes_for() {
  _p=$1; shift
  if [ "$_p" = default ]; then
    HERMES_HOME="$HERMES_HOME_DIR" hermes "$@"
  else
    HERMES_HOME="$HERMES_HOME_DIR" hermes -p "$_p" "$@"
  fi
}

profile_home() {
  if [ "$1" = default ]; then printf '%s' "$HERMES_HOME_DIR"; else printf '%s/profiles/%s' "$HERMES_HOME_DIR" "$1"; fi
}

write_file() {
  # write_file <path> <mode>; content on stdin
  if [ "$DRY" = 1 ]; then plan "write $1"; cat >/dev/null; return 0; fi
  cat > "$1.tmp.$$" && chmod "$2" "$1.tmp.$$" && mv -f "$1.tmp.$$" "$1"
}

install_guard() {
  printf '%s\n' "push guard (git hooks + gh/git shims)"
  if [ -z "$PYTHON" ]; then bad "python3 not found; the guard needs it"; return; fi
  run mkdir -p "$GUARD_DIR/hooks" "$GUARD_DIR/bin" "$STATE_DIR/push-grants"
  run chmod 700 "$STATE_DIR/push-grants"
  if [ "$DRY" = 1 ]; then plan "copy push_guard.py"; else
    cp "$REPO_DIR/push-approval/push_guard.py" "$GUARD_DIR/push_guard.py.tmp.$$" \
      && chmod 755 "$GUARD_DIR/push_guard.py.tmp.$$" \
      && mv -f "$GUARD_DIR/push_guard.py.tmp.$$" "$GUARD_DIR/push_guard.py" \
      || bad "could not copy push_guard.py"
  fi
  write_file "$GUARD_DIR/hooks/pre-push" 755 <<EOF
#!/bin/sh
# intelio push guard: an agent's push needs Hayden's approval (scripts/push-approval.sh).
exec "$PYTHON" "$GUARD_DIR/push_guard.py" hook pre-push "\$@"
EOF
  for _h in applypatch-msg pre-applypatch post-applypatch pre-commit pre-merge-commit \
            prepare-commit-msg commit-msg post-commit pre-rebase post-checkout post-merge \
            pre-auto-gc post-rewrite sendemail-validate post-index-change push-to-checkout \
            reference-transaction; do
    write_file "$GUARD_DIR/hooks/$_h" 755 <<'EOF'
#!/bin/sh
# intelio push guard pass-through: core.hooksPath points here, so run the repo's own hook
# and the hooks path that was configured before the guard (previous-hooks-path).
name=$(basename "$0")
guard=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
input=$(mktemp) || exit 1
trap 'rm -f "$input"' EXIT
cat > "$input"
common=$(git rev-parse --git-common-dir 2>/dev/null)
if [ -n "$common" ] && [ -x "$common/hooks/$name" ]; then
  "$common/hooks/$name" "$@" < "$input" || exit $?
fi
prev=$(cat "$guard/previous-hooks-path" 2>/dev/null)
case "$prev" in "~/"*) prev="$HOME/${prev#\~/}";; esac
if [ -n "$prev" ] && [ "$prev" != "$guard/hooks" ] && [ -x "$prev/$name" ]; then
  "$prev/$name" "$@" < "$input" || exit $?
fi
exit 0
EOF
  done
  for _prog in gh git; do
    write_file "$GUARD_DIR/bin/$_prog" 755 <<EOF
#!/bin/sh
# intelio push guard shim for $_prog (agents' PATH only; scripts/push-approval.sh).
# Only push-shaped calls reach Python; everything else runs the real $_prog directly.
for a in "\$@"; do
  case "\$a" in
    push|merge|sync|create|api|--no-verify) exec "$PYTHON" "$GUARD_DIR/push_guard.py" $_prog "\$@";;
  esac
done
self_dir=\$(CDPATH='' cd -- "\$(dirname -- "\$0")" && pwd)
old_ifs=\$IFS; IFS=:
for d in \$PATH; do
  [ "\$d" = "\$self_dir" ] && continue
  if [ -x "\$d/$_prog" ] && [ ! -d "\$d/$_prog" ]; then IFS=\$old_ifs; exec "\$d/$_prog" "\$@"; fi
done
IFS=\$old_ifs
exec /usr/bin/$_prog "\$@"
EOF
  done
  _cur=$(git config --global --get core.hooksPath 2>/dev/null || true)
  if [ -n "$_cur" ] && [ "$_cur" != "$GUARD_DIR/hooks" ]; then
    printf '%s\n' "$_cur" | write_file "$GUARD_DIR/previous-hooks-path" 600
    ok "previous core.hooksPath kept and chained: $_cur"
  fi
  if [ "$_cur" = "$GUARD_DIR/hooks" ]; then
    ok "core.hooksPath already points at the guard"
  else
    run git config --global core.hooksPath "$GUARD_DIR/hooks" && ok "core.hooksPath -> $GUARD_DIR/hooks"
  fi
}

uninstall_guard() {
  printf '%s\n' "push guard"
  _cur=$(git config --global --get core.hooksPath 2>/dev/null || true)
  _prev=$(cat "$GUARD_DIR/previous-hooks-path" 2>/dev/null || true)
  if [ "$_cur" = "$GUARD_DIR/hooks" ]; then
    if [ -n "$_prev" ]; then
      run git config --global core.hooksPath "$_prev" && ok "core.hooksPath restored to $_prev"
    else
      run git config --global --unset core.hooksPath && ok "core.hooksPath unset"
    fi
  else
    ok "core.hooksPath does not point at the guard (left as is)"
  fi
  run rm -rf "$GUARD_DIR/hooks" "$GUARD_DIR/bin" "$GUARD_DIR/push_guard.py" "$GUARD_DIR/previous-hooks-path"
  run rm -rf "$STATE_DIR/push-grants"
  ok "guard files removed (the decision log $STATE_DIR/push-guard.jsonl is kept)"
}

each_profile() {
  # each_profile <function>
  _old_ifs=$IFS; IFS=,
  for _p in $PROFILES; do
    IFS=$_old_ifs
    if ! profile_ok "$_p"; then bad "bad profile name: $_p"; continue; fi
    if [ "$_p" != default ] && [ ! -d "$(profile_home "$_p")" ]; then warn "profile $_p: no such profile (skipped)"; continue; fi
    "$1" "$_p"
  done
  IFS=$_old_ifs
}

plugin_install_one() {
  # --force: Hermes' install scan rates this plugin "caution" because the guard reads its own
  # /proc/self/cgroup, runs git, and names sudo/env as wrappers it looks through; a community
  # (file://) source with that verdict needs --force. Review: push-approval/push_guard.py.
  _src="file://$REPO_DIR#$PLUGIN_NAME"
  _live=""
  if hermes_gateway_running && hermes_install_supports_live_gateway; then _live="--allow-live-gateway"; fi
  if [ -f "$(profile_home "$1")/plugins/$PLUGIN_NAME/plugin.yaml" ] \
    && diff -rq -x __pycache__ "$REPO_DIR/$PLUGIN_NAME" "$(profile_home "$1")/plugins/$PLUGIN_NAME" >/dev/null 2>&1; then
    ok "profile $1: plugin up to date"
  else
    # shellcheck disable=SC2086
    run hermes_for "$1" plugins install --force $_live "$_src" </dev/null \
      && ok "profile $1: plugin installed (takes effect on the next gateway restart)" \
      || bad "profile $1: plugin install failed (Hermes' message is above; with an old Hermes, stop the gateway first)"
  fi
  run hermes_for "$1" plugins enable "$PLUGIN_NAME" </dev/null >/dev/null \
    && ok "profile $1: plugin enabled (plugins.enabled; approvals.mode untouched)" \
    || bad "profile $1: could not enable the plugin"
}

plugin_uninstall_one() {
  run hermes_for "$1" plugins disable "$PLUGIN_NAME" </dev/null >/dev/null \
    && ok "profile $1: plugin disabled" || warn "profile $1: plugin was not enabled"
}

plugin_status_one() {
  if [ -f "$(profile_home "$1")/plugins/$PLUGIN_NAME/plugin.yaml" ]; then
    if hermes_for "$1" config get plugins.enabled 2>/dev/null | grep -q "$PLUGIN_NAME"; then
      ok "profile $1: plugin installed and enabled"
    else
      bad "profile $1: plugin installed but not enabled"
    fi
  else
    bad "profile $1: plugin not installed"
  fi
}

status() {
  printf '%s\n' "push guard"
  _cur=$(git config --global --get core.hooksPath 2>/dev/null || true)
  [ "$_cur" = "$GUARD_DIR/hooks" ] && ok "core.hooksPath -> guard" || bad "core.hooksPath is '${_cur:-unset}', not the guard"
  for _f in push_guard.py hooks/pre-push hooks/pre-commit bin/gh bin/git; do
    [ -x "$GUARD_DIR/$_f" ] && ok "$_f" || bad "$_f missing"
  done
  if [ -n "$PYTHON" ] && [ -f "$GUARD_DIR/push_guard.py" ]; then
    ok "this shell counts as: $("$PYTHON" "$GUARD_DIR/push_guard.py" context)"
  fi
  _n=$(find "$STATE_DIR/push-grants" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  ok "open grants: ${_n:-0}"
  if [ -f "$STATE_DIR/push-guard.jsonl" ]; then
    ok "last decisions:"
    tail -n 5 "$STATE_DIR/push-guard.jsonl" | sed 's/^/         /'
  fi
}

case "$ACTION" in
  install)
    install_guard
    if [ "$DO_HERMES" = 1 ]; then
      if command -v hermes >/dev/null 2>&1; then
        printf '%s\n' "Hermes plugin ($PLUGIN_NAME)"
        each_profile plugin_install_one
        warn "restart the gateway when idle (systemctl --user reload hermes-gateway = graceful) to load the plugin"
      else
        warn "hermes not on PATH; plugin not installed (git guard is active)"
      fi
    fi
    ;;
  uninstall)
    [ "$DO_HERMES" = 1 ] && command -v hermes >/dev/null 2>&1 && each_profile plugin_uninstall_one
    uninstall_guard
    ;;
  status)
    status
    if [ "$DO_HERMES" = 1 ] && command -v hermes >/dev/null 2>&1; then
      printf '%s\n' "Hermes plugin ($PLUGIN_NAME)"
      each_profile plugin_status_one
    fi
    ;;
  *) sed -n '2,20p' "$0"; exit 2;;
esac
exit "$FAILED"
