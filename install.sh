#!/usr/bin/env bash
set -Eeuo pipefail

REPO="${DECKY_DIALOGS_REPO:-Lukaswnd/decky-dialogs}"
PLUGIN_NAME="DeckyDialogs"
ASSET="decky-dialogs.tar.gz"
BASE_URL="https://github.com/${REPO}/releases/latest/download"

log() {
  printf '[Decky Dialogs] %s\n' "$*"
}

fail() {
  printf '[Decky Dialogs] ERROR: %s\n' "$*" >&2
  exit 1
}

for command in curl tar sha256sum; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

if [[ ${EUID} -eq 0 && -n ${SUDO_USER:-} && ${SUDO_USER} != root ]]; then
  TARGET_USER="$SUDO_USER"
elif id deck >/dev/null 2>&1; then
  TARGET_USER="deck"
else
  TARGET_USER="$(id -un)"
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" 2>/dev/null | cut -d: -f6 || true)"
[[ -n "$TARGET_HOME" ]] || TARGET_HOME="$HOME"

if [[ -n ${DECKY_HOME:-} ]]; then
  DECKY_ROOT="$DECKY_HOME"
elif [[ -d /home/deck/homebrew ]]; then
  DECKY_ROOT="/home/deck/homebrew"
elif [[ -d "$TARGET_HOME/homebrew" ]]; then
  DECKY_ROOT="$TARGET_HOME/homebrew"
else
  fail "Decky Loader was not found. Install Decky first, or set DECKY_HOME."
fi

PLUGINS_DIR="$DECKY_ROOT/plugins"
DEST="$PLUGINS_DIR/$PLUGIN_NAME"
CLI_DIR="$TARGET_HOME/.local/bin"
CLI_LINK="$CLI_DIR/decky-dialogs"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

run_root() {
  if [[ ${EUID} -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    fail "Root access is required, but sudo is unavailable."
  fi
}

log "Downloading the latest release from $REPO..."
curl --fail --silent --show-error --location \
  "$BASE_URL/$ASSET" \
  --output "$TMP_DIR/$ASSET"
curl --fail --silent --show-error --location \
  "$BASE_URL/$ASSET.sha256" \
  --output "$TMP_DIR/$ASSET.sha256"

(
  cd "$TMP_DIR"
  sha256sum --check "$ASSET.sha256"
)

tar -xzf "$TMP_DIR/$ASSET" -C "$TMP_DIR"
SOURCE="$TMP_DIR/$PLUGIN_NAME"

[[ -f "$SOURCE/main.py" ]] || fail "Release is missing main.py"
[[ -f "$SOURCE/plugin.json" ]] || fail "Release is missing plugin.json"
[[ -s "$SOURCE/dist/index.js" ]] || fail "Release is missing dist/index.js"
[[ -f "$SOURCE/decky-dialogs" ]] || fail "Release is missing the CLI helper"

NEW_DEST="$PLUGINS_DIR/.${PLUGIN_NAME}.new.$$"
OLD_DEST="$PLUGINS_DIR/.${PLUGIN_NAME}.old.$$"

log "Installing into $DEST..."
run_root mkdir -p "$PLUGINS_DIR"
run_root rm -rf "$NEW_DEST" "$OLD_DEST"
run_root mkdir -p "$NEW_DEST"
run_root cp -a "$SOURCE/." "$NEW_DEST/"
run_root chmod 0755 "$NEW_DEST/decky-dialogs"

if [[ -e "$DEST" ]]; then
  run_root mv "$DEST" "$OLD_DEST"
fi
run_root mv "$NEW_DEST" "$DEST"

mkdir -p "$CLI_DIR"
ln -sfn "$DEST/decky-dialogs" "$CLI_LINK"

# Remove obsolete names from the former SSH Notify version.
rm -f "$TARGET_HOME/.local/bin/deck-notify"
run_root rm -rf "$DECKY_ROOT/plugins/SSHNotify"
run_root rm -f /tmp/decky-ssh-notify.sock

if systemctl list-unit-files plugin_loader.service >/dev/null 2>&1; then
  log "Restarting Decky Loader..."
  if ! run_root systemctl restart plugin_loader.service; then
    log "Restart failed; restoring the previous plugin version."
    run_root rm -rf "$DEST"
    if [[ -e "$OLD_DEST" ]]; then
      run_root mv "$OLD_DEST" "$DEST"
      run_root systemctl restart plugin_loader.service || true
    fi
    fail "Decky Loader could not be restarted."
  fi
else
  log "plugin_loader.service was not found; restart Decky Loader manually."
fi

run_root rm -rf "$OLD_DEST"

VERSION="unknown"
if [[ -f "$DEST/package.json" ]]; then
  VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$DEST/package.json" | head -n 1)"
  [[ -n "$VERSION" ]] || VERSION="unknown"
fi

log "Installed Decky Dialogs $VERSION successfully."
log "CLI: $CLI_LINK"
log "Enable Decky Dialogs in Decky if it is currently disabled."
