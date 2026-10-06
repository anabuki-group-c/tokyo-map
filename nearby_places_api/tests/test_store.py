import json
import math
import tempfile
import unittest
from pathlib import Path

from nearby_places_api.boundary import Boundary, load_tokyo_boundary
from nearby_places_api.categories import classify
from nearby_places_api.store import EARTH_RADIUS_M, PlaceStore, distance_m, import_places

TOKYO_STATION = (35.681236, 139.767125)


def square(west, south, east, north):
    return [[[west, south], [east, south], [east, north], [west, north], [west, south]]]


# A small "Tokyo" around Tokyo Station, with a hole and a separate island.
TEST_BOUNDARY = Boundary({"type": "MultiPolygon", "coordinates": [
    square(139.70, 35.65, 139.80, 35.70) + square(139.71, 35.66, 139.72, 35.67)[:1],
    square(139.35, 34.70, 139.45, 34.80),
]})


def north_of(lat, lon, metres):
    return lat + math.degrees(metres / EARTH_RADIUS_M), lon


def feature(place_id, lat, lon, **properties):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": place_id, **properties}}


def restaurant(place_id, lat, lon, **extra):
    return feature(place_id, lat, lon, names={"primary": f"店{place_id}"},
                   taxonomy={"hierarchy": ["food_and_drink", "restaurant"], "primary": "restaurant"}, **extra)


def museum(place_id, lat, lon):
    return feature(place_id, lat, lon, names={"primary": f"館{place_id}"}, basic_category="art_museum")


def write_seq(directory, features):
    path = Path(directory) / "places.geojsonseq"
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in features), encoding="utf-8")
    return path


def overture(hierarchy=(), name="", basic=None):
    """Properties shaped like the current Overture Places schema."""
    taxonomy = {"hierarchy": list(hierarchy), "primary": hierarchy[-1]} if hierarchy else None
    return {"names": {"primary": name}, "taxonomy": taxonomy, "basic_category": basic}


class ClassifyTests(unittest.TestCase):
    def test_restaurants_current_and_legacy_schemas(self):
        self.assertEqual(classify({"taxonomy": {"hierarchy": ["food_and_drink", "cafe"]}}), "restaurant")
        self.assertEqual(classify({"basic_category": "casual_eatery"}), "restaurant")
        self.assertEqual(classify({"categories": {"primary": "ramen_restaurant"}}), "restaurant")
        self.assertEqual(classify({"categories": {"primary": "shinto_shrine"}}), "tourism")
        self.assertIsNone(classify({"basic_category": "dentist"}))
        self.assertIsNone(classify({}))

    def test_tourism_labels_seen_in_tokyo_data(self):
        # Hierarchies as they appear in Overture Places for central Tokyo.
        tourism = [
            overture(["arts_and_entertainment", "museum", "art_museum"], "国立西洋美術館"),
            overture(["arts_and_entertainment", "science_attraction", "observatory"], "東京タワー"),
            overture(["arts_and_entertainment", "animal_attraction", "zoo"], "恩賜上野動物園"),
            overture(["cultural_and_historic", "place_of_worship", "buddhist_place_of_worship"], "浅草寺"),
            overture(["cultural_and_historic", "historic_site", "palace"], "皇居"),
            overture(["cultural_and_historic", "memorial_site", "monument"], "明治神宮参拝道開通記念碑"),
            overture(["sports_and_recreation", "park"], "上野恩賜公園"),
            overture(["geographic_entities", "land_feature", "mountain"], "高尾山山頂"),
            overture(["travel_and_transportation", "travel_service", "visitor_center"], "浅草文化観光センター"),
        ]
        for properties in tourism:
            with self.subTest(name=properties["names"]["primary"]):
                self.assertEqual(classify(properties), "tourism")

    def test_shrines_are_found_by_name(self):
        # Shinto shrines are labelled as churches, religious organizations or nothing at all.
        self.assertEqual(classify(overture(["cultural_and_historic", "place_of_worship", "christian_place_of_worship"], "明治神宮")), "tourism")
        self.assertEqual(classify(overture(["cultural_and_historic", "religious_organization"], "湯島天神")), "tourism")
        self.assertEqual(classify(overture([], "日枝神社")), "tourism")
        self.assertEqual(classify(overture([], "東京都庁舎展望室")), "tourism")
        self.assertIsNone(classify(overture(["cultural_and_historic", "place_of_worship", "christian_place_of_worship"], "渋谷教会")))
        self.assertIsNone(classify(overture(["cultural_and_historic", "religious_organization"], "幸福の科学渋谷精舎")))
        self.assertIsNone(classify(overture([], "神社前クリニック")))

    def test_historic_sites_exclude_buildings(self):
        historic = ["cultural_and_historic", "historic_site"]
        for name in ("金座跡", "服部半蔵の墓", "猫又坂", "旧因州池田屋敷表門", "石川島資料館"):
            with self.subTest(name=name):
                self.assertEqual(classify(overture(historic, name)), "tourism")
        for name in ("新宿アイランドレジデンス", "Roppongi Duplex Tower", "養命酒ビル", "豊洲三丁目 歩道橋", "Sopra Tower"):
            with self.subTest(name=name):
                self.assertIsNone(classify(overture(historic, name)))

    def test_excluded_parks_and_places(self):
        self.assertIsNone(classify(overture(["sports_and_recreation", "park", "playground"], "鶯谷児童遊園地")))
        self.assertIsNone(classify(overture(["sports_and_recreation", "park"], "中央区立月島第二児童公園")))
        self.assertIsNone(classify(overture(["arts_and_entertainment", "arts_and_crafts_space", "art_gallery"], "July Tree")))
        self.assertIsNone(classify(overture(["geographic_entities", "built_feature", "bridge"], "西参道歩道橋")))
        self.assertIsNone(classify(overture(["arts_and_entertainment", "stadium_arena"], "東京ドーム")))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "places.sqlite"
        lat, lon = TOKYO_STATION
        self.features = [
            restaurant("near", *north_of(lat, lon, 100)),
            museum("mid", *north_of(lat, lon, 300)),
            restaurant("inside", *north_of(lat, lon, 499)),
            restaurant("outside", *north_of(lat, lon, 501)),
            restaurant("closed", *north_of(lat, lon, 50), operating_status="permanently_closed"),
            feature("dentist", *north_of(lat, lon, 10), basic_category="dentist"),
            restaurant("near", *north_of(lat, lon, 20)),
            {"type": "Feature", "geometry": None, "properties": {"id": "broken"}},
        ]
        self.counts = import_places([write_seq(self.directory.name, self.features)], self.database, TEST_BOUNDARY)
        self.store = PlaceStore(self.database)

    def test_import_counts(self):
        self.assertEqual(self.counts, {"read": 8, "restaurant": 3, "tourism": 1, "skipped": 3, "outside": 0, "duplicates": 1})

    def test_radius_order_category_and_limit(self):
        places = self.store.nearby(*TOKYO_STATION, 500)
        self.assertEqual([place["id"] for place in places], ["near", "mid", "inside"])
        self.assertEqual(places[1]["category"], "tourism")
        self.assertEqual(places[1]["source_category"], "art_museum")
        self.assertEqual(places[0]["name"], "店near")
        self.assertIsNone(places[0]["address"])
        self.assertEqual([p["id"] for p in self.store.nearby(*TOKYO_STATION, 500, "restaurant")], ["near", "inside"])
        self.assertEqual([p["id"] for p in self.store.nearby(*TOKYO_STATION, 500, "all", 1)], ["near"])
        self.assertEqual(self.store.nearby(*TOKYO_STATION, 50), [])

    def test_boundary_is_inclusive(self):
        lat, lon = TOKYO_STATION
        target = north_of(lat, lon, 499)
        exact = distance_m(lat, lon, *target)
        self.assertIn("inside", [p["id"] for p in self.store.nearby(lat, lon, exact)])
        self.assertNotIn("inside", [p["id"] for p in self.store.nearby(lat, lon, exact - 0.01)])

    def test_search_uses_metres_not_degrees_east_west(self):
        lat, lon = TOKYO_STATION
        # 0.004 degrees of longitude is about 362 m at Tokyo's latitude.
        import_places([write_seq(self.directory.name, [restaurant("east", lat, lon + 0.004)])], self.database, TEST_BOUNDARY)
        self.assertEqual(len(self.store.nearby(lat, lon, 370)), 1)
        self.assertEqual(self.store.nearby(lat, lon, 350), [])

    def test_places_outside_boundary_are_dropped(self):
        outside = [restaurant("kawasaki", 35.60, 139.62), restaurant("hole", 35.665, 139.715), restaurant("island", 34.75, 139.40)]
        counts = import_places([write_seq(self.directory.name, outside)], self.database, TEST_BOUNDARY)
        self.assertEqual((counts["outside"], counts["restaurant"]), (2, 1))
        self.assertEqual(self.store.boundary().bbox, (139.35, 34.70, 139.80, 35.70))

    def test_multiple_files_and_feature_collection(self):
        path = Path(self.directory.name) / "places.geojson"
        path.write_text(json.dumps({"type": "FeatureCollection", "features": self.features[:2]}), encoding="utf-8")
        island = Path(self.directory.name) / "island.geojsonseq"
        island.write_text(json.dumps(restaurant("island", 34.75, 139.40)), encoding="utf-8")
        self.assertEqual(import_places([path, island], self.database, TEST_BOUNDARY)["restaurant"], 2)


class BoundaryTests(unittest.TestCase):
    def test_contains_with_hole_and_island(self):
        self.assertTrue(TEST_BOUNDARY.contains(*TOKYO_STATION))
        self.assertFalse(TEST_BOUNDARY.contains(35.665, 139.715))
        self.assertTrue(TEST_BOUNDARY.contains(34.75, 139.40))
        self.assertFalse(TEST_BOUNDARY.contains(35.60, 139.62))
        self.assertFalse(TEST_BOUNDARY.contains(35.75, 139.75))

    def test_covers_circle(self):
        self.assertTrue(TEST_BOUNDARY.covers_circle(*TOKYO_STATION, 500, EARTH_RADIUS_M))
        self.assertFalse(TEST_BOUNDARY.covers_circle(35.699, 139.75, 500, EARTH_RADIUS_M))
        self.assertFalse(TEST_BOUNDARY.covers_circle(35.668, 139.715, 500, EARTH_RADIUS_M))

    def test_load_tokyo_from_division_areas(self):
        with tempfile.TemporaryDirectory() as directory:
            def area(region, cls, polygon):
                return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": polygon},
                        "properties": {"subtype": "region", "region": region, "class": cls}}
            path = write_seq(directory, [
                area("JP-14", "land", square(139.0, 35.0, 139.5, 35.5)),
                area("JP-13", "maritime", square(139.0, 35.0, 140.5, 36.0)),
                area("JP-13", "land", square(139.5, 35.5, 139.9, 35.9)),
            ])
            self.assertEqual(load_tokyo_boundary(path).bbox, (139.5, 35.5, 139.9, 35.9))
            path = write_seq(directory, [area("JP-14", "land", square(139.0, 35.0, 139.5, 35.5))] * 2)
            with self.assertRaises(ValueError):
                load_tokyo_boundary(path)


if __name__ == "__main__":
    unittest.main()
