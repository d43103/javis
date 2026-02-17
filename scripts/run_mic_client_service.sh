#!/bin/zsh

set -euo pipefail

PROJECT_ROOT="/Users/d43103/Workspace/projects/javis"
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

SERVER_URL="${JAVIS_SERVER_URL:-ws://100.67.60.57:8765}"
SESSION_ID="${JAVIS_SESSION_ID:-mac-service}"
LOG_FILE="${JAVIS_CLIENT_LOG_FILE:-$LOG_DIR/client-service.log}"
DEVICE_OPT=()

if [[ -n "${JAVIS_MIC_DEVICE:-}" ]]; then
  DEVICE_OPT=(--device "$JAVIS_MIC_DEVICE")
fi

exec /opt/anaconda3/bin/python3 "$PROJECT_ROOT/scripts/mic_stream_client.py" \
  --server "$SERVER_URL" \
  --session-id "$SESSION_ID" \
  --log-file "$LOG_FILE" \
  "${DEVICE_OPT[@]}"
