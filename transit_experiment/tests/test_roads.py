import json
import tempfile
import unittest
from pathlib import Path

from transit_experiment.roads import Roads, import_roads


def road(coords, **properties):
    return json.dumps({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
                       "properties": {"subtype": "road", "class": "residential", **properties}})


class RoadsTests(unittest.TestCase):
    def test_walk_follows_the_streets_and_avoids_motorways(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "segments.geojsonseq", Path(folder) / "roads.sqlite"
            source.write_text("\n".join([
                # An L-shaped street from A (139.0, 35.0) to B (139.01, 35.01) via the corner (139.01, 35.0).
                road([[139.0, 35.0], [139.01, 35.0]]),
                road([[139.01, 35.0], [139.01, 35.01]], access_restrictions=[{"access_type": "denied", "when": {"heading": "backward"}}]),
                # Straight shortcuts a walker may not take.
                road([[139.0, 35.0], [139.01, 35.01]], **{"class": "motorway"}),
                road([[139.0, 35.0], [139.005, 35.005], [139.01, 35.01]],
                     access_restrictions=[{"access_type": "denied", "when": {"mode": ["foot"]}}]),
            ]), encoding="utf-8")
            self.assertEqual(import_roads(source, target), 2)
            roads = Roads(target)
            a, b = (35.0001, 139.0), (35.01, 139.0101)
            self.assertEqual(roads.route(a, b), [a, (35.0, 139.0), (35.0, 139.01), (35.01, 139.01), b])
            self.assertIsNone(roads.route(a, (35.05, 139.05)))  # Far from every road.


if __name__ == "__main__":
    unittest.main()
