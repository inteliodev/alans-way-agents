#!/bin/sh
# Non-GUI BlueBubbles setup for Hayden's Mac. Idempotent.
#
# Installs the bluebubbles cask, keeps the Mac awake on AC power, and opens
# the app. Full Disk Access, Accessibility, Messages sign-in, the server
# password, and turning off the ngrok/cloud proxy are GUI steps in
# docs/intelio-vps.md. Lid-closed sleep still happens unless the Mac is in
# clamshell mode (power adapter and an external display).
#
#   ./scripts/mac-bluebubbles-setup.sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
  echo "mac-bluebubbles-setup: run this on the Mac, not the VPS" >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "mac-bluebubbles-setup: Homebrew is required (brew install --cask bluebubbles)" >&2
  exit 1
fi

if brew list --cask bluebubbles >/dev/null 2>&1; then
  echo "bluebubbles cask already installed"
else
  brew install --cask bluebubbles
fi

# AC power only. Battery sleep is left as the Mac already has it.
if [ "$(id -u)" = 0 ]; then
  pmset -c sleep 0
  pmset -c disksleep 0
else
  sudo pmset -c sleep 0
  sudo pmset -c disksleep 0
fi

open -a BlueBubbles
echo "BlueBubbles open. Finish Full Disk Access, Accessibility, the server password, and disable the cloud proxy. See docs/intelio-vps.md."
