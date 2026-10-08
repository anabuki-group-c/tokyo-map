import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from nearby_places_api.store import PlaceStore, import_places
from nearby_places_api.web import make_handler
from test_store import TEST_BOUNDARY, TOKYO_STATION, museum, north_of, restaurant, write_seq


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "places.sqlite"
        lat, lon = TOKYO_STATION
        source = write_seq(self.directory.name, [
            restaurant("near", *north_of(lat, lon, 100.04)),
            museum("mid", *north_of(lat, lon, 300)),
            restaurant("far", *north_of(lat, lon, 900)),
        ])
        import_places([source], self.database, TEST_BOUNDARY)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(PlaceStore(self.database)))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path="/api/v1/places/nearby", **params):
        url = f"http://127.0.0.1:{self.server.server_port}{path}?{urlencode(params)}"
        try:
            with urlopen(url) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def nearby(self, **params):
        lat, lon = TOKYO_STATION
        return self.get(lat=lat, lon=lon, **params)

    def test_nearby_response(self):
        status, body = self.nearby(radius_m=500)
        self.assertEqual(status, 200)
        self.assertEqual(body["center"], {"lat": TOKYO_STATION[0], "lon": TOKYO_STATION[1]})
        self.assertEqual(body["radius_m"], 500)
        self.assertEqual(body["category"], "all")
        self.assertEqual(body["count"], 2)
        self.assertFalse(body["partial_coverage"])
        self.assertEqual([place["id"] for place in body["places"]], ["near", "mid"])
        self.assertEqual(body["places"][0]["distance_m"], 100.0)
        self.assertEqual(set(body["places"][0]), {"id", "name", "category", "source_category", "lat", "lon", "distance_m", "address", "website"})

    def test_category_and_limit(self):
        self.assertEqual(self.nearby(radius_m=1000, category="tourism")[1]["places"][0]["id"], "mid")
        self.assertEqual(self.nearby(radius_m=1000, limit=2)[1]["count"], 2)

    def test_no_results_is_200(self):
        status, body = self.nearby(radius_m=10)
        self.assertEqual((status, body["count"], body["places"]), (200, 0, []))

    def test_invalid_parameters(self):
        lat, lon = TOKYO_STATION
        cases = [
            {"lat": lat, "lon": lon},
            {"lat": "abc", "lon": lon, "radius_m": 100},
            {"lat": 91, "lon": lon, "radius_m": 100},
            {"lat": lat, "lon": 181, "radius_m": 100},
            {"lat": lat, "lon": "nan", "radius_m": 100},
            {"lat": lat, "lon": lon, "radius_m": 0},
            {"lat": lat, "lon": lon, "radius_m": -5},
            {"lat": lat, "lon": lon, "radius_m": 5001},
            {"lat": lat, "lon": lon, "radius_m": 100, "category": "hotel"},
            {"lat": lat, "lon": lon, "radius_m": 100, "limit": 0},
            {"lat": lat, "lon": lon, "radius_m": 100, "limit": 101},
            {"lat": lat, "lon": lon, "radius_m": 100, "limit": "1.5"},
        ]
        for params in cases:
            with self.subTest(params=params):
                status, body = self.get(**params)
                self.assertEqual(status, 400)
                self.assertEqual(body["error"]["code"], "INVALID_PARAMETER")
        self.assertEqual(self.nearby(radius_m=0)[1]["error"]["message"], "radius_mには0より大きい数値を指定してください。")

    def test_out_of_coverage_and_partial_coverage(self):
        status, body = self.get(lat=34.70, lon=135.50, radius_m=100)
        self.assertEqual((status, body["error"]["code"]), (400, "OUT_OF_COVERAGE"))
        status, body = self.get(lat=35.665, lon=139.715, radius_m=100)
        self.assertEqual((status, body["error"]["code"]), (400, "OUT_OF_COVERAGE"))
        status, body = self.get(lat=35.699, lon=139.75, radius_m=500)
        self.assertEqual(status, 200)
        self.assertTrue(body["partial_coverage"])

    def test_coverage_and_ui_files(self):
        status, body = self.get("/api/v1/places/coverage")
        self.assertEqual((status, body["bbox"]), (200, [139.35, 34.70, 139.80, 35.70]))
        self.assertEqual(body["boundary"]["type"], "MultiPolygon")
        base = f"http://127.0.0.1:{self.server.server_port}"
        for path, content_type in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            with self.subTest(path=path), urlopen(base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers["Content-Type"].startswith(content_type))

    def test_cors_only_for_local_origins(self):
        base = f"http://127.0.0.1:{self.server.server_port}/api/v1/places/coverage"
        for origin, allowed in (("http://127.0.0.1:5500", True), ("http://localhost:5500", True), ("https://example.com", False)):
            with self.subTest(origin=origin), urlopen(Request(base, headers={"Origin": origin})) as response:
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin if allowed else None)

    def test_unknown_path_and_missing_data(self):
        self.assertEqual(self.get("/api/v1/places/unknown")[0], 404)
        self.database.unlink()
        status, body = self.nearby(radius_m=100)
        self.assertEqual((status, body["error"]["code"]), (503, "DATA_NOT_READY"))
        self.assertEqual(self.get("/api/v1/places/coverage")[0], 503)


if __name__ == "__main__":
    unittest.main()
