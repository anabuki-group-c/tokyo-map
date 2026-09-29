"""Streaming GTFS importer and shared, scheduled-position estimator."""
from __future__ import annotations

import bisect
import csv
import io
import json
import math
import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from transit_experiment.timetable import JST, distance

SOURCES = {
    "bus": "https://api-public.odpt.org/api/v4/files/Toei/data/ToeiBus-GTFS.zip",
    "jr": "https://api-challenge.odpt.org/api/v4/files/JR-East/data/JR-East-Train-GTFS.zip",
}


def gtfs_seconds(value):
    if not value:
        return None
    h, m, s = map(int, value.split(":"))
    return h * 3600 + m * 60 + s


def rows(archive, name):
    if name not in archive.namelist():
        return
    with archive.open(name) as source:
        yield from csv.DictReader(io.TextIOWrapper(source, encoding="utf-8-sig"))


def import_feed(source: Path, target: Path):
    """No ZIP extraction; stream stop_times instead of loading a million dictionaries."""
    temporary = target.with_suffix(".building")
    temporary.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(temporary)
    try:
        db.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE routes(id TEXT PRIMARY KEY, name TEXT, color TEXT);
            CREATE TABLE stops(id TEXT PRIMARY KEY, name TEXT, lon REAL, lat REAL);
            CREATE TABLE trips(id TEXT PRIMARY KEY, route TEXT, service TEXT, shape TEXT, headsign TEXT, start INTEGER, end INTEGER);
            CREATE TABLE times(trip TEXT, seq INTEGER, stop TEXT, arrival INTEGER, departure INTEGER);
            CREATE TABLE calendar(service TEXT, start TEXT, end TEXT, weekdays TEXT);
            CREATE TABLE exceptions(service TEXT, day TEXT, kind TEXT);
            CREATE TABLE shapes(id TEXT, seq INTEGER, lon REAL, lat REAL);
            CREATE TABLE info(start TEXT, end TEXT, version TEXT);
        """)
        with zipfile.ZipFile(source) as archive:
            if sum(item.file_size for item in archive.infolist()) > 800_000_000:
                raise ValueError("GTFS uncompressed size too large")
            for required in ("routes.txt", "stops.txt", "trips.txt", "stop_times.txt"):
                if required not in archive.namelist():
                    raise ValueError("Missing required GTFS file")
            db.executemany("INSERT INTO routes VALUES(?,?,?)", (
                (r["route_id"], r.get("route_short_name") or r.get("route_long_name") or r["route_id"], r.get("route_color") or "53b96b")
                for r in rows(archive, "routes.txt")))
            db.executemany("INSERT INTO stops VALUES(?,?,?,?)", (
                (r["stop_id"], r.get("stop_name", r["stop_id"]), float(r["stop_lon"]), float(r["stop_lat"]))
                for r in rows(archive, "stops.txt") if r.get("stop_lon") and r.get("stop_lat")))
            db.executemany("INSERT INTO trips VALUES(?,?,?,?,?,NULL,NULL)", (
                (r["trip_id"], r["route_id"], r["service_id"], r.get("shape_id", ""), r.get("trip_headsign", ""))
                for r in rows(archive, "trips.txt")))
            db.executemany("INSERT INTO times VALUES(?,?,?,?,?)", (
                (r["trip_id"], int(r["stop_sequence"]), r["stop_id"],
                 gtfs_seconds(r.get("arrival_time") or r.get("departure_time")),
                 gtfs_seconds(r.get("departure_time") or r.get("arrival_time")))
                for r in rows(archive, "stop_times.txt")))
            db.executemany("INSERT INTO calendar VALUES(?,?,?,?)", (
                (r["service_id"], r["start_date"], r["end_date"], "".join(r[d] for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")))
                for r in rows(archive, "calendar.txt")))
            db.executemany("INSERT INTO exceptions VALUES(?,?,?)", (
                (r["service_id"], r["date"], r["exception_type"]) for r in rows(archive, "calendar_dates.txt")))
            db.executemany("INSERT INTO shapes VALUES(?,?,?,?)", (
                (r["shape_id"], int(r["shape_pt_sequence"]), float(r["shape_pt_lon"]), float(r["shape_pt_lat"]))
                for r in rows(archive, "shapes.txt")))
            db.executemany("INSERT INTO info VALUES(?,?,?)", (
                (r.get("feed_start_date", ""), r.get("feed_end_date", ""), r.get("feed_version", ""))
                for r in rows(archive, "feed_info.txt")))
        db.executescript("""
            CREATE INDEX times_trip ON times(trip,seq);
            CREATE INDEX shapes_id ON shapes(id,seq);
            CREATE INDEX trips_route ON trips(route);
            CREATE INDEX exception_day ON exceptions(day);
            UPDATE trips SET start=(SELECT MIN(arrival) FROM times WHERE trip=trips.id),
                             end=(SELECT MAX(departure) FROM times WHERE trip=trips.id);
            CREATE INDEX trips_active ON trips(service,start,end);
        """)
        db.commit()
    finally:
        db.close()
    os.replace(temporary, target)


def active_services(db, day):
    value = day.strftime("%Y%m%d")
    active = {r[0] for r in db.execute("SELECT service,weekdays FROM calendar WHERE start<=? AND end>=?", (value, value)) if r[1][day.weekday()] == "1"}
    for service, kind in db.execute("SELECT service,kind FROM exceptions WHERE day=?", (value,)):
        if kind == "1":
            active.add(service)
        elif kind == "2":
            active.discard(service)
    return active


def heading(a, b):
    dx, dy = (b[0] - a[0]) * 0.81, b[1] - a[1]
    return round(math.degrees(math.atan2(dx, dy)) % 360, 1) if abs(dx) + abs(dy) > 1e-12 else None


class Feed:
    def __init__(self, path, mode):
        self.path, self.mode = path, mode
        self.shapes, self.stop_cache, self.route_shapes = {}, {}, {}
        self.lock = threading.Lock()
        with self.connect() as db:
            self.routes = {r[0]: {"id": r[0], "name": r[1], "color": "#" + r[2]} for r in db.execute("SELECT * FROM routes ORDER BY name")}
            self.info = db.execute("SELECT * FROM info LIMIT 1").fetchone()
            for shape, _, lon, lat in db.execute("SELECT * FROM shapes ORDER BY id,seq"):
                self.shapes.setdefault(shape, []).append((lon, lat))
            for route, shape in db.execute("SELECT DISTINCT route,shape FROM trips WHERE shape!=''"):
                if shape in self.shapes:
                    self.route_shapes.setdefault(route, []).append(shape)
            # Preserve ordered stop-only patterns if shapes.txt is absent.
            if not self.shapes:
                for trip, route in db.execute("SELECT MIN(id),route FROM trips GROUP BY route,headsign"):
                    self.route_shapes.setdefault(route, []).append(trip)
                    self.shapes[trip] = [(r[3], r[4]) for r in self.stops(db, trip)]
        self.cumulative = {}
        for shape, points in self.shapes.items():
            lengths = [0.0]
            for a, b in zip(points, points[1:]):
                lengths.append(lengths[-1] + distance(a, b))
            self.cumulative[shape] = lengths
        self.matches = {}
        self.position_snapshot = None
        self.snapshot_at = 0

    @contextmanager
    def connect(self):
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            yield db
        finally:
            db.close()

    def stops(self, db, trip):
        if trip not in self.stop_cache:
            self.stop_cache[trip] = list(db.execute("SELECT t.arrival,t.departure,s.name,s.lon,s.lat,s.id FROM times t JOIN stops s ON s.id=t.stop WHERE t.trip=? ORDER BY t.seq", (trip,)))
            if len(self.stop_cache) > 4000:
                self.stop_cache = {trip: self.stop_cache[trip]}
        return self.stop_cache[trip]

    def match(self, shape, stops):
        # Monotonic projection of the entire stop sequence preserves loops/direction.
        key = (shape, tuple(stop[5] for stop in stops))
        if key not in self.matches:
            points = self.shapes[shape]
            cumulative = self.cumulative[shape]
            start, offsets, error = 0, [], 0
            for stop in stops:
                target = (stop[3], stop[4])
                best = None
                for i in range(start, len(points) - 1):
                    a, b = points[i], points[i + 1]
                    dx, dy = (b[0] - a[0]) * .81, b[1] - a[1]
                    length2 = dx * dx + dy * dy
                    t = max(0, min(1, (((target[0] - a[0]) * .81 * dx + (target[1] - a[1]) * dy) / length2))) if length2 else 0
                    offset = cumulative[i] + (cumulative[i + 1] - cumulative[i]) * t
                    if offsets and offset < offsets[-1] - 1e-9:
                        continue
                    point = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    candidate = (distance(target, point), i, offset)
                    if best is None or candidate < best:
                        best = candidate
                if best is None:
                    error = 1
                    offsets.append(cumulative[-1])
                else:
                    gap, start, offset = best
                    error = max(error, gap)
                    offsets.append(offset)
            self.matches[key] = offsets if error < .005 else None
        return self.matches[key]

    def point_on_shape(self, shape, offset):
        cumulative, points = self.cumulative[shape], self.shapes[shape]
        i = max(0, min(len(points) - 2, bisect.bisect_right(cumulative, offset) - 1))
        length = cumulative[i + 1] - cumulative[i]
        fraction = (offset - cumulative[i]) / length if length else 0
        a, b = points[i], points[i + 1]
        return [a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction], heading(a, b)

    def network(self):
        features = []
        for route, ids in self.route_shapes.items():
            features.append({"type": "Feature", "properties": {"route": route}, "geometry": {
                "type": "MultiLineString", "coordinates": [self.shapes[s] for s in ids if len(self.shapes[s]) > 1]}})
        stations = {}
        if self.mode == "jr":
            with self.connect() as db:
                for sid, name, lon, lat, route in db.execute("SELECT DISTINCT s.id,s.name,s.lon,s.lat,t.route FROM stops s JOIN times tm ON tm.stop=s.id JOIN trips t ON t.id=tm.trip"):
                    station = stations.setdefault(sid, {"id": sid, "name": name, "lon": lon, "lat": lat, "routes": []})
                    station["routes"].append(route)
        return {"routes": list(self.routes.values()), "stations": list(stations.values()), "geojson": {"type": "FeatureCollection", "features": features},
                "source": "東京都交通局 / 都営バス GTFS (CC BY 4.0)" if self.mode == "bus" else "JR東日本 / ODPT チャレンジ限定データ",
                "message": "GTFS時刻表による推定。遅延・運休情報は未連携です。", "ready": True}

    def positions(self, now=None):
        now = now or datetime.now(JST)
        with self.lock:
            if self.position_snapshot is not None and time.monotonic() - self.snapshot_at < 20:
                return self.position_snapshot
            pins, missing = [], 0
            with self.connect() as db:
                for day in (now.date(), now.date() - timedelta(days=1)):
                    date_key = day.strftime("%Y%m%d")
                    if self.info and ((self.info[0] and date_key < self.info[0]) or (self.info[1] and date_key > self.info[1])):
                        continue
                    services = active_services(db, day)
                    if not services:
                        continue
                    elapsed = (now - datetime.combine(day, datetime.min.time(), JST)).total_seconds()
                    sql = "SELECT id,route,shape,headsign FROM trips WHERE service IN (" + ",".join("?" for _ in services) + ") AND start<=? AND end>=?"
                    for trip, route, shape, headsign in db.execute(sql, (*services, elapsed, elapsed)):
                        stops = self.stops(db, trip)
                        segment = None
                        for i, stop in enumerate(stops):
                            arrival, departure = stop[:2]
                            if arrival is None or departure is None:
                                continue
                            if arrival <= elapsed <= departure:
                                segment = (i, i + 1, 0) if i + 1 < len(stops) else (i - 1, i, 1) if i else None
                                break
                            if i + 1 < len(stops) and stops[i + 1][0] is not None and departure < elapsed < stops[i + 1][0]:
                                segment = (i, i + 1, (elapsed - departure) / (stops[i + 1][0] - departure))
                                break
                        if not segment:
                            missing += 1
                            continue
                        i, j, progress = segment
                        first, last = stops[i], stops[j]
                        point = [first[3] + (last[3] - first[3]) * progress, first[4] + (last[4] - first[4]) * progress]
                        bearing = heading(first[3:5], last[3:5])
                        approximate = True
                        if shape in self.shapes and len(self.shapes[shape]) > 1:
                            offsets = self.match(shape, stops)
                            if offsets:
                                point, bearing = self.point_on_shape(shape, offsets[i] + (offsets[j] - offsets[i]) * progress)
                                approximate = False
                        pins.append({"id": date_key + ":" + trip, "number": trip, "route": route, "headsign": headsign,
                                     "from": first[2], "to": last[2], "lon": point[0], "lat": point[1],
                                     "heading": bearing, "geometry_approximate": approximate})
            result = {"pins": pins, "generated_at": now.isoformat(), "unmapped": missing, "ready": True,
                      "message": "時刻表による推定（遅延・運休未反映）。道路渋滞による誤差があります。" if self.mode == "bus" else "時刻表による推定（遅延・運休未反映）。"}
            self.position_snapshot, self.snapshot_at = result, time.monotonic()
            return result


class ExtraService:
    def __init__(self, root, cache_factory):
        self.root, self.cache_factory = root, cache_factory
        self.feeds, self.messages = {}, {"bus": "都営バスの時刻表を準備中です。", "jr": "JRの時刻表未取得。ODPT_CHALLENGE_KEY が必要です（通常キーでは403）。"}
        self.stop = threading.Event()
        self.network_cache = {}
        threading.Thread(target=self.warm, daemon=True).start()

    def prepare(self, mode, key):
        source = self.root / "data" / "raw" / f"{mode}-gtfs.zip"
        target = self.root / "data" / "processed" / f"{mode}-gtfs.sqlite"
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists() or time.time() - source.stat().st_mtime >= 86400:
            url = SOURCES[mode]
            if mode == "jr":
                url += "?" + urlencode({"acl:consumerKey": key})
            with urlopen(url, timeout=90) as response:
                temporary = source.with_suffix(".download")
                total = 0
                with temporary.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 100_000_000:
                            raise ValueError("GTFS download too large")
                        output.write(chunk)
                if not zipfile.is_zipfile(temporary):
                    raise ValueError("Invalid GTFS archive")
                os.replace(temporary, source)
        if not target.exists() or source.stat().st_mtime > target.stat().st_mtime:
            import_feed(source, target)
        feed = Feed(target, mode)
        self.network_cache[mode] = feed.network()
        self.feeds[mode] = feed
        return [{"ready": True}]

    def warm(self):
        keys = {"bus": "public", "jr": os.environ.get("ODPT_CHALLENGE_KEY", "")}
        caches = {mode: self.cache_factory(mode, key, lambda token, mode=mode: self.prepare(mode, token)) for mode, key in keys.items()}
        for mode in keys:
            target = self.root / "data" / "processed" / f"{mode}-gtfs.sqlite"
            if target.exists():
                try:
                    feed = Feed(target, mode)
                    self.network_cache[mode], self.feeds[mode] = feed.network(), feed
                except Exception:
                    self.messages[mode] = "保存済みGTFSを読み込めません。再取得待ちです。"
        if "jr" not in self.feeds:
            try:
                from transit_experiment.jr_model import JRModel
                model = JRModel(self.root / "data" / "processed")
                if model.routes:
                    self.network_cache["jr"], self.feeds["jr"] = model.network(), model
                    self.messages["jr"] = ""
            except (OSError, ValueError, KeyError):
                self.messages["jr"] = "JRの簡易モデルを作成できません。駅・路線データを確認してください。"
        while not self.stop.is_set():
            for mode, cache in caches.items():
                if keys[mode]:
                    snapshot = cache.get()
                    if snapshot["error"]:
                        self.messages[mode] = "GTFSを更新できません。キー・利用権限を確認してください。保存データがあれば利用します。"
                    elif mode in self.feeds:
                        self.messages[mode] = ""
                if self.stop.wait(2):
                    return
            self.stop.wait(60)

    def network(self, mode):
        if mode in self.network_cache:
            return {**self.network_cache[mode], "warning": self.messages.get(mode)}
        if mode == "jr":
            path = self.root / "data" / "processed" / "jr-east-railroads.geojson"
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                raw = {"features": []}
            grouped = {}
            for feature in raw["features"]:
                name = feature["properties"]["N02_003"]
                grouped.setdefault(name, []).append(feature["geometry"]["coordinates"])
            station_path = path.with_name("jr-east-stations.geojson")
            try:
                station_features = json.loads(station_path.read_text())["features"]
            except (OSError, ValueError, KeyError):
                station_features = []
            stations = {}
            for feature in station_features:
                props, geometry = feature["properties"], feature["geometry"]
                if geometry["type"] == "LineString" and geometry["coordinates"]:
                    first, last = geometry["coordinates"][0], geometry["coordinates"][-1]
                    lon, lat = (first[0] + last[0]) / 2, (first[1] + last[1]) / 2
                elif geometry["type"] == "Point":
                    lon, lat = geometry["coordinates"][:2]
                else:
                    continue
                name = props.get("N02_005", "駅名不明")
                key = props.get("N02_005g") or props.get("N02_005c") or f"{name}:{lon}:{lat}"
                station = stations.setdefault(key, {"id": key, "name": name, "lon": lon, "lat": lat, "routes": []})
                route = props.get("N02_003")
                if route and route not in station["routes"]:
                    station["routes"].append(route)
            colors = ["#80c241", "#f15a22", "#00b2e5", "#ffd400", "#e21f26", "#9acb3c"]
            return {"routes": [{"id": name, "name": name, "color": colors[i % len(colors)]} for i, name in enumerate(sorted(grouped))],
                    "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"route": name},
                        "geometry": {"type": "MultiLineString", "coordinates": lines}} for name, lines in grouped.items()]},
                    "stations": list(stations.values()),
                    "ready": False, "message": self.messages[mode] + " 路線名はN02の線路名称で、運行系統名とは異なります。",  "source": "国土数値情報 N02 (2022年) / JR東日本"}
        return {"routes": [], "geojson": {"type": "FeatureCollection", "features": []}, "ready": False, "message": self.messages[mode]}

    def positions(self, mode, bounds=None, route=None):
        if mode not in self.feeds:
            return {"pins": [], "ready": False, "message": self.messages[mode], "generated_at": datetime.now(JST).isoformat()}
        result = self.feeds[mode].positions()
        pins = result["pins"]
        if route:
            pins = [p for p in pins if p["route"] == route]
        if bounds:
            west, south, east, north = bounds
            pins = [p for p in pins if west <= p["lon"] <= east and south <= p["lat"] <= north]
        return {**result, "pins": pins[:600], "total_visible": len(pins), "limited": len(pins) > 600,
                "warning": self.messages.get(mode)}
