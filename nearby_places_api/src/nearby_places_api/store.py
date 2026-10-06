"""Import Overture Places into SQLite and search places within a radius."""
from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from nearby_places_api.boundary import Boundary, boundary_from_json
from nearby_places_api.categories import API_CATEGORIES, classify, source_category
from nearby_places_api.features import read_features

EARTH_RADIUS_M = 6_371_008.8
CLOSED_STATUSES = {"closed", "permanently_closed", "temporarily_closed"}

SCHEMA = """
CREATE TABLE places (
    rowid INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT,
    category TEXT NOT NULL,
    source_category TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    address TEXT
);
CREATE VIRTUAL TABLE places_index USING rtree(rowid, min_lon, max_lon, min_lat, max_lat);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class DataNotReady(Exception):
    pass


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle (haversine) distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def search_bounds(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """A lon/lat rectangle that contains the whole search circle."""
    dlat = math.degrees(radius_m / EARTH_RADIUS_M)
    dlon = dlat / max(math.cos(math.radians(lat)), 1e-6)
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def to_row(feature: dict) -> tuple | None:
    """Convert one Overture feature to a row, or None when it is not searchable."""
    if not isinstance(feature, dict):
        return None
    properties = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        return None
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2 or not all(isinstance(value, (int, float)) for value in coordinates[:2]):
        return None
    lon, lat = float(coordinates[0]), float(coordinates[1])  # GeoJSON order is lon, lat.
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if properties.get("operating_status") in CLOSED_STATUSES:
        return None
    category = classify(properties)
    place_id = properties.get("id", feature.get("id"))
    if category is None or not isinstance(place_id, str) or not place_id:
        return None
    names = properties.get("names")
    name = names.get("primary") if isinstance(names, dict) else None
    addresses = properties.get("addresses")
    address = addresses[0].get("freeform") if isinstance(addresses, list) and addresses and isinstance(addresses[0], dict) else None
    return (place_id, name if isinstance(name, str) else None, category, source_category(properties),
            lat, lon, address if isinstance(address, str) else None)


def import_places(sources: list[Path], database: Path, boundary: Boundary) -> dict:
    """Build the search database from one or more files, keeping only places inside the boundary."""
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + ".tmp")
    temporary.unlink(missing_ok=True)
    counts = {"read": 0, "restaurant": 0, "tourism": 0, "skipped": 0, "outside": 0, "duplicates": 0}
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SCHEMA)
        for source in sources:
            for feature in read_features(source):
                counts["read"] += 1
                row = to_row(feature)
                if row is None:
                    counts["skipped"] += 1
                    continue
                lat, lon = row[4], row[5]
                if not boundary.contains(lat, lon):
                    counts["outside"] += 1
                    continue
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO places (id, name, category, source_category, lat, lon, address) VALUES (?, ?, ?, ?, ?, ?, ?)", row)
                if cursor.rowcount == 0:
                    counts["duplicates"] += 1
                    continue
                connection.execute("INSERT INTO places_index VALUES (?, ?, ?, ?, ?)", (cursor.lastrowid, lon, lon, lat, lat))
                counts[row[2]] += 1
        connection.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("boundary", json.dumps(boundary.geometry)),
            ("sources", json.dumps([source.name for source in sources], ensure_ascii=False)),
        ])
        connection.commit()
    except BaseException:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    connection.close()
    os.replace(temporary, database)
    return counts


class PlaceStore:
    def __init__(self, database: Path):
        self.database = database
        self._boundary = None
        self._boundary_key = None

    def connect(self):
        if not self.database.exists():
            raise DataNotReady()
        # One read-only connection per request keeps the threaded server simple.
        return closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True))

    def boundary(self) -> Boundary:
        """The boundary saved at import time, cached until the database file changes."""
        try:
            stat = self.database.stat()
        except FileNotFoundError:
            raise DataNotReady() from None
        key = (stat.st_mtime_ns, stat.st_size)
        if key != self._boundary_key:
            with self.connect() as connection:
                row = connection.execute("SELECT value FROM meta WHERE key = 'boundary'").fetchone()
            if row is None:
                raise DataNotReady()
            self._boundary, self._boundary_key = boundary_from_json(row[0]), key
        return self._boundary

    def nearby(self, lat: float, lon: float, radius_m: float, category: str = "all", limit: int = 20) -> list[dict]:
        """Places within radius_m, nearest first. Distances are not rounded here."""
        categories = API_CATEGORIES if category == "all" else (category,)
        min_lon, min_lat, max_lon, max_lat = search_bounds(lat, lon, radius_m)
        placeholders = ", ".join("?" for _ in categories)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.id, p.name, p.category, p.source_category, p.lat, p.lon, p.address
                    FROM places_index AS i JOIN places AS p ON p.rowid = i.rowid
                    WHERE i.max_lon >= ? AND i.min_lon <= ? AND i.max_lat >= ? AND i.min_lat <= ?
                      AND p.category IN ({placeholders})""",
                (min_lon, max_lon, min_lat, max_lat, *categories)).fetchall()
        places = []
        for place_id, name, place_category, original, place_lat, place_lon, address in rows:
            distance = distance_m(lat, lon, place_lat, place_lon)
            if distance <= radius_m:
                places.append({
                    "id": place_id, "name": name, "category": place_category, "source_category": original,
                    "lat": place_lat, "lon": place_lon, "distance_m": distance, "address": address,
                })
        places.sort(key=lambda place: (place["distance_m"], place["id"]))
        return places[:limit]
