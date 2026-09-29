import csv
import json
import io
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

from transit_experiment.gtfs import Feed, ExtraService, active_services, gtfs_seconds, import_feed
from transit_experiment.timetable import JST


class GTFSTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source, self.db = self.root / 'feed.zip', self.root / 'feed.sqlite'
        content = {
            'routes.txt': 'route_id,route_short_name,route_color\nr,テスト系統,00AA88\n',
            'stops.txt': 'stop_id,stop_name,stop_lon,stop_lat\na,A停留所,139,35\nb,B停留所,139.002,35\n',
            'trips.txt': 'trip_id,route_id,service_id,shape_id,trip_headsign\nt,r,s,shape,B行\nnight,r,s,shape,B行\n',
            'stop_times.txt': 'trip_id,stop_sequence,stop_id,arrival_time,departure_time\nt,1,a,12:00:00,12:01:00\nt,2,b,12:05:00,12:05:00\nnight,1,a,24:00:00,24:00:00\nnight,2,b,24:10:00,24:10:00\n',
            'shapes.txt': 'shape_id,shape_pt_sequence,shape_pt_lon,shape_pt_lat\nshape,1,139,35\nshape,2,139.001,35.001\nshape,3,139.002,35\n',
            'calendar.txt': 'service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\ns,1,1,1,1,1,0,0,20260101,20261231\n',
            'calendar_dates.txt': 'service_id,date,exception_type\ns,20260929,2\ns,20261003,1\n',
            'feed_info.txt': 'feed_start_date,feed_end_date,feed_version\n20260101,20261231,test\n',
        }
        with zipfile.ZipFile(self.source, 'w') as archive:
            for name, text in content.items(): archive.writestr(name, text)
        import_feed(self.source, self.db)
        self.feed = Feed(self.db, 'bus')

    def test_calendars_addition_removal_and_validity(self):
        with self.feed.connect() as db:
            self.assertEqual(active_services(db, datetime(2026, 9, 29).date()), set())
            self.assertEqual(active_services(db, datetime(2026, 9, 30).date()), {'s'})
            self.assertEqual(active_services(db, datetime(2026, 10, 3).date()), {'s'})
            self.assertEqual(active_services(db, datetime(2027, 1, 4).date()), set())

    def test_route_shape_position_and_direction(self):
        result = self.feed.positions(datetime(2026, 9, 30, 12, 3, tzinfo=JST))
        self.assertEqual(len(result['pins']), 1)
        pin = result['pins'][0]
        self.assertAlmostEqual(pin['lon'], 139.001)
        self.assertAlmostEqual(pin['lat'], 35.001)
        self.assertFalse(pin['geometry_approximate'])
        self.assertIsNotNone(pin['heading'])
        self.assertEqual(self.feed.network()['routes'][0]['name'], 'テスト系統')

    def test_previous_service_day_and_24_hour_times(self):
        self.assertEqual(gtfs_seconds('24:05:00'), 86700)
        result = self.feed.positions(datetime(2026, 10, 1, 0, 5, tzinfo=JST))
        self.assertEqual(len(result['pins']), 1)
        self.assertTrue(result['pins'][0]['id'].startswith('20260930:night'))

    def test_dwell_and_expired_feed(self):
        result = self.feed.positions(datetime(2026, 9, 30, 12, 0, 30, tzinfo=JST))
        self.assertEqual(len(result['pins']), 1)
        self.assertAlmostEqual(result['pins'][0]['lat'], 35)
        self.feed.position_snapshot = None
        self.assertEqual(self.feed.positions(datetime(2027, 1, 5, 12, 3, tzinfo=JST))['pins'], [])

    def test_viewport_filter_and_cap(self):
        service = ExtraService.__new__(ExtraService)
        service.feeds, service.messages = {'bus': self.feed}, {'bus': ''}
        self.feed.positions = lambda: {'pins': [{'route': 'r', 'lon': 139, 'lat': 35} for _ in range(700)], 'ready': True}
        result = service.positions('bus', (138, 34, 140, 36))
        self.assertEqual(len(result['pins']), 600)
        self.assertTrue(result['limited'])
        self.assertEqual(service.positions('bus', (140, 34, 141, 36))['pins'], [])
        self.assertEqual(service.positions('bus', route='other')['pins'], [])

    def test_jr_station_pins_without_timetable(self):
        processed = self.root / 'data' / 'processed'
        processed.mkdir(parents=True)
        (processed / 'jr-east-railroads.geojson').write_text(json.dumps({'features': []}))
        features = [{'properties': {'N02_005': '新宿', 'N02_005g': 'group1', 'N02_003': route},
                     'geometry': {'type': 'LineString', 'coordinates': [[139.7, 35.69], [139.7, 35.7]]}}
                    for route in ['山手線', '中央線']]
        (processed / 'jr-east-stations.geojson').write_text(json.dumps({'features': features}))
        service = ExtraService.__new__(ExtraService)
        service.root, service.network_cache, service.messages = self.root, {}, {'jr': '時刻表未取得'}
        result = service.network('jr')
        self.assertFalse(result['ready'])
        self.assertEqual(len(result['stations']), 1)
        self.assertEqual(result['stations'][0]['routes'], ['山手線', '中央線'])
        self.assertAlmostEqual(result['stations'][0]['lat'], 35.695)

    def test_jr_gtfs_station_pins(self):
        result = Feed(self.db, 'jr').network()
        self.assertEqual(len(result['stations']), 2)
        self.assertEqual(result['stations'][0]['routes'], ['r'])

    def test_malformed_feed_keeps_previous_database(self):
        before = self.db.read_bytes()
        with zipfile.ZipFile(self.source, 'w') as archive: archive.writestr('bad.txt', 'bad')
        with self.assertRaises(ValueError): import_feed(self.source, self.db)
        self.assertEqual(self.db.read_bytes(), before)


if __name__ == '__main__': unittest.main()
