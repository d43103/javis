#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${JAVIS_SERVER_PYTHON:-python3}"
VENV_PATH="${JAVIS_SERVER_VENV:-$PROJECT_ROOT/.venv}"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"

mkdir -p "$PROJECT_ROOT/logs" "$PROJECT_ROOT/data"

"$PYTHON_BIN" -m venv "$VENV_PATH"
"$VENV_PATH/bin/pip" install --upgrade pip setuptools wheel
"$VENV_PATH/bin/pip" install -r "$REQUIREMENTS_FILE"

echo "Server environment installed at: $VENV_PATH"
echo "Run server with: $VENV_PATH/bin/python -m src.javis_stt.server"
