#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_DIR="${SCHEMADIFF_WEB_OUTPUT:-$ROOT_DIR/output/web}"
PID_FILE="$OUTPUT_DIR/server.pid"

if [ ! -f "$PID_FILE" ]; then
  printf 'Web server is not running.\n'
  exit 0
fi

SERVER_PID=$(cat "$PID_FILE")
if kill -0 "$SERVER_PID" 2>/dev/null; then
  kill "$SERVER_PID"
  printf 'Stopped Schematic PDF Diff web server.\n'
else
  printf 'Stale PID file removed.\n'
fi
rm -f "$PID_FILE"
