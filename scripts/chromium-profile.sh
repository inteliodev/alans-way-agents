#!/bin/sh
# Chromium --user-data-dir for the workspace browser.
#
# Snap Chromium only accepts a profile inside its confined user-data tree.
# The desktop docs (deployment.md, desktop/docs/vps-browser.md) require that
# path to sit in the desktop user's permitted directory, which on snap is
# ~/snap/chromium/common. Other Chromium builds keep the private data dir.
#
#   chromium_user_data_dir <chromium-binary> <desktop-home> <fallback-dir>
#   sh scripts/chromium-profile.sh --print <binary> <home> <fallback>
chromium_user_data_dir() {
  _chromium=$1
  _desktop_home=$2
  _fallback=$3
  _resolved=$_chromium
  if [ -L "$_chromium" ]; then
    _resolved=$(readlink -f "$_chromium" 2>/dev/null || printf '%s' "$_chromium")
  fi
  case "$_resolved" in
    */snap/*)
      printf '%s\n' "$_desktop_home/snap/chromium/common/hermes-alans-way"
      ;;
    *)
      printf '%s\n' "$_fallback"
      ;;
  esac
}

if [ "${1:-}" = "--print" ]; then
  chromium_user_data_dir "$2" "$3" "$4"
fi
