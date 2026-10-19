import unittest
from pathlib import Path

from nearby_places_api.store import PlaceStore
from integrated_api import search
from integrated_api.web import named_places

ENTRIES = [
    {"name": "東京駅八重洲口", "detail": "都営バス停留所", "lat": 35.68, "lon": 139.77},
    {"name": "新東京", "detail": "都営バス停留所", "lat": 35.6, "lon": 139.7},
    {"name": "東京駅", "detail": "山手線", "lat": 35.68, "lon": 139.77},
    {"name": "東京タワー", "detail": "都営バス停留所", "lat": 35.66, "lon": 139.75},
]


class SearchTests(unittest.TestCase):
    def test_exact_then_prefix_then_contains(self):
        names = lambda query: [entry["name"] for entry in search.search(ENTRIES, query)]
        self.assertEqual(names("東京駅"), ["東京駅", "東京駅八重洲口"])
        self.assertEqual(names("東京"), ["東京駅", "東京タワー", "東京駅八重洲口", "新東京"])  # 東京 is 東京駅 exactly.
        self.assertEqual(names("大阪"), [])

    def test_stations_are_found_without_the_places_database(self):
        missing = PlaceStore(Path("/nonexistent/places.sqlite"))
        self.assertEqual([r["name"] for r in named_places(missing, ENTRIES, "タワー")], ["東京タワー"])


if __name__ == "__main__":
    unittest.main()
