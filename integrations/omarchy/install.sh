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
    "$ROOT_DIR/integrations/omarchy/omascribe.desktop" \
    "$HOME/.local/share/applications/omascribe.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications"
fi

# Install the Omascribe control-panel bar plugin
plugin_src="$ROOT_DIR/integrations/omarchy/omascribe-control"
plugin_dst="$HOME/.config/omarchy/plugins/omascribe.control"
if [[ -d $plugin_src ]]; then
    if ! omarchy plugin validate "$plugin_src" >/dev/null 2>&1; then
        echo "ERROR: Omascribe control panel plugin failed validation." >&2
        exit 1
    fi
    mkdir -p "$plugin_dst"
    install -Dm644 "$plugin_src/manifest.json" "$plugin_dst/manifest.json"
    install -Dm644 "$plugin_src/Panel.qml" "$plugin_dst/Panel.qml"
fi

bindings="$HOME/.config/hypr/bindings.lua"
mkdir -p "$(dirname "$bindings")"
touch "$bindings"
if ! grep -Fq -- '-- omascribe:begin' "$bindings"; then
    cp "$bindings" "$bindings.bak.$(date +%s)"
    cat >> "$bindings" <<'EOF'

-- omascribe:begin
o.bind("SUPER + M", "Omascribe", { tui = "omascribe", focus = true })
-- omascribe:end
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
      [.bar.layout.right[] | select((if type == "object" then (.id // "") else . end) != "omascribe" and (if type == "object" then (.id // "") else . end) != "omascribe.control")]
      + [{
          "id": "omascribe.control",
          "refreshIntervalSec": 1,
          "maxRecent": 6
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
