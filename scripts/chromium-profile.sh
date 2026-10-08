#!/bin/sh
# Chromium --user-data-dir for the workspace browser.
#
# Snap Chromium only accepts a profile inside its confined user-data tree.
# On Ubuntu, /snap/bin/chromium is a symlink to /usr/bin/snap, so resolving
# first hides the snap path. Detect snap from the original path (/snap/bin/*)
# or from `snap list chromium` before resolving. The profile then sits in the
# desktop user's ~/snap/chromium/common. Other Chromium builds keep the
# private data dir.
#
#   chromium_user_data_dir <chromium-binary> <desktop-home> <fallback-dir>
#   sh scripts/chromium-profile.sh --print <binary> <home> <fallback>
chromium_user_data_dir() {
  _chromium=$1
  _desktop_home=$2
  _fallback=$3
  _snap_profile="$_desktop_home/snap/chromium/common/hermes-alans-way"

  # Match the path the operator invoked. readlink/realpath of the snap
  # wrapper lands on /usr/bin/snap, which is not under /snap/.
  case "$_chromium" in
    /snap/bin/*|*/snap/bin/*)
      printf '%s\n' "$_snap_profile"
      return
      ;;
  esac

  # command -v can return /usr/bin/chromium while the snap is what runs.
  # Only the Chromium snap names are checked, so google-chrome stays put.
  _base=$(basename "$_chromium")
  case "$_base" in
    chromium|chromium-browser)
      if command -v snap >/dev/null 2>&1 && snap list chromium >/dev/null 2>&1; then
        printf '%s\n' "$_snap_profile"
        return
      fi
      ;;
  esac

  _resolved=$_chromium
  if [ -L "$_chromium" ]; then
    _resolved=$(readlink -f "$_chromium" 2>/dev/null || printf '%s' "$_chromium")
  fi
  case "$_resolved" in
    */snap/*)
      printf '%s\n' "$_snap_profile"
      ;;
    *)
      printf '%s\n' "$_fallback"
      ;;
  esac
}

if [ "${1:-}" = "--print" ]; then
  chromium_user_data_dir "$2" "$3" "$4"
fi
