"""最長乗車経路実験専用の最小 Web サーバー。ODPT キーはサーバー内にのみ保持する。"""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen
import gzip

STATIC = Path(__file__).parent / "static"
ODPT_LOCK = threading.Lock()
ODPT_LAST_REQUEST = 0.0


def fetch_records(key: str, url: str, filters=None) -> list:
    query = urlencode({"odpt:operator": "odpt.Operator:TokyoMetro", "acl:consumerKey": key, **(filters or {})})
    # The shared key has a global rate budget, not a separate budget per route.
    global ODPT_LAST_REQUEST
    with ODPT_LOCK:
        time.sleep(max(0, 2 - (time.monotonic() - ODPT_LAST_REQUEST)))
        ODPT_LAST_REQUEST = time.monotonic()
        with urlopen(f"{url}?{query}", timeout=25) as response:
            data = json.load(response)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("Invalid ODPT response")
    return data


def make_handler(longpath_service):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if urlsplit(self.path).path != "/api/route/plan" or longpath_service is None:
                self.respond(404, b"Not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1_000_000:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError
                result = longpath_service.plan(body)
            except (ValueError, TypeError, KeyError):
                self.respond(400, b"Invalid request body", "text/plain")
                return
            self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/route/network" and longpath_service is not None:
                self.respond(200, json.dumps(longpath_service.get_network(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            files = {
                "/": (STATIC / "route.html", "text/html; charset=utf-8"),
                "/route": (STATIC / "route.html", "text/html; charset=utf-8"),
                "/route.js": (STATIC / "route.js", "text/javascript; charset=utf-8"),
            }
            if path not in files:
                self.respond(404, b"Not found", "text/plain")
                return
            filename, content_type = files[path]
            try:
                body = filename.read_bytes()
            except FileNotFoundError:
                self.respond(404, b"Static file missing", "text/plain")
                return
            self.respond(200, body, content_type)

        def respond(self, status, body, content_type):
            compressed = len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "")
            if compressed:
                body = gzip.compress(body, compresslevel=1)
            self.send_response(status)
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            # Do not log arbitrary URLs or query strings.
            pass
    return Handler


def serve(host="127.0.0.1", port=8000):
    processed = Path(__file__).resolve().parents[2] / "data" / "processed"
    key = os.environ.get("ODPT_CONSUMER_KEY", "")
    from longpath_mock.longpath import Service as LongPathService

    longpath_service = LongPathService(
        processed, key, compute_seconds=float(os.environ.get("LONGPATH_COMPUTE_SECONDS", "3")))
    server = ThreadingHTTPServer((host, port), make_handler(longpath_service))
    print(f"Longpath route experiment: http://{host}:{port}/route (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
