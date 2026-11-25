"""Find a starting point by name: railway stations and Toei bus stops (places come from PlaceStore.named)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from transit_experiment.journey import metres
from transit_experiment.n02 import STATION_MEMBER, _intersects_tokyo, _load_member


MERGE_M = 1000  # Same-named stations closer than this are one (京葉線 東京 is its own N02 group).


def stations(archive: Path) -> list[dict]:
    """Stations of every railway in mainland Tokyo, one per N02 station group (JR and metro 東京 are one)."""
    codes, groups = {}, []
    for feature in _load_member(archive, STATION_MEMBER)["features"]:
        properties = feature["properties"]
        if not _intersects_tokyo(feature.get("geometry")):
            continue
        name, point = properties["N02_005"], feature["geometry"]["coordinates"][0]
        group = codes.get(properties["N02_005g"]) or next(
            (g for g in groups if g["name"] == name and metres((g["points"][0][1], g["points"][0][0]), (point[1], point[0])) <= MERGE_M), None)
        if group is None:
            group = {"name": name, "lines": [], "points": []}
            groups.append(group)
        codes[properties["N02_005g"]] = group
        if properties["N02_003"] not in group["lines"]:
            group["lines"].append(properties["N02_003"])
        group["points"].extend(feature["geometry"]["coordinates"])
    return [{"name": group["name"] + "駅", "detail": "・".join(group["lines"][:3]) + ("ほか" if len(group["lines"]) > 3 else ""),
             "lat": sum(p[1] for p in group["points"]) / len(group["points"]),
             "lon": sum(p[0] for p in group["points"]) / len(group["points"])} for group in groups]


def bus_stops(database: Path) -> list[dict]:
    """Toei bus stops; the poles of one stop share its name and are averaged into one point."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT name, AVG(lat), AVG(lon) FROM stops GROUP BY name").fetchall()
    return [{"name": name, "detail": "都営バス停留所", "lat": lat, "lon": lon} for name, lat, lon in rows]


def rank(name: str, query: str) -> int | None:
    """0 exact (東京 matches 東京駅 too), 1 starts with the query, 2 contains it, None no match."""
    if name == query or name.removesuffix("駅") == query:
        return 0
    if name.startswith(query):
        return 1
    return 2 if query in name else None


def search(entries: list[dict], query: str, limit: int = 6) -> list[dict]:
    # ponytail: linear scan over ~5,000 names per search; add an index if the list grows to places scale.
    scored = [(score, len(entry["name"]), entry["name"], entry) for entry in entries
              if (score := rank(entry["name"], query)) is not None]
    return [entry for *_, entry in sorted(scored, key=lambda item: item[:3])[:limit]]


def load(transit_root: Path) -> list[dict]:
    """Every name that can be searched; a missing data file just leaves its names out."""
    entries = []
    archive, buses = transit_root / "data" / "raw" / "N02-22_GML.zip", transit_root / "data" / "processed" / "bus-gtfs.sqlite"
    if archive.exists():
        entries += stations(archive)
    if buses.exists():
        entries += bus_stops(buses)
    return entries
