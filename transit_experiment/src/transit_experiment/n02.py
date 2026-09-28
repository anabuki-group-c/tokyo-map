"""Import Tokyo Metro and JR East line geometries from MLIT N02 data."""

from __future__ import annotations

import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

N02_URL = "https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-22/N02-22_GML.zip"
RAILROAD_MEMBER = "UTF-8/N02-22_RailroadSection.geojson"
STATION_MEMBER = "UTF-8/N02-22_Station.geojson"

# Mainland Tokyo. The experiment intentionally excludes the remote Tokyo islands.
TOKYO_BOUNDS = (138.9, 35.5, 140.0, 35.95)  # min_lon, min_lat, max_lon, max_lat
OPERATORS = {
    "jr-east": "東日本旅客鉄道",
    "tokyo-metro": "東京地下鉄",
}


def download_n02(raw_dir: Path) -> Path:
    """Download the published N02 archive once, unless it already exists."""
    destination = raw_dir / "N02-22_GML.zip"
    if destination.exists():
        return destination

    raw_dir.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.part")
    try:
        with urllib.request.urlopen(N02_URL, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _coordinate_pairs(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1])
        else:
            for item in value:
                yield from _coordinate_pairs(item)


def _intersects_tokyo(geometry: dict[str, Any] | None) -> bool:
    if not geometry:
        return False
    min_lon, min_lat, max_lon, max_lat = TOKYO_BOUNDS
    coordinates = list(_coordinate_pairs(geometry.get("coordinates")))
    if not coordinates:
        return False
    geometry_min_lon = min(point[0] for point in coordinates)
    geometry_max_lon = max(point[0] for point in coordinates)
    geometry_min_lat = min(point[1] for point in coordinates)
    geometry_max_lat = max(point[1] for point in coordinates)
    return not (
        geometry_max_lon < min_lon
        or geometry_min_lon > max_lon
        or geometry_max_lat < min_lat
        or geometry_min_lat > max_lat
    )


def _load_member(archive: Path, member: str) -> dict[str, Any]:
    with zipfile.ZipFile(archive) as zip_file:
        with zip_file.open(member) as source:
            return json.load(source)


def _select_features(collection: dict[str, Any], operator_name: str) -> dict[str, Any]:
    features = [
        feature
        for feature in collection.get("features", [])
        if feature.get("properties", {}).get("N02_004") == operator_name
        and _intersects_tokyo(feature.get("geometry"))
    ]
    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "source": N02_URL,
            "operator": operator_name,
            "filter": "mainland Tokyo bounding box",
        },
    }


def import_tokyo_railways(raw_dir: Path, processed_dir: Path) -> dict[str, int]:
    """Write per-operator line and station GeoJSON files and return feature counts."""
    archive = download_n02(raw_dir)
    railroads = _load_member(archive, RAILROAD_MEMBER)
    stations = _load_member(archive, STATION_MEMBER)
    processed_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for slug, operator_name in OPERATORS.items():
        railroad_features = _select_features(railroads, operator_name)
        station_features = _select_features(stations, operator_name)
        (processed_dir / f"{slug}-railroads.geojson").write_text(
            json.dumps(railroad_features, ensure_ascii=False), encoding="utf-8"
        )
        (processed_dir / f"{slug}-stations.geojson").write_text(
            json.dumps(station_features, ensure_ascii=False), encoding="utf-8"
        )
        counts[f"{slug}-railroads"] = len(railroad_features["features"])
        counts[f"{slug}-stations"] = len(station_features["features"])
    return counts
