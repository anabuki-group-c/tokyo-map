"""Tokyo boundary from Overture Maps division areas, with a fast point-in-polygon test."""
from __future__ import annotations

import json
import math
from pathlib import Path

from nearby_places_api.features import read_features

TOKYO_REGION = "JP-13"
STRIPS = 1024


class Boundary:
    """A Polygon/MultiPolygon tested with the even-odd rule.

    Edges are bucketed into latitude strips so a point is only tested against
    the edges that cross its latitude, not all of Tokyo's ~13,000 vertices.
    """

    def __init__(self, geometry: dict):
        if geometry.get("type") == "Polygon":
            polygons = [geometry["coordinates"]]
        elif geometry.get("type") == "MultiPolygon":
            polygons = geometry["coordinates"]
        else:
            raise ValueError("Boundary must be a Polygon or MultiPolygon")
        self.geometry = geometry
        edges = []
        for polygon in polygons:
            for ring in polygon:
                for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
                    if y1 != y2:
                        edges.append((float(x1), float(y1), float(x2), float(y2)))
        if not edges:
            raise ValueError("Boundary has no area")
        xs = [x for edge in edges for x in (edge[0], edge[2])]
        ys = [y for edge in edges for y in (edge[1], edge[3])]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.strip_height = (self.bbox[3] - self.bbox[1]) / STRIPS or 1.0
        self.strips = [[] for _ in range(STRIPS)]
        for edge in edges:
            low, high = sorted((edge[1], edge[3]))
            for strip in range(self._strip(low), self._strip(high) + 1):
                self.strips[strip].append(edge)

    def _strip(self, lat: float) -> int:
        return min(STRIPS - 1, max(0, int((lat - self.bbox[1]) / self.strip_height)))

    def contains(self, lat: float, lon: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bbox
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            return False
        inside = False
        for x1, y1, x2, y2 in self.strips[self._strip(lat)]:
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside

    def covers_circle(self, lat: float, lon: float, radius_m: float, earth_radius_m: float) -> bool:
        """Approximate: True when 32 points on the circle are all inside the boundary."""
        angular = radius_m / earth_radius_m
        p1, l1 = math.radians(lat), math.radians(lon)
        for step in range(32):
            bearing = 2 * math.pi * step / 32
            p2 = math.asin(math.sin(p1) * math.cos(angular) + math.cos(p1) * math.sin(angular) * math.cos(bearing))
            l2 = l1 + math.atan2(math.sin(bearing) * math.sin(angular) * math.cos(p1),
                                 math.cos(angular) - math.sin(p1) * math.sin(p2))
            if not self.contains(math.degrees(p2), math.degrees(l2)):
                return False
        return True


def load_tokyo_boundary(path: Path) -> Boundary:
    """Read Tokyo's land area from an Overture division_area file.

    A file holding a single Polygon/MultiPolygon feature is also accepted.
    """
    features = [feature for feature in read_features(path) if isinstance(feature, dict)]
    for feature in features:
        properties = feature.get("properties") or {}
        if (properties.get("region") == TOKYO_REGION and properties.get("subtype") == "region"
                and properties.get("class") == "land"):
            return Boundary(feature["geometry"])
    if len(features) == 1:
        return Boundary(features[0]["geometry"])
    raise ValueError(f"Tokyo land area (region {TOKYO_REGION}, subtype region, class land) was not found in {path.name}")


def boundary_from_json(text: str) -> Boundary:
    return Boundary(json.loads(text))

