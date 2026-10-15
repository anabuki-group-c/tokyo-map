"""Longest-ride journeys ("Long Route Times"): ride as far as possible and still arrive by a deadline.

All modes become one list of timetable hops ("connections"): metro from the ODPT timetables, JR from the
real GTFS or the headway model, Toei bus from its GTFS. Times are service-day seconds (03:00 boundary,
so 25:10 is after midnight). Walking is a straight line times a detour factor, not a street route.
"""
from __future__ import annotations

import bisect
import math
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


def longest_journey(network, origin, destination, start, deadline, beam=200, max_rides=8):
    """Approximate the journey that rides the most metres, arrives by `deadline`, and passes no station twice.

    A station is its name, so JR and metro 新宿 count as one station. Returns None if nothing arrives in time.
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

    # Backward pass: latest[s] is the latest time one can stand at s and still arrive by the deadline.
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

    def finished(state):
        distance, time, stop, _, legs = state
        walk = egress[stop]
        return distance, legs + (("walk", stop, None, time, time + walk),)

    # ponytail: beam search keeps the `beam` farthest-riding partial journeys per round, so it can miss the true
    # optimum (longest simple path is NP-hard); widen `beam` or add branch-and-bound if results look short.
    states = [(0.0, start + walk, stop, frozenset({stops[stop].name}), (("walk", None, stop, start, start + walk),))
              for stop, walk in access if start + walk <= latest.get(stop, -math.inf)]
    best = None
    for _ in range(max_rides + 1):
        grown = {}
        for state in states:
            distance, time, stop, visited, legs = state
            if stop in egress and time + egress[stop] <= deadline and legs[-1][0] != "walk-change":
                candidate = finished(state)
                if best is None or candidate[0] > best[0]:
                    best = candidate
            ready = time + (TRANSFER_S if len(legs) > 1 else 0)
            items = departures.get(stop, [])
            directions = set()
            for dep, trip, index in items[bisect.bisect_left(items, (ready,)):]:
                if dep > latest.get(stop, -math.inf) + TRANSFER_S:
                    break  # Nothing later from here can still make the deadline.
                hops = by_trip[trip]
                # The earliest train per line and direction rides the same stations with more time left.
                direction = (hops[index].line, hops[index].to)
                if direction in directions:
                    continue
                directions.add(direction)
                ridden, seen = 0.0, set()
                for position, hop in enumerate(hops[index:], index + 1):
                    name = stops[hop.to].name
                    if name in visited or name in seen:
                        break
                    ridden += hop.metres
                    seen.add(name)
                    if hop.arr <= latest.get(hop.to, -math.inf):
                        leg = ("ride", stop, hop.to, dep, hop.arr, trip, index, position, ridden)
                        grown_state = (distance + ridden, hop.arr, hop.to, visited | seen, legs + (leg,))
                        key = (hop.to, hop.arr // 300)
                        if key not in grown or grown[key][0] < grown_state[0]:
                            grown[key] = grown_state
            if legs[-1][0] == "ride":
                for other, walk in footpaths.get(stop, ()):
                    name = stops[other].name
                    arrival = time + walk
                    if (name not in visited or name == stops[stop].name) and arrival <= latest.get(other, -math.inf):
                        grown_state = (distance, arrival, other, visited | {name}, legs + (("walk-change", stop, other, time, arrival),))
                        key = (other, arrival // 300)
                        if key not in grown or grown[key][0] < grown_state[0]:
                            grown[key] = grown_state
        states = sorted(grown.values(), key=lambda s: -s[0])[:beam]
        if not states:
            break
    if best is None:
        return None
    return describe(best, network, by_trip, origin, destination)


def describe(best, network, by_trip, origin, destination):
    distance, legs = best
    stops = network.stops

    def place(stop_id, fallback):
        if stop_id is None:
            return {"name": None, "lat": fallback[0], "lon": fallback[1]}
        stop = stops[stop_id]
        return {"name": stop.name, "lat": stop.lat, "lon": stop.lon}

    out = []
    for leg in legs:
        if leg[0] == "ride":
            _, frm, to, dep, arr, trip, first, last, ridden = leg
            hops = by_trip[trip][first:last]
            path = [place(hops[0].frm, None)] + [place(h.to, None) for h in hops]
            out.append({"mode": hops[0].mode, "line": hops[0].line, "from": path[0], "to": path[-1], "stops": path,
                        "depart": display_time(dep), "arrive": display_time(arr), "distance_m": round(ridden)})
        else:
            kind, frm, to, dep, arr = leg
            a, b = place(frm, origin), place(to, destination)
            if arr - dep < 1:
                continue
            out.append({"mode": "walk", "from": a, "to": b, "depart": display_time(dep), "arrive": display_time(arr),
                        "distance_m": round(metres((a["lat"], a["lon"]), (b["lat"], b["lon"])))})
    return {"ride_distance_m": round(distance), "depart": out[0]["depart"] if out else None,
            "arrive": out[-1]["arrive"] if out else None, "legs": out}


class JourneyPlanner:
    """Builds the network from the server's metro timetable and GTFS services, per request window."""
    def __init__(self, timetables, extras):
        self.timetables, self.extras = timetables, extras
        self.base_key, self.base = None, None

    def plan(self, origin, destination, depart, arrive, now=None):
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
        result = longest_journey(network, origin, destination, start, arrive)
        return {"journey": result, "service_date": day.isoformat(), "calendar": calendar,
                "window": {"depart": display_time(start), "arrive": display_time(arrive)}, "sources": sources,
                "notes": ["乗車距離は駅間の直線距離の合計です。線路・道路に沿った距離ではありません。",
                          "徒歩は直線距離×1.3、分速80mで見積もっています。",
                          "遅延・運休は反映していません。最長経路は近似探索で、真の最長とは限りません。"]}
