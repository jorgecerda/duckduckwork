#!/usr/bin/env bash
# ==============================================================================
# DuckDuckWork - 1-Click Local Launcher
# https://github.com/jorgecerda/duckduckwork
# ==============================================================================

set -e

PORT="${PORT:-5050}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================================="
echo " Starting DuckDuckWork (100% Local Job Search Engine & CRM)"
echo "=========================================================="

# 1. Verify Python installation
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Error: Python 3.10+ is required but not found."
    echo "Please install Python from https://www.python.org/downloads/ or your package manager."
    exit 1
fi

# 2. Virtual environment setup
if [ ! -d ".venv" ]; then
    echo "Creating local Python virtual environment (.venv)..."
    $PYTHON_CMD -m venv .venv
fi

# 3. Activate virtualenv
source .venv/bin/activate

# 4. Install dependencies if not yet installed
if ! python -c "import typer, rich, httpx" >/dev/null 2>&1; then
    echo "Installing required dependencies..."
    pip install --upgrade pip
    pip install -e .
fi

# 5. Launch local server and optional Cloudflare Tunnel
export PYTHONPATH=src
export APPLYPILOT_DIR="${APPLYPILOT_DIR:-$SCRIPT_DIR/.data}"

CLOUDFLARED_PID=""
cleanup() {
    echo ""
    echo "Shutting down DuckDuckWork server..."
    if [ -n "$CLOUDFLARED_PID" ]; then
        kill "$CLOUDFLARED_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup INT TERM

if [ "${NO_TUNNEL:-0}" != "1" ] && [ -x ".venv/bin/cloudflared" ]; then
    echo "Starting Cloudflare Tunnel for remote mobile access..."
    .venv/bin/cloudflared tunnel --url "http://localhost:${PORT}" > /tmp/ddw_cloudflared.log 2>&1 &
    CLOUDFLARED_PID=$!
    sleep 3
    TUNNEL_URL=$(grep -o -E "https://[a-zA-Z0-9-]+\.trycloudflare\.com" /tmp/ddw_cloudflared.log | tail -n 1 || true)
    if [ -n "$TUNNEL_URL" ]; then
        echo "Remote Mobile Access URL: $TUNNEL_URL"
        # Sync external URL with server config
        python -c "
import json
from pathlib import Path
cfg_path = Path('$APPLYPILOT_DIR/admin_config.json')
if cfg_path.exists():
    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        cfg['server_external_url'] = '$TUNNEL_URL'
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    except Exception:
        pass
" 2>/dev/null || true
    fi
fi

echo "=========================================================="
echo "Opening DuckDuckWork in your browser: http://localhost:${PORT}/"
echo "Press Ctrl+C to stop the local server at any time."
echo "=========================================================="

python -m applypilot.cli serve --port "$PORT" --open
