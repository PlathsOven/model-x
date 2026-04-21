#!/usr/bin/env bash
# ModelX container entrypoint.
#
# Responsibilities:
#   1. Seed the persistent volume with default yaml configs on first boot.
#   2. Launch the market runner and the dashboard server together.
#   3. If either process dies, tear the other down so Railway restarts us.

set -euo pipefail

# --- 1. seed /data on first boot ---------------------------------------------
VOLUME_DIR="$(dirname "$DB_PATH")"
mkdir -p "$VOLUME_DIR"

if [ ! -s "$CONTRACT_YAML" ] && [ -f /app/contracts.yaml ]; then
    echo "[entrypoint] seeding $CONTRACT_YAML from image default"
    cp /app/contracts.yaml "$CONTRACT_YAML"
fi

if [ ! -s "$AGENTS_YAML" ] && [ -f /app/agents.yaml ]; then
    echo "[entrypoint] seeding $AGENTS_YAML from image default"
    cp /app/agents.yaml "$AGENTS_YAML"
fi

# --- 2. start both processes -------------------------------------------------
echo "[entrypoint] starting runner (run_live.py)"
python3 -u run_live.py \
    --contract "$CONTRACT_YAML" \
    --agents   "$AGENTS_YAML" \
    --db       "$DB_PATH" &
RUNNER_PID=$!

echo "[entrypoint] starting dashboard on $HOST:$PORT"
python3 -u dashboard/server.py \
    --db     "$DB_PATH" \
    --traces "$TRACES_PATH" \
    --host   "$HOST" \
    --port   "$PORT" &
DASHBOARD_PID=$!

# --- 3. supervise ------------------------------------------------------------
# Forward SIGTERM/SIGINT to both children so shutdown is clean.
shutdown() {
    echo "[entrypoint] received signal, shutting down"
    kill -TERM "$RUNNER_PID" "$DASHBOARD_PID" 2>/dev/null || true
    wait "$RUNNER_PID" "$DASHBOARD_PID" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

# Block until either child exits, then bring the other down.
wait -n "$RUNNER_PID" "$DASHBOARD_PID"
EXIT=$?
echo "[entrypoint] child exited with $EXIT; tearing down the other"
kill -TERM "$RUNNER_PID" "$DASHBOARD_PID" 2>/dev/null || true
wait "$RUNNER_PID" "$DASHBOARD_PID" 2>/dev/null || true
exit "$EXIT"
