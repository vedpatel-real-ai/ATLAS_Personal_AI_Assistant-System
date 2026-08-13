#!/bin/bash

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT" || exit 1

source venv/bin/activate

# --- API key (Milestone 0 fix) ---------------------------------------
# Previously this script hardcoded ASSISTANT_API_KEY in plaintext, which
# meant it was sitting unencrypted on disk (and likely version-controlled)
# every time this repo was touched. Now: a random key is generated once
# into .secrets/api_key (chmod 600, gitignored — see .gitignore), and every
# subsequent run just loads it. Nothing secret lives in this script anymore.
SECRETS_DIR="$PROJECT/.secrets"
KEY_FILE="$SECRETS_DIR/api_key"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

if [ ! -f "$KEY_FILE" ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > "$KEY_FILE"
    chmod 600 "$KEY_FILE"
    echo "Generated a new ASSISTANT_API_KEY -> $KEY_FILE"
    echo "Paste this value into chat.html's \"API key\" box once (Settings/keyBtn)."
fi

export ASSISTANT_API_KEY="$(cat "$KEY_FILE")"

# Start Ollama only if not already running
if ! pgrep -x ollama >/dev/null; then
    OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_KV_CACHE_TYPE=q8_0 \
    ollama serve > logs/ollama.log 2>&1 &
fi

sleep 2

# Start FastAPI only if not already running
if ! lsof -i:8420 >/dev/null; then
    uvicorn main:app \
        --host 127.0.0.1 \
        --port 8420 \
        > logs/assistant.log 2>&1 &
fi

echo ""
echo "==============================="
echo "🚀 AI Assistant Ready"
echo "==============================="
echo ""

echo "Ollama:"
curl -s http://127.0.0.1:11434/api/tags >/dev/null \
&& echo "✅ Running" \
|| echo "❌ Failed"

echo ""

echo "FastAPI:"
curl -s http://127.0.0.1:8420/docs >/dev/null \
&& echo "✅ Running" \
|| echo "❌ Failed"

echo ""

echo "Installed Models:"
ollama list

osascript -e 'display notification "AI Assistant Ready" with title "AI Assistant"'
