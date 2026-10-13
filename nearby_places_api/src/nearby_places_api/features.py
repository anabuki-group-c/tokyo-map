"""Read GeoJSON and newline-delimited GeoJSON (geojsonseq) files."""
from __future__ import annotations

import json
from pathlib import Path

SEQ_SUFFIXES = (".geojsonseq", ".geojsonl", ".ndjson", ".jsonl")


def read_features(path: Path):
    """Yield features from GeoJSON or newline-delimited GeoJSON (geojsonseq)."""
    if path.suffix.lower() in SEQ_SUFFIXES:
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                line = line.strip().lstrip("\x1e")
                if line:
                    yield json.loads(line)
        return
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if data.get("type") == "FeatureCollection":
        yield from data.get("features", [])
    elif data.get("type") == "Feature":
        yield data
