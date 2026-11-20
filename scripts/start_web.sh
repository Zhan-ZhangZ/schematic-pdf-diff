#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PORT="${SCHEMADIFF_WEB_PORT:-8899}"
OUTPUT_DIR="${SCHEMADIFF_WEB_OUTPUT:-$ROOT_DIR/output/web}"
PREFIX="${SCHEMADIFF_WEB_PREFIX:-}"
PID_FILE="$OUTPUT_DIR/server.pid"
LOG_FILE="$OUTPUT_DIR/server.log"

mkdir -p "$OUTPUT_DIR"
if [ -f "$PID_FILE" ]; then
  OLD_PID=$(cat "$PID_FILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    printf 'Schematic PDF Diff web server already running: http://127.0.0.1:%s\n' "$PORT"
    exit 0
  fi
fi

nohup "$ROOT_DIR/.venv/bin/schemadiff-web" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --prefix "$PREFIX" \
  --output-root "$OUTPUT_DIR" \
  >>"$LOG_FILE" 2>&1 &
SERVER_PID=$!
printf '%s\n' "$SERVER_PID" >"$PID_FILE"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    printf 'Schematic PDF Diff web server started: http://127.0.0.1:%s\n' "$PORT"
    exit 0
  fi
  sleep 0.5
done

printf 'Web server did not become healthy. See %s\n' "$LOG_FILE" >&2
exit 1
