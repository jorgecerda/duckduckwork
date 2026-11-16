#!/usr/bin/env bash
# ==============================================================================
# DuckDuckWork - Stop DDW + Restore Normal Sleep
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/.data"
PID_FILE="$DATA_DIR/ddw_pids.txt"

echo "=========================================================="
echo " Stopping DuckDuckWork & Restoring Normal Sleep"
echo "=========================================================="

# 1. Kill processes from PID file if available
if [ -f "$PID_FILE" ]; then
    PIDS=$(cat "$PID_FILE")
    for p in $PIDS; do
        if [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; then
            kill -9 "$p" 2>/dev/null || true
        fi
    done
    rm -f "$PID_FILE"
fi

# 2. Terminate any remaining server / tunnel / caffeinate processes
echo "[1/3] Terminating DuckDuckWork server processes..."
pkill -9 -f "applypilot.cli serve" 2>/dev/null || true
pkill -9 -f "cloudflared tunnel" 2>/dev/null || true
pkill -9 -f "caffeinate -dimsu" 2>/dev/null || true

# 3. Restore normal macOS sleep settings
echo "[2/3] Restoring normal macOS sleep behavior..."
echo "Please enter your Mac password if prompted:"
sudo pmset -a disablesleep 0 2>/dev/null || true

# 4. Verify port 5050 is freed
sleep 1
if lsof -i :5050 >/dev/null 2>&1; then
    echo "Warning: Port 5050 is still occupied by another process:"
    lsof -i :5050
else
    echo "[3/3] Port 5050 is clear."
fi

echo "=========================================================="
echo " DuckDuckWork is STOPPED."
echo " Normal sleep has been RESTORED."
echo "=========================================================="
