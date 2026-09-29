"""Small local web server. ODPT credentials never leave the backend."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, parse_qs
import gzip
import math
from urllib.request import urlopen

from transit_experiment.odpt import TRAIN_INFORMATION_URL

ROOT = Path(__file__).resolve().parents[2]
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


def fetch_notices(key: str) -> list:
    return fetch_records(key, TRAIN_INFORMATION_URL)


def fetch_trains(key: str) -> list:
    return fetch_records(key, "https://api.odpt.org/api/v4/odpt:Train")


def fetch_stations(key: str) -> list:
    return fetch_records(key, "https://api.odpt.org/api/v4/odpt:Station")


def fetch_timetables(key: str, route: str, calendar: str) -> list:
    records = fetch_records(key, "https://api.odpt.org/api/v4/odpt:TrainTimetable", {
        "odpt:railway": f"odpt.Railway:TokyoMetro.{route}",
        "odpt:calendar": f"odpt.Calendar:{calendar}",
    })
    if len(records) >= 1000:
        # Do not mistake an API-truncated response for a complete day's schedule.
        raise ValueError("Timetable response may be truncated")
    fields = ("odpt:trainNumber", "odpt:railway", "odpt:calendar", "odpt:trainTimetableObject", "dc:date")
    return [{field: record[field] for field in fields if field in record} for record in records]


def train_positions(trains: dict, stations: dict) -> dict:
    """Station/section reports, not GPS or simulated train coordinates."""
    lookup = {s.get("owl:sameAs"): s for s in stations["notices"]}
    pins = []
    for train in trains["notices"]:
        origin = train.get("odpt:fromStation")
        destination = train.get("odpt:toStation")
        station = lookup.get(origin)
        if not station:
            continue
        lat, lon = station.get("geo:lat"), station.get("geo:long")
        if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        target = lookup.get(destination, {})
        kind = "section_origin" if destination and destination != origin else "station"
        target_lat, target_lon = target.get("geo:lat"), target.get("geo:long")
        if kind == "section_origin" and isinstance(target_lat, (float, int)) and isinstance(target_lon, (float, int)):
            if -90 <= target_lat <= 90 and -180 <= target_lon <= 180:
                lat, lon = (lat + target_lat) / 2, (lon + target_lon) / 2
                kind = "section_midpoint"
        pins.append({
            "id": train.get("owl:sameAs") or train.get("@id"),
            "number": train.get("odpt:trainNumber", ""),
            "railway": train.get("odpt:railway", ""),
            "lat": lat, "lon": lon,
            "from": station.get("dc:title", origin),
            "to": target.get("dc:title", destination),
            "position_kind": kind,
            "reported_at": train.get("dc:date"),
            "valid_until": train.get("dct:valid"),
        })
    return {"pins": pins, "total": len(trains["notices"]),
            "unmapped": len(trains["notices"]) - len(pins),
            "updated_at": trains["updated_at"], "stale": trains["stale"],
            "error": trains["error"] or stations["error"],
            "cache_seconds": trains["cache_seconds"]}


class StatusCache:
    """Shared single-flight cache with persisted cooldown and stale-on-error."""

    def __init__(self, path: Path, key: str, ttl: int = 120, fetch=fetch_notices, clock=time.time):
        self.path, self.key = path, key
        self.ttl = max(120, ttl)
        self.fetch, self.clock = fetch, clock
        self.lock = threading.Lock()
        self.notices = []
        self.updated_at = None
        self.next_attempt = 0
        self.failures = 0
        self.error = None
        try:
            saved = json.loads(path.read_text())
            if isinstance(saved.get("notices"), list):
                self.notices = saved["notices"]
                self.updated_at = saved.get("updated_at")
                self.next_attempt = float(saved.get("next_attempt", 0))
                self.failures = int(saved.get("failures", 0))
                self.error = saved.get("error")
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        data = {"notices": self.notices, "updated_at": self.updated_at,
                "next_attempt": self.next_attempt, "failures": self.failures, "error": self.error}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            # Keep serving the in-memory cache if the disk is unavailable.
            pass

    def get(self):
        with self.lock:
            now = self.clock()
            if not self.key:
                self.error = "ODPT_CONSUMER_KEY が未設定です。地図のみ表示しています。"
            elif now >= self.next_attempt:
                # Persist a reservation before sending, to avoid retries on restart.
                self.next_attempt = now + self.ttl
                self._save()
                try:
                    notices = self.fetch(self.key)
                    self.notices = notices
                    self.updated_at = self.clock()
                    self.next_attempt = self.updated_at + self.ttl
                    self.failures = 0
                    self.error = None
                except Exception as exc:
                    self.failures += 1
                    delay = min(1800, self.ttl * 2 ** min(self.failures, 4))
                    if isinstance(exc, HTTPError):
                        if exc.code in (401, 403):
                            delay = max(delay, 1800)
                        if exc.headers:
                            value = exc.headers.get("Retry-After", "")
                            try:
                                delay = max(delay, float(value))
                            except ValueError:
                                try:
                                    delay = max(delay, parsedate_to_datetime(value).timestamp() - self.clock())
                                except (ValueError, TypeError, OverflowError):
                                    pass
                        exc.close()
                    self.next_attempt = self.clock() + delay
                    # Never expose exception text: urllib errors can contain the secret URL.
                    self.error = "情報を更新できません。時間をおいて再試行します。"
                self._save()
            return {
                "notices": self.notices,
                "updated_at": datetime.fromtimestamp(self.updated_at, timezone.utc).isoformat() if self.updated_at is not None else None,
                "stale": bool(self.error) or self.updated_at is None or now - self.updated_at >= self.ttl,
                "error": self.error,
                "retry_in_seconds": max(0, int(self.next_attempt - self.clock())),
                "cache_seconds": self.ttl,
            }


def make_handler(cache: StatusCache, processed: Path, trains=None, stations=None, estimates=None, extras=None):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/api/extra-network", "/api/extra-positions") and extras is not None:
                query = parse_qs(urlsplit(self.path).query)
                mode = query.get("mode", [""])[0]
                if mode not in ("jr", "bus"):
                    self.respond(400, b"Invalid mode", "text/plain")
                    return
                bounds = None
                if "bounds" in query:
                    try:
                        bounds = tuple(float(x) for x in query["bounds"][0].split(","))
                        if len(bounds) != 4 or not all(math.isfinite(x) for x in bounds) or not (-180 <= bounds[0] <= bounds[2] <= 180 and -90 <= bounds[1] <= bounds[3] <= 90):
                            raise ValueError()
                    except ValueError:
                        self.respond(400, b"Invalid bounds", "text/plain")
                        return
                result = extras.network(mode) if path == "/api/extra-network" else extras.positions(mode, bounds, query.get("route", [None])[0])
                self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            if path == "/api/estimates" and estimates is not None:
                self.respond(200, json.dumps(estimates.get(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            if path == "/api/trains" and trains is not None and stations is not None:
                result = train_positions(trains.get(), stations.get())
                self.respond(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            if path == "/api/status":
                self.respond(200, json.dumps(cache.get(), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                return
            files = {
                "/": (STATIC / "index.html", "text/html; charset=utf-8"),
                "/app.js": (STATIC / "app.js", "text/javascript; charset=utf-8"),
                "/trains.js": (STATIC / "trains.js", "text/javascript; charset=utf-8"),
                "/extras.js": (STATIC / "extras.js", "text/javascript; charset=utf-8"),
                "/all.js": (STATIC / "all.js", "text/javascript; charset=utf-8"),
                "/style.css": (STATIC / "style.css", "text/css; charset=utf-8"),
                "/data/railroads": (processed / "tokyo-metro-railroads.geojson", "application/geo+json"),
                "/data/stations": (processed / "tokyo-metro-stations.geojson", "application/geo+json"),
            }
            if path not in files:
                self.respond(404, b"Not found", "text/plain")
                return
            filename, content_type = files[path]
            try:
                body = filename.read_bytes()
            except FileNotFoundError:
                self.respond(404, b"Run transit import-static-railways first", "text/plain")
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
    processed = ROOT / "data" / "processed"
    cache = StatusCache(processed / "realtime" / "web-status-cache.json", os.environ.get("ODPT_CONSUMER_KEY", ""))
    key = os.environ.get("ODPT_CONSUMER_KEY", "")
    trains = StatusCache(processed / "realtime" / "web-train-cache.json", key, fetch=fetch_trains)
    stations = StatusCache(processed / "realtime" / "web-station-cache.json", key, ttl=86400, fetch=fetch_stations)
    from transit_experiment.timetable import TimetableService

    def timetable_cache(route, calendar):
        return StatusCache(processed / "realtime" / f"timetable-{route}-{calendar}.json", key,
                           ttl=86400, fetch=lambda token: fetch_timetables(token, route, calendar))

    estimates = TimetableService(processed, timetable_cache, cache, trains, stations)
    from transit_experiment.gtfs import ExtraService

    def extra_cache(mode, token, fetch):
        return StatusCache(processed / "realtime" / f"gtfs-refresh-{mode}.json", token, ttl=86400, fetch=fetch)

    extras = ExtraService(ROOT, extra_cache)
    server = ThreadingHTTPServer((host, port), make_handler(cache, processed, trains, stations, estimates, extras))
    print(f"Tokyo Metro map: http://{host}:{port} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        estimates.stop.set()
        extras.stop.set()
        server.server_close()
