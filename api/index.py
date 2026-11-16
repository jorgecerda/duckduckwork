"""Vercel Serverless Function Entrypoint for DuckDuckWork."""

import io
import os
import sys
from pathlib import Path

# Add src to Python path
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

# Set default writable directory for Vercel serverless environment
os.environ.setdefault("APPLYPILOT_DIR", "/tmp/.data")
os.environ.setdefault("APPLYPILOT_BASE_DIR", "/tmp/.data")
os.environ.setdefault("VERCEL", "1")

tmp_base = Path("/tmp/.data")
tmp_base.mkdir(parents=True, exist_ok=True)

# Auto-restore session from environment variable or local payload file
session_b64 = os.environ.get("DUCKDUCKWORK_SESSION_B64") or os.environ.get("APPLYPILOT_SESSION_B64")
if not session_b64:
    local_b64_file = ROOT_DIR / ".session_b64.txt"
    if local_b64_file.exists():
        session_b64 = local_b64_file.read_text(encoding="utf-8").strip()

if session_b64:
    try:
        import base64
        import json
        import tarfile
        seed_sid = os.environ.get("APPLYPILOT_SEED_SESSION_ID")
        if seed_sid:
            target_dir = tmp_base / "sessions" / seed_sid
            if not target_dir.exists() or not (target_dir / "applypilot.db").exists():
                target_dir.mkdir(parents=True, exist_ok=True)
                compressed_bytes = base64.b64decode(session_b64.strip())
                with tarfile.open(fileobj=io.BytesIO(compressed_bytes), mode="r:gz") as tar:
                    tar.extractall(path=target_dir)
                print(f"[DuckDuckWork] Successfully restored session {seed_sid} into {target_dir}")
    except Exception as seed_err:
        print(f"[DuckDuckWork Session Restore Error]: {seed_err}")

from applypilot.server import CRMDashboardHandler


class _WSGIAdapterSocket:
    """Mock socket adapter bridging Python WSGI streams to BaseHTTPRequestHandler."""

    def __init__(self, rfile, wfile):
        self._rfile = rfile
        self._wfile = wfile

    def makefile(self, mode, bufsize=None):
        if "r" in mode:
            return self._rfile
        return self._wfile

    def sendall(self, data, flags=0):
        self._wfile.write(data)

    def send(self, data, flags=0):
        self._wfile.write(data)
        return len(data)

    def getpeername(self):
        return ("127.0.0.1", 80)


def app(environ, start_response):
    """WSGI application entrypoint for Vercel Serverless Functions."""
    path = environ.get("PATH_INFO", "")
    query = environ.get("QUERY_STRING", "")
    if query:
        path = f"{path}?{query}"
    if not path:
        path = "/"

    method = environ.get("REQUEST_METHOD", "GET")
    content_len = int(environ.get("CONTENT_LENGTH", 0) or 0)
    body_bytes = environ["wsgi.input"].read(content_len) if content_len > 0 else b""

    request_line = f"{method} {path} HTTP/1.1\r\n"
    headers_str = ""
    for k, v in environ.items():
        if k.startswith("HTTP_"):
            hdr_name = k[5:].replace("_", "-").title()
            headers_str += f"{hdr_name}: {v}\r\n"
        elif k in ("CONTENT_TYPE", "CONTENT_LENGTH") and v:
            hdr_name = k.replace("_", "-").title()
            headers_str += f"{hdr_name}: {v}\r\n"

    raw_request = (request_line + headers_str + "\r\n").encode("utf-8") + body_bytes
    rfile = io.BytesIO(raw_request)
    wfile = io.BytesIO()

    sock = _WSGIAdapterSocket(rfile, wfile)

    try:
        CRMDashboardHandler(sock, ("127.0.0.1", 80), None)
    except Exception as e:
        print(f"[DuckDuckWork Vercel WSGI Handler Exception]: {e}")

    wfile.seek(0)
    raw_response = wfile.read()

    if raw_response:
        try:
            parts = raw_response.split(b"\r\n\r\n", 1)
            header_part = parts[0].decode("utf-8", errors="ignore")
            resp_body = parts[1] if len(parts) > 1 else b""

            lines = header_part.split("\r\n")
            status_line = lines[0]
            status_parts = status_line.split(" ", 2)
            status_code = f"{status_parts[1]} {status_parts[2]}" if len(status_parts) > 2 else f"{status_parts[1]} OK"

            headers = []
            for line in lines[1:]:
                if ":" in line:
                    hk, hv = line.split(":", 1)
                    headers.append((hk.strip(), hv.strip()))

            start_response(status_code, headers)
            return [resp_body]
        except Exception as parse_err:
            print(f"[DuckDuckWork Vercel Response Parse Error]: {parse_err}")

    start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
    return [b"Serverless Handler Error"]
