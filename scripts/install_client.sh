#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${JAVIS_CLIENT_PYTHON:-python3}"
VENV_PATH="${JAVIS_CLIENT_VENV:-$PROJECT_ROOT/.venv-client}"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements-mac-client.txt"

mkdir -p "$PROJECT_ROOT/logs"

"$PYTHON_BIN" -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel
"$VENV_PATH/bin/pip" install -r "$REQUIREMENTS_FILE"

echo "Client environment installed at: $VENV_PATH"
echo "Run client with: $VENV_PATH/bin/python scripts/mic_stream_client.py --server ws://<server>:8765 --session-id mac-1"
