#!/bin/bash

pkill -f "uvicorn main:app"
pkill -x ollama

echo "AI Assistant stopped."

osascript -e 'display notification "AI Assistant Stopped" with title "AI Assistant"'