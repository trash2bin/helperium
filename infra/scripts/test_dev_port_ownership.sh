#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PID_DIR="$(mktemp -d)"
SERVER_PID=""
trap 'rm -rf "$PID_DIR"; [ -z "$SERVER_PID" ] || kill "$SERVER_PID" 2>/dev/null || true' EXIT

SERVICES=("data")
DATA_PORT=0
service_port() { echo "$DATA_PORT"; }
pidfile() { echo "$PID_DIR/$1.pid"; }

# Load only the pure ownership helpers; never source the command dispatcher.
eval "$(sed -n '/^# Return the PID listening on a TCP port\./,/^health_url()/ { /^health_url()/d; p; }' "$PROJECT_ROOT/infra/scripts/dev.sh")"

assert_success() {
  "$@"
}

assert_failure() {
  if "$@"; then
    echo "expected command to fail: $*" >&2
    exit 1
  fi
}

# A free port is accepted.
DATA_PORT=$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
assert_success check_port_ownership data

# A listener without a Helperium pidfile is rejected as foreign.
bash -c 'exec -a data-service python3 -m http.server "$1" --bind 127.0.0.1' _ "$DATA_PORT" >/dev/null 2>&1 &
SERVER_PID=$!
for _ in 1 2 3 4 5; do
  lsof -nP -tiTCP:"$DATA_PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
  sleep 0.1
done
assert_failure check_port_ownership data

# A listener with the recorded pid is accepted only when its command line
# identifies the expected service.
echo "$SERVER_PID" > "$(pidfile data)"
assert_success check_port_ownership data

echo "port ownership helper tests passed"
