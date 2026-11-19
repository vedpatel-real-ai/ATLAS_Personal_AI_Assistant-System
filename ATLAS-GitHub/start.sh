#!/bin/bash

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT" || exit 1

source venv/bin/activate || exit 1
mkdir -p logs || exit 1

# Keep the generated credential private from the moment it is created.
umask 077
SECRETS_DIR="$PROJECT/.secrets"
KEY_FILE="$SECRETS_DIR/api_key"
mkdir -p "$SECRETS_DIR" || exit 1
chmod 700 "$SECRETS_DIR" || exit 1
if [ ! -f "$KEY_FILE" ]; then
    (set -C; python3 -c 'import secrets; print(secrets.token_hex(32))' > "$KEY_FILE") || exit 1
    echo "Generated a local key in .secrets/api_key. Enter it using the API key button."
fi
ASSISTANT_API_KEY="$(cat "$KEY_FILE")" || exit 1
if [[ ! "$ASSISTANT_API_KEY" =~ [^[:space:]] ]]; then
    echo "The local API key file is empty. Configure .secrets/api_key before starting." >&2
    exit 1
fi
export ASSISTANT_API_KEY

# Start Ollama only if not already running
if ! curl -fs --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null; then
    OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_KV_CACHE_TYPE=q8_0 \
    ollama serve > logs/ollama.log 2>&1 &
fi

sleep 2

# Start FastAPI only if not already running
if ! lsof -nP -iTCP:8420 -sTCP:LISTEN >/dev/null; then
    uvicorn main:app \
        --host 127.0.0.1 \
        --port 8420 \
        > logs/assistant.log 2>&1 &
fi

# Uvicorn imports the application before it can answer HTTP requests.
for attempt in {1..30}; do
    if curl -fsS --max-time 1 http://127.0.0.1:8420/openapi.json 2>/dev/null |
        python3 -c 'import json, sys; sys.exit("/command" not in json.load(sys.stdin)["paths"])' 2>/dev/null; then
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "ATLAS did not become ready on port 8420. Check logs/assistant.log." >&2
        exit 1
    fi
    sleep 0.3
done

echo "ATLAS is available at http://127.0.0.1:8420/"
if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "Ollama is unavailable. Model-backed requests need the local service on port 11434." >&2
    exit 1
fi
ollama list || exit 1
osascript -e 'display notification "AI Assistant Ready" with title "AI Assistant"'
