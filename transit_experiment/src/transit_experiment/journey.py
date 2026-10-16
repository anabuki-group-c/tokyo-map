"""Longest journeys ("Long Route Times"): take as long as possible and still arrive by a deadline.

Follows 最長ルート検索_アルゴリズム設計書 v1.0: maximise the total time T = arrival - departure time (rides,
waits and walks), arrive by the deadline D, never re-enter an area already left, and alight only to change
or to finish. An area is a circle of radius X around a station; the whole path (rides included) is checked.

All modes become one list of timetable hops ("connections"): metro from the ODPT timetables, JR from the
real GTFS or the headway model, Toei bus from its GTFS. Times are service-day seconds (03:00 boundary,
so 25:10 is after midnight). Walking is a straight line times a detour factor, not a street route.
"""
from __future__ import annotations

import bisect
import math
import time
from collections import namedtuple
from datetime import datetime

from transit_experiment.timetable import JST, ROUTES, display_time, service_calendar, timetable_stops

WALK_M_PER_S = 80 / 60
# ponytail: straight line x1.3 stands in for street distance; a road graph would replace it.
WALK_DETOUR = 1.3
TRANSFER_S = 60
ACCESS_M = 1000      # Furthest walk from the origin to a first stop, and from a last stop to the destination.
FOOTPATH_M = 400     # Furthest walk between two stops when changing.
MAX_WINDOW_S = 6 * 3600
AREA_RADIUS_M = 300  # X: default radius of the area around each station.
MERGE_M = 1000       # Same-named stops closer than this are one station (JR and metro 新宿).
TIME_LIMIT_S = 10    # Search budget per request; the best journey so far is returned when it runs out.

Connection = namedtuple("Connection", "dep arr frm to trip line mode metres")
Stop = namedtuple("Stop", "id name lat lon")


def metres(a, b):
    """Great-circle distance between two (lat, lon) points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def walk_seconds(distance_m):
    return distance_m * WALK_DETOUR / WALK_M_PER_S


class Network:
    def __init__(self):
        self.stops, self.connections = {}, []

    def add_trip(self, trip, line, mode, rows):
        """rows: [(Stop, arrival, departure)] in travel order. Hops with impossible times are dropped."""
        for stop, _, _ in rows:
            self.stops.setdefault(stop.id, stop)
        for (a, _, dep), (b, arr, _) in zip(rows, rows[1:]):
            if dep is not None and arr is not None and dep <= arr:
                self.connections.append(Connection(dep, arr, a.id, b.id, trip, line, mode, metres((a.lat, a.lon), (b.lat, b.lon))))

    def extend(self, other):
        self.stops.update(other.stops)
        self.connections.extend(other.connections)
        return self

    def near(self, point, radius):
        """[(stop id, walk seconds)] for stops within radius metres of (lat, lon)."""
        # ponytail: linear scan over all stops (a few thousand); a grid index would make it O(nearby).
        return [(stop.id, walk_seconds(d)) for stop in self.stops.values()
                if (d := metres(point, (stop.lat, stop.lon))) <= radius]


def metro_network(tables, station_records, calendar):
    coords = {s.get("owl:sameAs"): s for s in station_records}
    network, seen = Network(), set()
    for table in tables:
        railway, number = table.get("odpt:railway", ""), table.get("odpt:trainNumber", "")
        if table.get("odpt:calendar") != "odpt.Calendar:" + calendar or (railway, number) in seen:
            continue
        seen.add((railway, number))
        try:
            stops = timetable_stops(table)
        except (ValueError, TypeError):
            continue
        rows, piece = [], 0
        # Through trains continue onto other companies' stations with no coordinates here; split there, and give
        # each piece its own trip id so a ride never jumps the gap.
        for station, arrival, departure in stops + [(None, None, None)]:
            record = coords.get(station)
            try:
                stop = Stop(station, record["dc:title"], float(record["geo:lat"]), float(record["geo:long"]))
            except (TypeError, KeyError, ValueError):
                if len(rows) > 1:
                    network.add_trip(f"metro:{railway}:{number}:{piece}", ROUTES.get(railway.split(".")[-1], railway), "metro", rows)
                    piece += 1
                rows = []
                continue
            rows.append((stop, arrival, departure))
    return network


def jr_model_network(model):
    from transit_experiment.jr_model import ASSUMPTIONS
    network = Network()
    names = {route["id"]: route["name"] for route in model.routes}
    start = ASSUMPTIONS["start_hour"] * 3600
    operating = (ASSUMPTIONS["end_hour"] - ASSUMPTIONS["start_hour"]) * 3600
    dwell = ASSUMPTIONS["dwell_seconds"]
    for route, direction, interval, legs, ends, duration in model.services:
        for k in range(math.ceil(operating / interval)):
            t0 = start + k * interval
            rows = [(Stop("jr:" + origin, origin, a[1], a[0]), t0 + offset, t0 + offset + dwell)
                    for origin, _, a, _, offset, _ in legs]
            _, target, _, b, _, _ = legs[-1]
            rows.append((Stop("jr:" + target, target, b[1], b[0]), t0 + duration, t0 + duration))
            network.add_trip(f"jr-model:{route}:{direction}:{k}", names[route] + "（簡易推定）", "jr", rows)
    return network


def gtfs_network(feed, day, start, end):
    """Trips of the GTFS service day `day` that run during [start, end]."""
    from transit_experiment.gtfs import active_services
    network, key = Network(), day.strftime("%Y%m%d")
    if feed.info and ((feed.info[0] and key < feed.info[0]) or (feed.info[1] and key > feed.info[1])):
        return network
    with feed.connect() as db:
        services = active_services(db, day)
        if not services:
            return network
        sql = ("SELECT t.id, r.name, s.id, s.name, s.lat, s.lon, tm.arrival, tm.departure FROM trips t "
               "JOIN routes r ON r.id=t.route JOIN times tm ON tm.trip=t.id JOIN stops s ON s.id=tm.stop "
               "WHERE t.service IN (" + ",".join("?" * len(services)) + ") AND t.start<=? AND t.end>=? ORDER BY t.id, tm.seq")
        trip, line, rows = None, None, []
        for trip_id, route, stop_id, name, lat, lon, arrival, departure in db.execute(sql, (*services, end, start)):
            if trip_id != trip:
                network.add_trip(f"{feed.mode}:{trip}", line, feed.mode, rows)
                trip, line, rows = trip_id, route, []
            rows.append((Stop(f"{feed.mode}:{stop_id}", name, lat, lon), arrival, departure))
        network.add_trip(f"{feed.mode}:{trip}", line, feed.mode, rows)
    return network


class Areas:
    """Areas of radius X around stations, as bits of an int: `inside` and `exited` sets are int masks."""
    def __init__(self, stops, radius):
        self.radius, self.cell = radius, max(radius, 500) / 111_195
        seeds, members = {}, []  # seeds: name -> [(first stop's point, group index)]
        for stop in stops.values():
            here = seeds.setdefault(stop.name, [])
            index = next((i for point, i in here if metres(point, (stop.lat, stop.lon)) <= MERGE_M), None)
            if index is None:
                here.append(((stop.lat, stop.lon), len(members)))
                members.append([])
                index = len(members) - 1
            members[index].append(stop)
        self.centres = [(sum(m.lat for m in group) / len(group), sum(m.lon for m in group) / len(group)) for group in members]
        self.grid = {}
        for bit, (lat, lon) in enumerate(self.centres):
            self.grid.setdefault((int(lat // self.cell), int(lon // self.cell)), []).append(bit)
        self.cache = {}

    def _near(self, a, b):
        """Area bits whose centres lie in grid cells around the box spanned by points a and b."""
        pad = self.radius / 111_195 / 0.8
        y0, y1 = int((min(a[0], b[0]) - pad) // self.cell), int((max(a[0], b[0]) + pad) // self.cell)
        x0, x1 = int((min(a[1], b[1]) - pad) // self.cell), int((max(a[1], b[1]) + pad) // self.cell)
        return [bit for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) for bit in self.grid.get((y, x), ())]

    def touched(self, a, b):
        """Mask of areas the straight segment a -> b passes through (both ends included)."""
        key = (a, b)
        if key not in self.cache:
            # Flat-earth metres around a; fine for the few kilometres between two stops.
            k = 111_195 * math.cos(math.radians(a[0]))
            bx, by = (b[1] - a[1]) * k, (b[0] - a[0]) * 111_195
            length2 = bx * bx + by * by
            mask = 0
            for bit in self._near(a, b):
                cx, cy = (self.centres[bit][1] - a[1]) * k, (self.centres[bit][0] - a[0]) * 111_195
                t = min(1.0, max(0.0, (cx * bx + cy * by) / length2)) if length2 else 0.0
                if math.hypot(cx - t * bx, cy - t * by) < self.radius:
                    mask |= 1 << bit
            self.cache[key] = mask
        return self.cache[key]

    def at(self, point):
        return self.touched(point, point)

    def move(self, inside, exited, a, b):
        """(inside, exited) after moving a -> b, or None if the move re-enters an area already left."""
        crossed = self.touched(a, b)
        if crossed & exited:
            return None
        now = self.at(b)
        return now, exited | ((inside | crossed) & ~now)


def longest_journey(network, origin, destination, start, deadline, radius=AREA_RADIUS_M,
                    time_limit=TIME_LIMIT_S, clock=time.monotonic):
    """The journey that arrives as late as possible by `deadline`, never re-entering an area it has left.

    Returns (journey or None, complete). complete=False means the time limit cut the search short: the
    journey is the best found so far, and None does not prove that no journey exists.
    """
    conns = sorted((c for c in network.connections if start <= c.dep and c.arr <= deadline), key=lambda c: c.dep)
    stops = network.stops
    access = network.near(origin, ACCESS_M)
    egress = dict(network.near(destination, ACCESS_M))
    footpaths, grid = {}, {}
    # Cells at least FOOTPATH_M wide, so every pair in range sits in neighbouring cells. A longitude degree is
    # cos(lat) times a latitude degree; 0.8 is below cos(lat) everywhere in Tokyo (24-36 degrees north).
    cell_lat, cell_lon = FOOTPATH_M / 111_000, FOOTPATH_M / 111_000 / 0.8
    for stop_id in {c.frm for c in conns} | {c.to for c in conns}:
        stop = stops[stop_id]
        grid.setdefault((int(stop.lat // cell_lat), int(stop.lon // cell_lon)), []).append(stop)
    for (y, x), members in grid.items():
        for a in members:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    for b in grid.get((y + dy, x + dx), ()):
                        if b.id != a.id and (d := metres((a.lat, a.lon), (b.lat, b.lon))) <= FOOTPATH_M:
                            footpaths.setdefault(a.id, []).append((b.id, walk_seconds(d)))

    # Earliest-arrival check with the revisit rule relaxed, run backwards: latest[s] is the latest time one can
    # stand at s, ready to change, and still arrive by the deadline. Past it, no journey from s can make it.
    latest = {s: deadline - w for s, w in egress.items()}
    on_useful_trip = set()
    for c in reversed(conns):
        if c.trip in on_useful_trip or c.arr <= latest.get(c.to, -math.inf):
            on_useful_trip.add(c.trip)
            board_by = c.dep - TRANSFER_S
            if board_by > latest.get(c.frm, -math.inf):
                latest[c.frm] = board_by
                # ponytail: one walking hop per change, not chains of walks.
                for other, walk in footpaths.get(c.frm, ()):
                    latest[other] = max(latest.get(other, -math.inf), board_by - walk)

    by_trip, departures = {}, {}
    for c in conns:
        by_trip.setdefault(c.trip, []).append(c)
    for trip, hops in by_trip.items():
        for i, c in enumerate(hops):
            departures.setdefault(c.frm, []).append((c.dep, trip, i))
    for items in departures.values():
        items.sort()

    # Earliest arrival from the origin with the revisit rule relaxed, run forwards: only hops one can actually
    # catch count. No journey arrives later than the last such hop into a stop near the destination (plus the
    # walk), so reaching that ceiling proves the journey is the longest.
    earliest = {s: start + w for s, w in access}
    boarded, ceiling = set(), None
    for c in conns:
        if c.trip in boarded or earliest.get(c.frm, math.inf) <= c.dep:
            boarded.add(c.trip)
            if c.arr < earliest.get(c.to, math.inf):
                earliest[c.to] = c.arr
                for other, walk in footpaths.get(c.to, ()):
                    earliest[other] = min(earliest.get(other, math.inf), c.arr + walk)
            if c.to in egress and (arrival := c.arr + egress[c.to]) <= deadline:
                ceiling = arrival if ceiling is None else max(ceiling, arrival)
    if ceiling is None:
        return None, True

    areas = Areas(stops, radius)
    point = lambda stop_id: (stops[stop_id].lat, stops[stop_id].lon)
    # A free state stands at a stop: (time, stop, how it got there, inside, exited, legs). `came` is None after
    # the walk from the origin, "walk" after a change on foot, or the trip just left.
    stack = []
    inside0 = areas.at(origin)
    for stop, walk in access:
        moved = areas.move(inside0, 0, origin, point(stop))
        if moved and start + walk <= latest.get(stop, -math.inf) + TRANSFER_S:
            stack.append((start + walk, stop, None, *moved, (("walk", None, stop, start, start + walk),)))

    # ponytail: plain depth-first search over every candidate (the longest simple path is NP-hard), stopped by
    # TIME_LIMIT_S; add per-state upper bounds or dominance rules if the limit is hit too often.
    best, best_arrival, stop_at = None, -math.inf, clock() + time_limit
    while stack:
        if clock() > stop_at:
            return describe_best(best, network, by_trip, origin, destination, start), False
        now, stop, came, inside, exited, legs = stack.pop()
        if came not in (None, "walk"):
            # Just got off a trip: finish here, or change (on foot, or at this stop below).
            if stop in egress and (arrival := now + egress[stop]) <= deadline and arrival > best_arrival:
                if areas.move(inside, exited, point(stop), destination):
                    best, best_arrival = legs + (("walk", stop, None, now, arrival),), arrival
                    if best_arrival >= ceiling:
                        return describe_best(best, network, by_trip, origin, destination, start), True
            for other, walk in footpaths.get(stop, ()):
                if now + walk <= latest.get(other, -math.inf) and (moved := areas.move(inside, exited, point(stop), point(other))):
                    stack.append((now + walk, other, "walk", *moved, legs + (("walk-change", stop, other, now, now + walk),)))
        # Board a later trip here. Waiting for a later one is allowed; the same trip is never re-boarded.
        ready = now + (0 if came is None else TRANSFER_S)
        items = departures.get(stop, [])
        for dep, trip, index in items[bisect.bisect_left(items, (ready,)):]:
            if dep > latest.get(stop, -math.inf) + TRANSFER_S:
                break  # Nothing later from here can still make the deadline.
            if trip == came:
                continue
            on, off, ridden = inside, exited, 0.0
            for position, hop in enumerate(by_trip[trip][index:], index + 1):
                moved = areas.move(on, off, point(hop.frm), point(hop.to))
                if moved is None:
                    break  # Staying on board would re-enter an area already left.
                on, off = moved
                ridden += hop.metres
                if hop.arr <= latest.get(hop.to, -math.inf) or (hop.to in egress and hop.arr + egress[hop.to] <= deadline):
                    stack.append((hop.arr, hop.to, trip, on, off, legs + (("ride", stop, hop.to, dep, hop.arr, trip, index, position, ridden),)))
    return describe_best(best, network, by_trip, origin, destination, start), True


def describe_best(best, network, by_trip, origin, destination, start):
    return None if best is None else describe(best, network, by_trip, origin, destination, start)


def describe(legs, network, by_trip, origin, destination, start):
    stops = network.stops

    def place(stop_id, fallback):
        if stop_id is None:
            return {"name": None, "lat": fallback[0], "lon": fallback[1]}
        stop = stops[stop_id]
        return {"name": stop.name, "lat": stop.lat, "lon": stop.lon}

    out, ride_s, walk_s, ride_m, walk_m = [], 0, 0, 0.0, 0.0
    for leg in legs:
        if leg[0] == "ride":
            _, frm, to, dep, arr, trip, first, last, ridden = leg
            hops = by_trip[trip][first:last]
            path = [place(hops[0].frm, None)] + [place(h.to, None) for h in hops]
            ride_s, ride_m = ride_s + arr - dep, ride_m + ridden
            out.append({"mode": hops[0].mode, "line": hops[0].line, "from": path[0], "to": path[-1], "stops": path,
                        "depart": display_time(dep), "arrive": display_time(arr), "distance_m": round(ridden)})
        else:
            kind, frm, to, dep, arr = leg
            a, b = place(frm, origin), place(to, destination)
            distance = metres((a["lat"], a["lon"]), (b["lat"], b["lon"]))
            walk_s, walk_m = walk_s + arr - dep, walk_m + distance
            if arr - dep < 1:
                continue
            out.append({"mode": "walk", "from": a, "to": b, "depart": display_time(dep), "arrive": display_time(arr),
                        "distance_m": round(distance)})
    total, ride_s, walk_s = round(legs[-1][4] - start), round(ride_s), round(walk_s)
    # Wait = everything that is neither riding nor walking, including the first wait at the origin.
    return {"total_seconds": total, "ride_seconds": ride_s, "walk_seconds": walk_s,
            "wait_seconds": total - ride_s - walk_s, "ride_distance_m": round(ride_m),
            "distance_m": round(ride_m + walk_m), "depart": display_time(start), "arrive": display_time(legs[-1][4]),
            "legs": out}


class JourneyPlanner:
    """Builds the network from the server's metro timetable and GTFS services, per request window."""
    def __init__(self, timetables, extras):
        self.timetables, self.extras = timetables, extras
        self.base_key, self.base = None, None

    def plan(self, origin, destination, depart, arrive, radius=AREA_RADIUS_M, now=None):
        now = (now or datetime.now(JST)).astimezone(JST)
        day, calendar = service_calendar(now)
        start = depart if depart is not None else (now - datetime.combine(day, datetime.min.time(), JST)).total_seconds()
        if not start < arrive <= start + MAX_WINDOW_S:
            raise ValueError(f"到着希望時刻は出発時刻より後、{MAX_WINDOW_S // 3600}時間以内にしてください。")
        network, sources = Network(), []
        with self.timetables.snap_lock:
            tables = [t for (route, cal), snap in self.timetables.snapshots.items()
                      if cal == calendar and snap and snap.get("updated_at") for t in snap["notices"]]
        if tables:
            # ponytail: rebuilt per request (~0.5 s); cache by (day, timetable dates) if it becomes slow.
            network.extend(metro_network(tables, self.timetables.stations.get()["notices"], calendar))
            sources.append("東京メトロ（ODPT 実時刻表）")
        for mode in ("jr", "bus"):
            feed = self.extras.feeds.get(mode)
            if feed is None:
                continue
            if hasattr(feed, "services"):
                network.extend(jr_model_network(feed))
                sources.append("JR東日本（簡易推定：仮の運転間隔・速度。実時刻表ではありません）")
            else:
                network.extend(gtfs_network(feed, day, start, arrive))
                sources.append("都営バス（GTFS 時刻表）" if mode == "bus" else "JR東日本（GTFS 時刻表）")
        result, complete = longest_journey(network, origin, destination, start, arrive, radius)
        return {"journey": result, "complete": complete, "radius_m": radius, "service_date": day.isoformat(), "calendar": calendar,
                "window": {"depart": display_time(start), "arrive": display_time(arrive)}, "sources": sources,
                "notes": ["移動距離と再訪判定は駅間・徒歩区間を直線で近似しています（線路・道路の形状は未使用）。",
                          "徒歩は直線距離×1.3、分速80mで見積もっています。",
                          "到着はデータ上の判定です。遅延・運休は反映していません。"]}
