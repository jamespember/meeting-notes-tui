#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
OMARCHY_PATH="${OMARCHY_PATH:-/usr/share/omarchy}"

if ! command -v omarchy >/dev/null 2>&1 || [[ $(omarchy version) != 4.* ]]; then
    echo "Skipping Omarchy integration: Quattro 4.x was not detected."
    exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required to update the Omarchy shell configuration." >&2
    exit 1
fi

install -Dm644 \
    "$ROOT_DIR/integrations/omarchy/meeting-notes.desktop" \
    "$HOME/.local/share/applications/meeting-notes.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi

bindings="$HOME/.config/hypr/bindings.lua"
mkdir -p "$(dirname "$bindings")"
touch "$bindings"
if ! grep -Fq -- '-- meeting-notes:begin' "$bindings"; then
    cp "$bindings" "$bindings.bak.$(date +%s)"
    cat >> "$bindings" <<'EOF'

-- meeting-notes:begin
o.bind("SUPER + M", "Meeting Notes", { tui = "meeting-notes", focus = true })
-- meeting-notes:end
EOF
fi

shell_config="$HOME/.config/omarchy/shell.json"
default_config="$OMARCHY_PATH/config/omarchy/shell.json"
mkdir -p "$(dirname "$shell_config")"
source_config="$shell_config"
if [[ ! -s $source_config ]]; then
    source_config="$default_config"
fi

tmp="$(mktemp "$(dirname "$shell_config")/.shell.json.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
jq '
  .version = 1
  | .bar = (.bar // {})
  | .bar.layout = (.bar.layout // {})
  | .bar.layout.right = (.bar.layout.right // [])
  | .bar.layout.right = (
      [.bar.layout.right[] | select((if type == "object" then (.id // "") else . end) != "meeting-notes")]
      + [{
          "id": "meeting-notes",
          "type": "command",
          "exec": "meeting-notes-status",
          "interval": 1,
          "tooltip": "Meeting Notes",
          "onClick": "omarchy-launch-or-focus-tui meeting-notes"
        }]
    )
' "$source_config" > "$tmp"

if [[ ! -f $shell_config ]] || ! cmp -s "$tmp" "$shell_config"; then
    if [[ -f $shell_config ]]; then
        cp "$shell_config" "$shell_config.bak.$(date +%s)"
    fi
    chmod 600 "$tmp"
    mv "$tmp" "$shell_config"
fi

if [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
    hyprctl reload >/dev/null
fi
omarchy-shell -q shell reloadConfig || true

echo "Omarchy integration installed: SUPER+M, Apps menu, notifications, and status bar."
