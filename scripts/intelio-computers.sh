#!/bin/sh
# "Your computers (intelio node)": point Hermes profiles at the intelio relay's
# aggregated MCP server. Sourced by setup.sh (POSIX sh); also runnable:
#
#   sh scripts/intelio-computers.sh apply  <hermes-home> <profiles-csv>
#   sh scripts/intelio-computers.sh verify <hermes-home> <profiles-csv>
#
# Hermes-native wiring only: one `mcp_servers.intelio_computers` entry per
# profile, written with `hermes [-p P] config set`, plus the bearer token in
# that profile's .env. No Hermes source, plugin hook or patch is involved, so a
# Hermes update cannot silently drop it (docs/intelio-vps.md, "Your computers").
#
# Hermes behaviour this relies on (pinned fork d9ef91e, re-check on a pin bump):
#   tools/mcp_tool_config.py:541  _load_mcp_config reads mcp_servers per profile
#   tools/mcp_tool_config.py:448  ${VAR} in url/headers resolves from the
#                                 profile's own .env (secret scope)
#   hermes_cli/config.py:3582     `config set` writes dotted mcp_servers.* keys
#                                 (mcp_servers is an open dict, config.py:3203)
#
# The token value is never printed, passed on a command line, or logged.

INTELIO_COMPUTERS_SERVER="intelio_computers"
INTELIO_COMPUTERS_URL="${INTELIO_NODES_MCP_URL:-http://127.0.0.1:8645/mcp}"
INTELIO_COMPUTERS_ENV_KEY="INTELIO_NODES_MCP_TOKEN"
# The literal Hermes env reference; single quotes keep the shell from expanding it.
# shellcheck disable=SC2016
INTELIO_COMPUTERS_AUTH='Bearer ${INTELIO_NODES_MCP_TOKEN}'
INTELIO_COMPUTERS_TIMEOUT=300
INTELIO_COMPUTERS_CONNECT_TIMEOUT=30
INTELIO_COMPUTERS_CHANGED=0

intelio_computers_token_file() {
  printf '%s' "${INTELIO_NODES_MCP_TOKEN_FILE:-$HOME/.config/intelio/nodes-mcp.token}"
}

# intelio_computers_profile_home <hermes-home> <profile>
intelio_computers_profile_home() {
  if [ "$2" = default ]; then
    printf '%s' "$1"
  else
    printf '%s/profiles/%s' "$1" "$2"
  fi
}

# hermes for one profile; "default" is the bare home (no -p).
intelio_computers_hermes() {
  _p=$1
  shift
  if [ "$_p" = default ]; then
    HERMES_HOME="$INTELIO_COMPUTERS_HOME" hermes "$@"
  else
    HERMES_HOME="$INTELIO_COMPUTERS_HOME" hermes -p "$_p" "$@"
  fi
}

intelio_computers_profile_ok() {
  case "$1" in
    ''|*[!0-9A-Za-z_.-]*|.*) return 1;;
  esac
  return 0
}

# intelio_computers_write_env <env-file> <token-file>
# Sets INTELIO_NODES_MCP_TOKEN in the profile .env from the token file. Other
# lines are kept byte for byte; the file is replaced atomically and left mode
# 600. Prints "changed" or "unchanged", never the value.
intelio_computers_write_env() {
  python3 - "$1" "$2" "$INTELIO_COMPUTERS_ENV_KEY" <<'PY'
import os, re, sys, tempfile
env_path, token_path, key = sys.argv[1:4]
with open(token_path, encoding="utf-8") as fh:
    token = fh.read().strip()
if not token or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
    print("bad-token")
    sys.exit(3)
line_re = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=")
try:
    with open(env_path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
except FileNotFoundError:
    lines = []
wanted = "%s=%s\n" % (key, token)
out, seen = [], False
for line in lines:
    if line_re.match(line):
        if not seen:
            out.append(wanted)
            seen = True
        continue
    out.append(line)
if not seen:
    if out and not out[-1].endswith("\n"):
        out[-1] += "\n"
    out.append(wanted)
changed = out != lines
if changed:
    directory = os.path.dirname(os.path.abspath(env_path))
    fd, tmp = tempfile.mkstemp(prefix=".env.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write("".join(out))
        os.replace(tmp, env_path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
os.chmod(env_path, 0o600)
print("changed" if changed else "unchanged")
PY
}

# intelio_computers_env_state <env-file> <token-file> -> match|stale|missing|no-file
intelio_computers_env_state() {
  python3 - "$1" "$2" "$INTELIO_COMPUTERS_ENV_KEY" <<'PY'
import os, re, stat, sys
env_path, token_path, key = sys.argv[1:4]
if not os.path.exists(env_path):
    print("no-file"); sys.exit(0)
token = open(token_path, encoding="utf-8").read().strip() if os.path.exists(token_path) else None
line_re = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=\s*(.*?)\s*$")
value = None
for line in open(env_path, encoding="utf-8"):
    m = line_re.match(line)
    if m:
        value = m.group(1).strip("'\"")
mode = stat.S_IMODE(os.stat(env_path).st_mode)
if value is None:
    print("missing")
elif token is not None and value != token:
    print("stale")
elif mode & 0o077:
    print("mode")
else:
    print("match")
PY
}

intelio_computers_config_sum() {
  if [ -f "$1" ]; then cksum < "$1"; else echo none; fi
}

# intelio_computers_apply <hermes-home> <profiles-csv>
# Sets INTELIO_COMPUTERS_CHANGED=1 when any .env or config.yaml changed.
intelio_computers_apply() {
  INTELIO_COMPUTERS_HOME=$1
  _csv=$2
  INTELIO_COMPUTERS_CHANGED=0
  _tok=$(intelio_computers_token_file)
  if [ ! -f "$_tok" ]; then
    printf '  note %s\n' "no relay token at $_tok — skipped (install the intelio relay, then re-run setup.sh)"
    return 0
  fi
  _rc=0
  _old_ifs=$IFS
  IFS=,
  set -f
  # shellcheck disable=SC2086
  set -- $_csv
  set +f
  IFS=$_old_ifs
  for _p in "$@"; do
    _p=$(printf '%s' "$_p" | tr -d ' ')
    [ -n "$_p" ] || continue
    if ! intelio_computers_profile_ok "$_p"; then
      printf '  FAIL %s\n' "bad profile name in --computers-profiles: $_p"
      _rc=1
      continue
    fi
    _home=$(intelio_computers_profile_home "$INTELIO_COMPUTERS_HOME" "$_p")
    if [ ! -d "$_home" ]; then
      printf '  warn %s\n' "profile $_p has no home at $_home — skipped (create it with: hermes profile create $_p)"
      continue
    fi
    _state=$(intelio_computers_write_env "$_home/.env" "$_tok") || {
      printf '  FAIL %s\n' "could not write $INTELIO_COMPUTERS_ENV_KEY to $_home/.env ($_state)"
      _rc=1
      continue
    }
    [ "$_state" = changed ] && INTELIO_COMPUTERS_CHANGED=1
    _before=$(intelio_computers_config_sum "$_home/config.yaml")
    _key="mcp_servers.$INTELIO_COMPUTERS_SERVER"
    if intelio_computers_hermes "$_p" config set "$_key.url" "$INTELIO_COMPUTERS_URL" >/dev/null \
        && intelio_computers_hermes "$_p" config set "$_key.headers.Authorization" "$INTELIO_COMPUTERS_AUTH" >/dev/null \
        && intelio_computers_hermes "$_p" config set "$_key.timeout" "$INTELIO_COMPUTERS_TIMEOUT" >/dev/null \
        && intelio_computers_hermes "$_p" config set "$_key.connect_timeout" "$INTELIO_COMPUTERS_CONNECT_TIMEOUT" >/dev/null; then
      [ "$_before" = "$(intelio_computers_config_sum "$_home/config.yaml")" ] || INTELIO_COMPUTERS_CHANGED=1
      printf '  ok   %s\n' "profile $_p: $INTELIO_COMPUTERS_SERVER -> $INTELIO_COMPUTERS_URL (token in $_home/.env, $_state)"
    else
      printf '  FAIL %s\n' "profile $_p: hermes config set $_key.* failed"
      _rc=1
    fi
  done
  return "$_rc"
}

# intelio_computers_probe <url> -> HTTP status of an unauthenticated POST, or "none"
intelio_computers_probe() {
  python3 - "$1" <<'PY'
import sys, urllib.request, urllib.error
body = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
req = urllib.request.Request(sys.argv[1], data=body, method="POST", headers={
    "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(resp.status)
except urllib.error.HTTPError as exc:
    print(exc.code)
except Exception:
    print("none")
PY
}

# intelio_computers_verify <hermes-home> <profiles-csv>. Reports only; returns
# 1 when a listed profile has a broken entry (never because the relay is down).
intelio_computers_verify() {
  INTELIO_COMPUTERS_HOME=$1
  _csv=$2
  _tok=$(intelio_computers_token_file)
  _rc=0
  if [ ! -f "$_tok" ]; then
    printf '  note %s\n' "no relay token at $_tok — computers not wired (relay not installed?)"
    return 0
  fi
  _old_ifs=$IFS
  IFS=,
  set -f
  # shellcheck disable=SC2086
  set -- $_csv
  set +f
  IFS=$_old_ifs
  for _p in "$@"; do
    _p=$(printf '%s' "$_p" | tr -d ' ')
    [ -n "$_p" ] || continue
    intelio_computers_profile_ok "$_p" || continue
    _home=$(intelio_computers_profile_home "$INTELIO_COMPUTERS_HOME" "$_p")
    [ -d "$_home" ] || { printf '  warn %s\n' "profile $_p: no home at $_home"; continue; }
    case "$(intelio_computers_env_state "$_home/.env" "$_tok")" in
      match) printf '  ok   %s\n' "profile $_p: $INTELIO_COMPUTERS_ENV_KEY in .env (mode 600, matches the relay token)";;
      stale) printf '  FAIL %s\n' "profile $_p: $INTELIO_COMPUTERS_ENV_KEY in .env differs from the relay token — re-run setup.sh"; _rc=1;;
      mode) printf '  FAIL %s\n' "profile $_p: $_home/.env is readable by others — chmod 600 it"; _rc=1;;
      *) printf '  FAIL %s\n' "profile $_p: $INTELIO_COMPUTERS_ENV_KEY missing from $_home/.env — re-run setup.sh"; _rc=1;;
    esac
    _key="mcp_servers.$INTELIO_COMPUTERS_SERVER"
    _url=$(intelio_computers_hermes "$_p" config get "$_key.url" 2>/dev/null | tail -1)
    _to=$(intelio_computers_hermes "$_p" config get "$_key.timeout" 2>/dev/null | tail -1)
    _cto=$(intelio_computers_hermes "$_p" config get "$_key.connect_timeout" 2>/dev/null | tail -1)
    _en=$(intelio_computers_hermes "$_p" config get "$_key.enabled" 2>/dev/null | tail -1)
    # `config get` expands ${VAR} from this profile's .env and masks
    # secret-shaped values ("Bear...abcd"); compare inside python and print
    # only the verdict.
    _auth=$(intelio_computers_hermes "$_p" config get "$_key.headers.Authorization" 2>/dev/null \
      | TOKFILE="$_tok" python3 -c 'import os,sys
lines=[l.rstrip("\n") for l in sys.stdin if l.strip()]
v=lines[-1] if lines else ""
p=os.environ["TOKFILE"]
t=open(p,encoding="utf-8").read().strip()
masked=v.startswith("Bear") and "..." in v
if v=="Bearer "+t or (masked and len(t)>=8 and v.endswith(t[-4:])): print("resolved")
elif "INTELIO_NODES_MCP_TOKEN" in v or (masked and v.endswith("KEN}")): print("literal")
elif not v or v=="null": print("missing")
else: print("other")' 2>/dev/null)
    if [ "$_url" = "$INTELIO_COMPUTERS_URL" ] && [ "$_to" = "$INTELIO_COMPUTERS_TIMEOUT" ] \
        && [ "$_cto" = "$INTELIO_COMPUTERS_CONNECT_TIMEOUT" ] && [ "$_auth" = resolved ]; then
      printf '  ok   %s\n' "profile $_p: mcp_servers.$INTELIO_COMPUTERS_SERVER configured; Authorization resolves from this profile's .env"
    else
      printf '  FAIL %s\n' "profile $_p: mcp_servers.$INTELIO_COMPUTERS_SERVER incomplete (url=${_url:-unset} timeout=${_to:-unset} connect_timeout=${_cto:-unset} auth=${_auth:-unset}) — re-run setup.sh"
      _rc=1
    fi
    case "$_en" in
      false|False) printf '  note %s\n' "profile $_p: $INTELIO_COMPUTERS_SERVER is disabled (enabled: false)";;
    esac
  done
  _code=$(intelio_computers_probe "$INTELIO_COMPUTERS_URL")
  case "$_code" in
    401) printf '  ok   %s\n' "relay MCP at $INTELIO_COMPUTERS_URL refuses an unauthenticated POST (401)";;
    none) printf '  warn %s\n' "relay MCP at $INTELIO_COMPUTERS_URL not reachable — is intelio-pwa running?";;
    *) printf '  warn %s\n' "relay MCP at $INTELIO_COMPUTERS_URL answered $_code to an unauthenticated POST (expected 401)";;
  esac
  return "$_rc"
}

case "${1:-}" in
  apply|verify)
    _fn="intelio_computers_$1"
    shift
    [ $# -ge 1 ] || { echo "usage: intelio-computers.sh apply|verify <hermes-home> [profiles-csv]" >&2; exit 2; }
    "$_fn" "$1" "${2:-intelio}"
    _rc=$?
    [ "$_fn" = intelio_computers_apply ] && printf 'changed=%s\n' "$INTELIO_COMPUTERS_CHANGED"
    exit "$_rc"
    ;;
esac
