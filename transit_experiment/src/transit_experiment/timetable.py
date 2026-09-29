"""Timetable-derived positions, explicitly estimates, never fabricated headways."""
from __future__ import annotations

import csv
import heapq
import json
import math
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROUTES = dict(zip(
    ("Ginza", "Marunouchi", "MarunouchiBranch", "Hibiya", "Tozai", "Chiyoda", "Yurakucho", "Hanzomon", "Namboku", "Fukutoshin"),
    ("銀座線", "丸ノ内線", "丸ノ内線", "日比谷線", "東西線", "千代田線", "有楽町線", "半蔵門線", "南北線", "副都心線"),
))
HOLIDAY_SOURCE = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"


def service_calendar(now):
    """Before 03:00 JST belongs to the previous railway service day."""
    local = now.astimezone(JST)
    day = (local - timedelta(hours=3)).date()
    with Path(__file__).with_name("holidays.csv").open(encoding="shift_jis") as source:
        rows = list(csv.reader(source))[1:]
    holidays = {datetime.strptime(row[0], "%Y/%m/%d").date() for row in rows if row}
    if day.year > max(d.year for d in holidays) or day.year < min(d.year for d in holidays):
        raise ValueError("祝日データの対応年外です。カレンダーを更新してください。")
    holiday = day.weekday() >= 5 or day in holidays or (day.month == 12 and day.day >= 30) or (day.month == 1 and day.day <= 3)
    return day, "SaturdayHoliday" if holiday else "Weekday"


def seconds(value):
    hour, minute = map(int, value.split(":"))
    if hour < 3:
        hour += 24
    return hour * 3600 + minute * 60


def timetable_stops(table):
    stops = []
    for obj in table.get("odpt:trainTimetableObject", []):
        station = obj.get("odpt:departureStation") or obj.get("odpt:arrivalStation")
        arrival = obj.get("odpt:arrivalTime") or obj.get("odpt:departureTime")
        departure = obj.get("odpt:departureTime") or arrival
        if not station or not arrival:
            continue
        stops.append((station, seconds(arrival), seconds(departure)))
    return stops


def display_time(value):
    """Render a service-day time without losing the after-midnight distinction."""
    prefix = "翌" if value >= 86400 else ""
    return f"{prefix}{int(value // 3600) % 24:02d}:{int(value // 60) % 60:02d}"


def scheduled_window(stops, elapsed):
    for index, (station, arrival, departure) in enumerate(stops):
        if arrival <= elapsed <= departure:
            return display_time(arrival), display_time(departure), "dwell"
        if index + 1 < len(stops) and departure < elapsed < stops[index + 1][1]:
            return display_time(departure), display_time(stops[index + 1][1]), "travel"
    return None, None, None


def active_segment(stops, elapsed):
    if not stops or elapsed < stops[0][1] or elapsed > stops[-1][2]:
        return None
    for i, (station, arrival, departure) in enumerate(stops):
        if arrival <= elapsed <= departure:
            return station, station, 0.0
        if i + 1 < len(stops):
            target, next_arrival, _ = stops[i + 1]
            if departure < elapsed < next_arrival:
                return station, target, (elapsed - departure) / (next_arrival - departure)
    return None


def distance(a, b):
    return math.hypot((a[0] - b[0]) * 0.81, a[1] - b[1])


class RailGeometry:
    """Cache shortest paths on the static N02 network; fall back to a marked chord."""
    def __init__(self, path, route_names=None):
        self.names = dict(ROUTES if route_names is None else route_names)
        self.graphs, self.paths = {}, {}
        try:
            features = json.loads(path.read_text())["features"]
        except (OSError, ValueError):
            features = []
        for feature in features:
            name = feature.get("properties", {}).get("N02_003", "")
            route = next((name_ja for name_ja in self.names.values() if name_ja in name), None)
            if not route:
                continue
            graph = self.graphs.setdefault(route, {})
            geometry = feature["geometry"]
            lines = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
            for line in lines:
                for a, b in zip(line, line[1:]):
                    a, b = tuple(round(v, 5) for v in a), tuple(round(v, 5) for v in b)
                    length = distance(a, b)
                    graph.setdefault(a, {})[b] = length
                    graph.setdefault(b, {})[a] = length

    def heading(self, route, a, b, fraction):
        before, _ = self.position(route, a, b, max(0, fraction - 0.002))
        after, _ = self.position(route, a, b, min(1, fraction + 0.002))
        dx, dy = (after[0] - before[0]) * 0.81, after[1] - before[1]
        if abs(dx) + abs(dy) < 1e-12:
            return None
        # Clockwise degrees from north, matching the map's unrotated viewport.
        return round(math.degrees(math.atan2(dx, dy)) % 360, 1)

    def position(self, route, a, b, fraction):
        key = (route, a, b)
        if key not in self.paths:
            graph = self.graphs.get(self.names.get(route), {})
            points = [a, b]
            approximate = True
            if graph:
                start = min(graph, key=lambda node: distance(node, a))
                end = min(graph, key=lambda node: distance(node, b))
                if distance(start, a) < 0.003 and distance(end, b) < 0.003 and (start != end or a == b):
                    queue, lengths, previous = [(0, start)], {start: 0}, {}
                    while queue:
                        cost, node = heapq.heappop(queue)
                        if cost != lengths[node]:
                            continue
                        if node == end:
                            points = [node]
                            while node != start:
                                node = previous[node]
                                points.append(node)
                            points.reverse()
                            approximate = False
                            break
                        for target, length in graph[node].items():
                            candidate = cost + length
                            if candidate < lengths.get(target, math.inf):
                                lengths[target], previous[target] = candidate, node
                                heapq.heappush(queue, (candidate, target))
            lengths = [distance(x, y) for x, y in zip(points, points[1:])]
            self.paths[key] = points, lengths, sum(lengths), approximate
        points, lengths, total, approximate = self.paths[key]
        remainder = total * fraction
        for i, length in enumerate(lengths):
            if remainder <= length and length:
                t = remainder / length
                x, y = points[i], points[i + 1]
                return [x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t], approximate
            remainder -= length
        return list(points[-1]), approximate


def estimate_positions(tables, station_records, live, notices, now, geometry):
    day, calendar = service_calendar(now)
    elapsed = (now.astimezone(JST) - datetime.combine(day, datetime.min.time(), JST)).total_seconds()
    stations = {s.get("owl:sameAs"): s for s in station_records}
    delays = {}
    if not live.get("stale"):
        for train in live.get("notices", []):
            # Only apply fresh numeric, train-specific delays; never parse notice prose.
            try:
                reported = datetime.fromisoformat(train["dc:date"])
                age = (now - reported).total_seconds()
                if not 0 <= age <= 300:
                    continue
                valid = train.get("dct:valid")
                if valid and now > datetime.fromisoformat(valid):
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            delay = train.get("odpt:delay")
            if isinstance(delay, (int, float)) and not isinstance(delay, bool) and 0 <= delay <= 21600:
                delays[(train.get("odpt:railway"), train.get("odpt:trainNumber"))] = delay
    alerts, normal = set(), set()
    if not notices.get("stale"):
        for notice in notices.get("notices", []):
            text = notice.get("odpt:trainInformationText", {})
            text = text.get("ja", "") if isinstance(text, dict) else text
            railway = notice.get("odpt:railway")
            if text and "平常どおり" in text:
                normal.add(railway)
            elif text:
                alerts.add(railway)
    pins, skipped, seen = [], 0, set()
    for table in tables:
        if table.get("odpt:calendar") != "odpt.Calendar:" + calendar:
            continue
        railway = table.get("odpt:railway", "")
        number = table.get("odpt:trainNumber", "")
        key = (railway, number)
        if key in seen:
            continue
        delay = delays.get(key)
        try:
            stops = timetable_stops(table)
            segment = active_segment(stops, elapsed - (delay or 0))
        except (ValueError, TypeError):
            continue
        if not segment:
            continue
        origin, target, fraction = segment
        planned_start, planned_end, planned_kind = scheduled_window(stops, elapsed - (delay or 0))
        first, last = stations.get(origin), stations.get(target)
        if not first or not last:
            skipped += 1
            continue
        try:
            a = (float(first["geo:long"]), float(first["geo:lat"]))
            b = (float(last["geo:long"]), float(last["geo:lat"]))
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        route = railway.split(".")[-1]
        coordinates, chord = geometry.position(route, a, b, fraction)
        heading = geometry.heading(route, a, b, fraction)
        heading_target = last.get("dc:title", target)
        if origin == target:
            # At a dwell use the outgoing segment; at the terminus use arrival direction.
            index = next((i for i, stop in enumerate(stops) if stop[0] == origin
                          and stop[1] <= elapsed - (delay or 0) <= stop[2]), None)
            if index is not None:
                neighbor = stations.get(stops[index + 1][0]) if index + 1 < len(stops) else None
                incoming = False
                if not neighbor and index > 0:
                    neighbor = stations.get(stops[index - 1][0])
                    incoming = True
                if neighbor:
                    try:
                        point = (float(neighbor["geo:long"]), float(neighbor["geo:lat"]))
                        heading = geometry.heading(route, point, a, 1) if incoming else geometry.heading(route, a, point, 0)
                        heading_target = first.get("dc:title", origin) if incoming else neighbor.get("dc:title", "")
                    except (KeyError, ValueError, TypeError):
                        pass
        seen.add(key)
        pins.append({"number": number, "railway": railway, "lon": coordinates[0], "lat": coordinates[1],
                     "from": first.get("dc:title", origin), "to": last.get("dc:title", target),
                     "planned_start": planned_start, "planned_end": planned_end, "planned_kind": planned_kind,
                     "basis": "published_timetable",
                     "progress": round(fraction, 3), "heading": heading, "heading_target": heading_target, "delay_seconds": delay,
                     "alert": railway in alerts or bool(delay),
                     "status_known": railway in alerts or railway in normal,
                     "geometry_approximate": chord, "timetable_updated_at": table.get("dc:date")})
    return {"pins": pins, "unmapped": skipped, "calendar": calendar, "service_date": day.isoformat(),
            "generated_at": now.isoformat(), "status_stale": notices.get("stale", True),
            "basis": "published_timetable", "timetable_source": "https://api.odpt.org/api/v4/odpt:TrainTimetable"}


class TimetableService:
    """Warm daily per-line/calendar caches serially, never fan out from page loads."""
    def __init__(self, processed, cache_factory, status, trains, stations):
        self.processed, self.cache_factory = processed, cache_factory
        self.status, self.trains, self.stations = status, trains, stations
        self.snapshots, self.snap_lock = {}, threading.Lock()
        self.geometry = RailGeometry(processed / "tokyo-metro-railroads.geojson")
        self.result_lock, self.result, self.result_at = threading.Lock(), None, 0
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.warm, daemon=True)
        self.worker.start()

    def warm(self):
        caches = {}
        while not self.stop.is_set():
            try:
                _, calendar = service_calendar(datetime.now(JST))
            except ValueError:
                self.stop.wait(60)
                continue
            for route in ROUTES:
                if self.stop.is_set():
                    return
                key = (route, calendar)
                if key not in caches:
                    caches[key] = self.cache_factory(route, calendar)
                snapshot = caches[key].get()
                with self.snap_lock:
                    self.snapshots[key] = snapshot
                # Separate route downloads, including at startup, by at least 2 seconds.
                self.stop.wait(2)
            self.stop.wait(60)

    def get(self):
        with self.result_lock:
            if self.result is not None and time.monotonic() - self.result_at < 15:
                return self.result
            now = datetime.now(JST)
            try:
                _, calendar = service_calendar(now)
            except ValueError as exc:
                return {"pins": [], "error": str(exc), "generated_at": now.isoformat()}
            with self.snap_lock:
                snapshots = {route: self.snapshots.get((route, calendar)) for route in ROUTES}
            ready, tables, missing, stale, failed = [], [], [], [], []
            for route, snapshot in snapshots.items():
                if snapshot and snapshot.get("error"):
                    failed.append(route)
                if not snapshot or not snapshot.get("updated_at"):
                    missing.append(route)
                    continue
                # Old failed caches can be useful briefly, but not forever after a revision.
                age = (now - datetime.fromisoformat(snapshot["updated_at"])).total_seconds()
                if age > 7 * 86400:
                    missing.append(route)
                    continue
                ready.append(route)
                tables.extend(snapshot["notices"])
                if snapshot.get("stale"):
                    stale.append(route)
            result = estimate_positions(tables, self.stations.get()["notices"], self.trains.get(),
                                        self.status.get(), now, self.geometry)
            result.update({"ready_routes": ready, "pending_routes": missing, "stale_routes": stale,
                           "timetable_count": len(tables), "failed_routes": failed, "error": None})
            self.result, self.result_at = result, time.monotonic()
            return result
