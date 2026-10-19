"""Walking routes on Overture Maps roads, for drawing the walks of a journey along the streets.

Data: uvx overturemaps download --bbox=138.94,35.49,139.93,35.90 -f geojsonseq --type=segment
      -r 2026-09-23.1 -o data/raw/tokyo-segments.geojsonseq
then: uv run transit import-roads data/raw/tokyo-segments.geojsonseq
"""
from __future__ import annotations

import heapq
import json
import math
import sqlite3

from transit_experiment.journey import metres

PAD_M = 300   # Roads this far outside the box spanned by the two ends may still be on the way.
SNAP_M = 200  # Furthest an end may be from the road network; beyond it the walk stays a straight line.


def walkable(properties):
    """Roads one may walk on: no motorways, nothing closed to pedestrians at all times."""
    if properties.get("subtype") != "road" or properties.get("class") == "motorway":
        return False
    for rule in properties.get("access_restrictions") or ():
        when = rule.get("when") or {}
        # One-way rules (heading) and vehicle, time or permit conditions do not stop a walker.
        if (rule.get("access_type") == "denied" and "foot" in (when.get("mode") or ["foot"])
                and not any(when.get(key) for key in ("during", "heading", "recognized", "using", "vehicle"))):
            return False
    return True


def import_roads(source, target):
    """Write the walkable roads of an Overture segment GeoJSONSeq file into SQLite with a box index."""
    temporary = target.with_suffix(".part")
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    db.executescript("CREATE TABLE roads(id INTEGER PRIMARY KEY, coords TEXT);"
                     "CREATE VIRTUAL TABLE road_boxes USING rtree(id, min_lon, max_lon, min_lat, max_lat);")
    count = 0
    with open(source, encoding="utf-8") as lines:
        for line in lines:
            feature = json.loads(line)
            if feature["geometry"]["type"] != "LineString" or not walkable(feature["properties"]):
                continue
            # 7 decimals (~1 cm) as in the source, so roads meeting at a point share the exact same vertex.
            coords = [(round(lon, 7), round(lat, 7)) for lon, lat, *_ in feature["geometry"]["coordinates"]]
            count += 1
            db.execute("INSERT INTO roads VALUES(?,?)", (count, json.dumps(coords, separators=(",", ":"))))
            db.execute("INSERT INTO road_boxes VALUES(?,?,?,?,?)", (count, min(p[0] for p in coords), max(p[0] for p in coords),
                                                                     min(p[1] for p in coords), max(p[1] for p in coords)))
    db.commit()
    db.close()
    temporary.replace(target)
    return count


class Roads:
    def __init__(self, path):
        self.path = path

    def route(self, a, b):
        """[(lat, lon)] from a to b along the roads, or None when either end is off the network or they are not linked."""
        pad_lat = PAD_M / 111_195
        pad_lon = pad_lat / math.cos(math.radians(max(abs(a[0]), abs(b[0]))))
        db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT r.coords FROM road_boxes b JOIN roads r ON r.id=b.id "
                              "WHERE b.max_lon>=? AND b.min_lon<=? AND b.max_lat>=? AND b.min_lat<=?",
                              (min(a[1], b[1]) - pad_lon, max(a[1], b[1]) + pad_lon,
                               min(a[0], b[0]) - pad_lat, max(a[0], b[0]) + pad_lat)).fetchall()
        finally:
            db.close()
        graph = {}  # (lat, lon) -> {(lat, lon): metres}
        for (coords,) in rows:
            line = [(lat, lon) for lon, lat in json.loads(coords)]
            for p, q in zip(line, line[1:]):
                d = metres(p, q)
                graph.setdefault(p, {})[q] = d
                graph.setdefault(q, {})[p] = d
        if not graph:
            return None
        # ponytail: ends snap to the nearest road vertex, not the nearest point on a road; on a long straight
        # road the walk starts at its next bend. Project onto the edges if that shows.
        start = min(graph, key=lambda node: metres(node, a))
        end = min(graph, key=lambda node: metres(node, b))
        if metres(start, a) > SNAP_M or metres(end, b) > SNAP_M:
            return None
        queue, lengths, previous = [(0.0, start)], {start: 0.0}, {}
        while queue:
            cost, node = heapq.heappop(queue)
            if node == end:
                points = [node]
                while node != start:
                    node = previous[node]
                    points.append(node)
                return [a, *reversed(points), b]
            if cost > lengths[node]:
                continue
            for target, length in graph[node].items():
                if cost + length < lengths.get(target, math.inf):
                    lengths[target], previous[target] = cost + length, node
                    heapq.heappush(queue, (cost + length, target))
        return None
