#!/bin/bash
# Meeting Notes - Waybar Module
# Displays current meeting recording status in Waybar

# Auto-detect meeting-notes directory (parent of hyprland/ folder where this script lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEETING_NOTES_DIR="$(dirname "$SCRIPT_DIR")"
STATUS_FILE="$MEETING_NOTES_DIR/.status"

# Check if meeting-notes app is running
if ! pgrep -f "python.*run.py" > /dev/null; then
    echo '{"text": "󰗠", "tooltip": "Meeting Notes (not running)", "class": "idle"}'
    exit 0
fi

# Read status file
if [ -f "$STATUS_FILE" ]; then
    source "$STATUS_FILE"
    
    case "$STATUS" in
        "recording")
            echo "{\"text\": \"󰦕 ${DURATION:-00:00}\", \"tooltip\": \"Recording: ${TITLE:-Meeting}\", \"class\": \"recording\"}"
            ;;
        "processing")
            echo "{\"text\": \"󰄬\", \"tooltip\": \"Processing recording...\", \"class\": \"processing\"}"
            ;;
        *)
            echo "{\"text\": \"󰗠\", \"tooltip\": \"Meeting Notes (ready)\", \"class\": \"ready\"}"
            ;;
    esac
else
    # App is running but no status file yet
    echo '{"text": "󰗠", "tooltip": "Meeting Notes (ready)", "class": "ready"}'
fi
