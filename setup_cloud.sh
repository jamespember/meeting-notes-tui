#!/bin/bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT_DIR/venv/bin/python"

if [[ ! -x $PYTHON ]]; then
    echo "Run ./setup.sh first to create the application environment." >&2
    exit 1
fi

echo "Cloud AI Provider Setup"
echo "  1) OpenAI"
echo "  2) Anthropic"
echo "  3) OpenRouter"
read -r -p "Enter choice [1-3]: " provider_choice

case $provider_choice in
    1) provider=openai; provider_name=OpenAI; env_var=OPENAI_API_KEY; model=mini; key_url=https://platform.openai.com/api-keys ;;
    2) provider=anthropic; provider_name=Anthropic; env_var=ANTHROPIC_API_KEY; model=haiku; key_url=https://console.anthropic.com/settings/keys ;;
    3) provider=openrouter; provider_name=OpenRouter; env_var=OPENROUTER_API_KEY; model=balanced; key_url=https://openrouter.ai/keys ;;
    *) echo "Invalid choice" >&2; exit 1 ;;
esac

api_key="${!env_var:-}"
if [[ -z $api_key ]]; then
    echo "Get a key at: $key_url"
    read -r -s -p "$provider_name API key: " api_key
    echo
fi
if [[ -z $api_key ]]; then
    echo "No API key provided." >&2
    exit 1
fi

MEETING_NOTES_PROVIDER="$provider" \
MEETING_NOTES_MODEL="$model" \
MEETING_NOTES_API_KEY="$api_key" \
"$PYTHON" <<'PY'
import os

from meeting_notes.config import load_config, save_config

provider = os.environ["MEETING_NOTES_PROVIDER"]
config = load_config()
config.ai_provider = provider
config.ai_model = os.environ["MEETING_NOTES_MODEL"]
setattr(config, f"{provider}_api_key", os.environ["MEETING_NOTES_API_KEY"])
save_config(config)
PY

unset api_key
echo "$provider_name configured. The key is stored only in the private Meeting Notes config."
echo "Run: meeting-notes"
