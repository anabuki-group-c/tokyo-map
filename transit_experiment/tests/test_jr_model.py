import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from transit_experiment.jr_model import JRModel, ASSUMPTIONS
from transit_experiment.timetable import JST


class JRModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.models = [('Test', '試験線', '#80c241', 2, '線路', 'A B C')]
        self.path.joinpath('jr-east-stations.geojson').write_text(json.dumps({'features': [
            {'properties': {'N02_005': name, 'N02_003': '線路'}, 'geometry': {'type': 'Point', 'coordinates': [lon, 35]}}
            for name, lon in [('A', 139), ('B', 139.01), ('C', 139.02)]]}))
        self.path.joinpath('jr-east-railroads.geojson').write_text(json.dumps({'features': [
            {'properties': {'N02_003': '線路'}, 'geometry': {'type': 'LineString', 'coordinates': [[139, 35], [139.01, 35], [139.02, 35]]}}]}))
        self.model = JRModel(self.path, self.models)

    def test_bidirectional_moving_and_deterministic(self):
        now = datetime(2026, 9, 29, 5, 1, tzinfo=JST)
        result = self.model.positions(now)
        self.assertEqual(len(result['pins']), 2)
        self.assertEqual({p['heading'] for p in result['pins']}, {90, 270})
        self.assertEqual(result, self.model.positions(now))
        next_result = self.model.positions(now + timedelta(seconds=10))
        before = {p['id']: p['lon'] for p in result['pins']}
        for p in next_result['pins']:
            self.assertNotEqual(p['lon'], before[p['id']])
        self.assertEqual(result['basis'], 'headway_model')
        self.assertEqual(result['assumptions'], ASSUMPTIONS)

    def test_night_window_and_previous_service_day(self):
        self.assertEqual(self.model.positions(datetime(2026, 9, 30, 2, tzinfo=JST))['pins'], [])
        self.assertEqual(self.model.positions(datetime(2026, 9, 30, 4, tzinfo=JST))['pins'], [])
        midnight = self.model.positions(datetime(2026, 9, 30, 0, 10, tzinfo=JST))['pins']
        self.assertTrue(midnight)
        self.assertTrue(all(':2026-09-29:' in p['id'] for p in midnight))

    def test_network_station_routes_and_loop(self):
        network = self.model.network()
        self.assertEqual(len(network['stations']), 3)
        self.assertTrue(all(s['routes'] == ['Test'] for s in network['stations']))
        loop = JRModel(self.path, [('Loop', '環状', '#80c241', 2, '線路', 'A B C A')])
        coordinates = loop.network()['geojson']['features'][0]['geometry']['coordinates']
        self.assertEqual(coordinates[0][0], coordinates[-1][-1])

    def test_missing_station_does_not_bridge_unknown_segment(self):
        model = JRModel(self.path, [('Missing', '欠落', '#80c241', 2, '線路', 'A D C')])
        self.assertEqual(model.routes, [])
        self.assertEqual(model.omitted, ['Missing'])
        self.assertEqual(model.positions()['pins'], [])

    def test_huge_track_detour_falls_back_to_marked_chord(self):
        a, b = (139, 35), (139.01, 35)
        self.model.geometry.paths[('Test', a, b)] = [a, b], [1], 1, False
        self.model.prepare_path('Test', a, b)
        self.assertTrue(self.model.geometry.paths[('Test', a, b)][3])
        self.assertLess(self.model.geometry.paths[('Test', a, b)][2], .02)


if __name__ == '__main__':
    unittest.main()
