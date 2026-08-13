#!/bin/bash

echo "===== AI Assistant Status ====="

if pgrep -x ollama >/dev/null; then
    echo "✅ Ollama: Running"
else
    echo "❌ Ollama: Not Running"
fi

if lsof -i:8420 >/dev/null; then
    echo "✅ FastAPI: Running"
else
    echo "❌ FastAPI: Not Running"
fi

echo

echo "Installed Models:"
ollama list
