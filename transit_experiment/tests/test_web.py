import json
import gzip
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen, Request
from http.server import ThreadingHTTPServer

from transit_experiment.web import StatusCache, make_handler, train_positions


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'cache.json'
        self.now = 1000
        self.calls = 0

    def fetch(self, key):
        self.calls += 1
        return [{'odpt:railway': 'odpt.Railway:TokyoMetro.Ginza'}]

    def cache(self, fetch=None, key='secret'):
        return StatusCache(self.path, key, fetch=fetch or self.fetch, clock=lambda: self.now)

    def test_ttl_and_concurrent_single_flight(self):
        cache = self.cache()
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: cache.get(), range(24)))
        self.assertEqual(self.calls, 1)
        self.assertTrue(all(not item['stale'] for item in results))
        self.now += 119
        cache.get()
        self.assertEqual(self.calls, 1)
        self.now += 1
        cache.get()
        self.assertEqual(self.calls, 2)

    def test_restart_reuses_cache_and_never_persists_key(self):
        self.cache().get()
        self.assertEqual(self.cache().get()['notices'][0]['odpt:railway'], 'odpt.Railway:TokyoMetro.Ginza')
        self.assertEqual(self.calls, 1)
        self.assertNotIn('secret', self.path.read_text())

    def test_failure_retains_stale_and_respects_retry_after(self):
        cache = self.cache()
        cache.get()
        self.now += 120
        def fail(key):
            self.calls += 1
            raise HTTPError('https://example.invalid/?key=secret', 429, 'limited', {'Retry-After': '600'}, None)
        cache.fetch = fail
        result = cache.get()
        self.assertTrue(result['stale'])
        self.assertEqual(len(result['notices']), 1)
        self.assertEqual(result['retry_in_seconds'], 600)
        self.assertNotIn('secret', json.dumps(result))
        self.now += 599
        self.cache(fetch=fail).get()
        self.assertEqual(self.calls, 2)
        self.now += 1
        cache.get()
        self.assertEqual(self.calls, 3)

    def test_missing_key_and_empty_success(self):
        result = self.cache(key='').get()
        self.assertTrue(result['stale'])
        self.assertEqual(self.calls, 0)
        result = self.cache(fetch=lambda _: []).get()
        self.assertFalse(result['stale'])
        self.assertEqual(result['notices'], [])

    def test_failure_without_prior_data_backoff(self):
        def fail(key):
            self.calls += 1
            raise TimeoutError('secret')
        cache = self.cache(fetch=fail)
        first = cache.get()
        self.assertIsNone(first['updated_at'])
        self.assertGreaterEqual(first['retry_in_seconds'], 120)
        cache.get()
        self.assertEqual(self.calls, 1)

    def test_train_positions_station_section_and_unknown(self):
        trains = {'notices': [
            {'odpt:fromStation': 'A', 'odpt:toStation': 'B', 'dc:date': '2026-09-29T12:00:00+09:00'},
            {'odpt:fromStation': 'B'},
            {'odpt:fromStation': 'unknown'},
        ], 'updated_at': '2026-09-29T03:00:00+00:00', 'stale': False, 'error': None, 'cache_seconds': 120}
        stations = {'notices': [
            {'owl:sameAs': 'A', 'dc:title': '起点駅', 'geo:lat': 35.0, 'geo:long': 139.0},
            {'owl:sameAs': 'B', 'dc:title': '次駅', 'geo:lat': 35.1, 'geo:long': 139.1},
        ], 'error': None}
        result = train_positions(trains, stations)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['unmapped'], 1)
        self.assertEqual(result['pins'][0]['position_kind'], 'section_midpoint')
        self.assertAlmostEqual(result['pins'][0]['lat'], 35.05)
        self.assertAlmostEqual(result['pins'][0]['lon'], 139.05)
        self.assertEqual(result['pins'][0]['to'], '次駅')
        self.assertEqual(result['pins'][1]['position_kind'], 'station')
        trains['notices'] = []
        self.assertEqual(train_positions(trains, stations)['pins'], [])

    def test_http_routes_and_no_secret_exposure(self):
        trains = StatusCache(self.path.with_name('trains.json'), 'secret', fetch=lambda _: [])
        stations = StatusCache(self.path.with_name('stations.json'), 'secret', fetch=lambda _: [], ttl=86400)
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.cache(), Path(self.directory.name), trains, stations,
            SimpleNamespace(get=lambda: {'pins': [], 'calendar': 'Weekday'}),
            SimpleNamespace(network=lambda mode: {'ready': False}, positions=lambda mode, bounds, route: {'pins': []})))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        for route in ('/', '/app.js', '/trains.js', '/extras.js', '/all.js', '/style.css', '/api/status', '/api/trains', '/api/estimates', '/api/extra-network?mode=jr', '/api/extra-positions?mode=bus&bounds=139,35,140,36'):
            with urlopen(base + route) as response:
                body = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertNotIn('secret', body)
        with urlopen(Request(base + '/', headers={'Accept-Encoding': 'gzip'})) as response:
            self.assertEqual(response.headers['Content-Encoding'], 'gzip')
            self.assertIn('Tokyo', gzip.decompress(response.read()).decode())
        for route in ('/api/extra-network?mode=invalid', '/api/extra-positions?mode=bus&bounds=nan,35,140,36', '/api/extra-positions?mode=bus&bounds=140,35,139,36'):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + route)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        for route in ('/.env', '/../.env', '/data/stations'):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + route)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()


if __name__ == '__main__':
    unittest.main()
