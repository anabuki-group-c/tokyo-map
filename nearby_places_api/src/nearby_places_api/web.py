"""Small local API server and test UI for nearby place search."""
from __future__ import annotations

import gzip
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from nearby_places_api.categories import API_CATEGORIES
from nearby_places_api.store import EARTH_RADIUS_M, DataNotReady, DataOutdated, PlaceStore

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
DATABASE = ROOT / "data" / "processed" / "places.sqlite"
# Provisional cap until response times are measured with the Tokyo data.
MAX_RADIUS_M = 5000
DEFAULT_LIMIT = 20
MAX_LIMIT = 100
# Lets the UI call the API when it is opened from another local server, such as VS Code Live Server.
LOCAL_ORIGIN = re.compile(r"http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?")


class InvalidParameter(Exception):
    pass


def parse_nearby(query: dict) -> dict:
    """Validate /api/v1/places/nearby parameters. Raises InvalidParameter."""
    def value(name, default=None):
        values = query.get(name)
        if not values or values[0] == "":
            if default is None:
                raise InvalidParameter(f"{name}を指定してください。")
            return default
        return values[0]

    def number(name):
        try:
            result = float(value(name))
        except ValueError:
            raise InvalidParameter(f"{name}には数値を指定してください。") from None
        if not math.isfinite(result):
            raise InvalidParameter(f"{name}には数値を指定してください。")
        return result

    lat, lon, radius_m = number("lat"), number("lon"), number("radius_m")
    if not -90 <= lat <= 90:
        raise InvalidParameter("latには-90〜90の数値を指定してください。")
    if not -180 <= lon <= 180:
        raise InvalidParameter("lonには-180〜180の数値を指定してください。")
    if radius_m <= 0:
        raise InvalidParameter("radius_mには0より大きい数値を指定してください。")
    if radius_m > MAX_RADIUS_M:
        raise InvalidParameter(f"radius_mには{MAX_RADIUS_M}以下の数値を指定してください。")
    category = value("category", "all")
    if category not in (*API_CATEGORIES, "all"):
        raise InvalidParameter("categoryにはrestaurant、tourism、allのいずれかを指定してください。")
    try:
        limit = int(value("limit", str(DEFAULT_LIMIT)))
    except ValueError:
        raise InvalidParameter(f"limitには1〜{MAX_LIMIT}の整数を指定してください。") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise InvalidParameter(f"limitには1〜{MAX_LIMIT}の整数を指定してください。")
    return {"lat": lat, "lon": lon, "radius_m": radius_m, "category": category, "limit": limit}


def nearby_response(store: PlaceStore, params: dict) -> tuple[int, dict]:
    boundary = store.boundary()
    lat, lon, radius_m = params["lat"], params["lon"], params["radius_m"]
    if not boundary.contains(lat, lon):
        return 400, error_body("OUT_OF_COVERAGE", "指定した地点は東京都の範囲外です。")
    places = store.nearby(lat, lon, radius_m, params["category"], params["limit"])
    for place in places:
        place["distance_m"] = round(place["distance_m"], 1)
    return 200, {
        "center": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "category": params["category"],
        "count": len(places),
        # True when the search circle may reach outside Tokyo (another prefecture or the sea).
        "partial_coverage": not boundary.covers_circle(lat, lon, radius_m, EARTH_RADIUS_M),
        "places": places,
    }


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def make_handler(store: PlaceStore):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in STATIC_FILES:
                filename, content_type = STATIC_FILES[url.path]
                self.respond(200, (STATIC / filename).read_bytes(), content_type)
                return
            if url.path not in ("/api/v1/places/nearby", "/api/v1/places/coverage"):
                self.respond_json(404, error_body("NOT_FOUND", "指定したAPIは存在しません。"))
                return
            try:
                if url.path == "/api/v1/places/coverage":
                    boundary = store.boundary()
                    status, body = 200, {"bbox": list(boundary.bbox), "boundary": boundary.geometry}
                else:
                    status, body = nearby_response(store, parse_nearby(parse_qs(url.query)))
            except InvalidParameter as error:
                status, body = 400, error_body("INVALID_PARAMETER", str(error))
            except DataNotReady:
                status, body = 503, error_body("DATA_NOT_READY", "施設データが未取込です。places import を実行してください。")
            except DataOutdated:
                status, body = 503, error_body("DATA_OUTDATED", "施設データが古い形式です。places import を実行し直してください。")
            self.respond_json(status, body)

        def respond_json(self, status, data):
            self.respond(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def respond(self, status, body, content_type):
            compressed = len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "")
            if compressed:
                body = gzip.compress(body, compresslevel=1)
            self.send_response(status)
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding, Origin")
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            origin = self.headers.get("Origin", "")
            if self.path.startswith("/api/") and LOCAL_ORIGIN.fullmatch(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            # Query strings contain the user's location; do not log them.
            pass
    return Handler


def serve(host="127.0.0.1", port=8000, database: Path = DATABASE):
    server = ThreadingHTTPServer((host, port), make_handler(PlaceStore(database)))
    print(f"Nearby places UI: http://{host}:{port}/  API: /api/v1/places/nearby (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
