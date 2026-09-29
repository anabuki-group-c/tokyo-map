import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from transit_experiment.timetable import JST, RailGeometry, active_segment, estimate_positions, seconds, service_calendar, timetable_stops
from transit_experiment.web import fetch_timetables


class TimetableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'rail.json'
        self.path.write_text(json.dumps({'features': [{'properties': {'N02_003': '銀座線'},
            'geometry': {'type': 'LineString', 'coordinates': [[139, 35], [139.001, 35.001], [139.002, 35]]}}]}))
        self.geometry = RailGeometry(self.path)
        self.railway = 'odpt.Railway:TokyoMetro.Ginza'
        self.table = {'odpt:railway': self.railway, 'odpt:trainNumber': 'A1', 'odpt:calendar': 'odpt.Calendar:Weekday',
            'odpt:trainTimetableObject': [
                {'odpt:departureStation': 'A', 'odpt:departureTime': '12:00'},
                {'odpt:arrivalStation': 'B', 'odpt:arrivalTime': '12:04', 'odpt:departureTime': '12:05'},
                {'odpt:arrivalStation': 'C', 'odpt:arrivalTime': '12:10'}]}
        self.stations = [{'owl:sameAs': 'A', 'geo:long': 139, 'geo:lat': 35, 'dc:title': 'A駅'},
                         {'owl:sameAs': 'B', 'geo:long': 139.002, 'geo:lat': 35, 'dc:title': 'B駅'}]
        self.now = datetime(2026, 9, 29, 12, 2, tzinfo=JST)
        self.empty = {'notices': [], 'stale': False}

    def estimate(self, live=None, status=None):
        return estimate_positions([self.table], self.stations, live or self.empty, status or self.empty, self.now, self.geometry)

    def test_calendar_weekday_holiday_weekend_and_midnight(self):
        self.assertEqual(service_calendar(self.now)[1], 'Weekday')
        self.assertEqual(service_calendar(datetime(2026, 9, 21, 12, tzinfo=JST))[1], 'SaturdayHoliday')
        self.assertEqual(service_calendar(datetime(2026, 10, 3, 12, tzinfo=JST))[1], 'SaturdayHoliday')
        day, calendar = service_calendar(datetime(2026, 10, 3, 0, 30, tzinfo=JST))
        self.assertEqual(str(day), '2026-10-02')
        self.assertEqual(calendar, 'Weekday')
        with self.assertRaises(ValueError):
            service_calendar(datetime(2099, 1, 1, tzinfo=JST))

    def test_dwell_before_after_and_midnight(self):
        stops = timetable_stops(self.table)
        self.assertIsNone(active_segment(stops, seconds('11:59')))
        self.assertIsNone(active_segment(stops, seconds('12:11')))
        self.assertEqual(active_segment(stops, seconds('12:02')), ('A', 'B', 0.5))
        self.assertEqual(active_segment(stops, seconds('12:04') + 30), ('B', 'B', 0.0))
        self.assertEqual(seconds('00:15'), seconds('24:15'))
        self.assertEqual(active_segment([('A', seconds('23:59'), seconds('23:59')), ('B', seconds('00:01'), seconds('00:01'))], seconds('00:00')), ('A', 'B', 0.5))

    def test_interpolates_on_rail_path_not_station_chord(self):
        pin = self.estimate()['pins'][0]
        self.assertAlmostEqual(pin['lon'], 139.001)
        self.assertAlmostEqual(pin['lat'], 35.001)
        self.assertFalse(pin['geometry_approximate'])
        self.assertEqual(pin['basis'], 'published_timetable')
        self.assertEqual(pin['planned_start'], '12:00')
        self.assertEqual(pin['planned_end'], '12:04')
        self.assertEqual(pin['planned_kind'], 'travel')
        self.assertIsNone(pin['delay_seconds'])
        self.assertFalse(pin['status_known'])

    def test_heading_follows_path_and_reverses(self):
        a, b = (139, 35), (139.002, 35)
        forward = self.geometry.heading('Ginza', a, b, 0.25)
        backward = self.geometry.heading('Ginza', b, a, 0.75)
        self.assertTrue(0 < forward < 90)
        self.assertAlmostEqual((backward - forward) % 360, 180, places=1)
        self.assertTrue(90 < self.geometry.heading('Ginza', a, b, 0.75) < 180)
        self.assertIsNone(self.geometry.heading('Ginza', a, a, 0))

    def test_heading_during_dwell_and_at_terminus(self):
        self.stations.append({'owl:sameAs': 'C', 'geo:long': 139.004, 'geo:lat': 35, 'dc:title': 'C駅'})
        self.now = datetime(2026, 9, 29, 12, 4, 30, tzinfo=JST)
        pin = self.estimate()['pins'][0]
        self.assertEqual(pin['heading_target'], 'C駅')
        self.assertIsNotNone(pin['heading'])
        self.assertEqual(pin['planned_start'], '12:04')
        self.assertEqual(pin['planned_end'], '12:05')
        self.assertEqual(pin['planned_kind'], 'dwell')
        self.now = datetime(2026, 9, 29, 12, 10, tzinfo=JST)
        pin = self.estimate()['pins'][0]
        self.assertEqual(pin['heading_target'], 'C駅')
        self.assertIsNotNone(pin['heading'])

    def test_numeric_delay_shifts_schedule_not_all_trains(self):
        live = {'stale': False, 'notices': [{'odpt:railway': self.railway, 'odpt:trainNumber': 'A1', 'odpt:delay': 60,
                                           'dc:date': self.now.isoformat()}]}
        pin = self.estimate(live=live)['pins'][0]
        self.assertEqual(pin['progress'], 0.25)
        self.assertEqual(pin['delay_seconds'], 60)
        self.assertTrue(pin['alert'])
        live['stale'] = True
        self.assertEqual(self.estimate(live=live)['pins'][0]['progress'], 0.5)
        live['stale'] = False
        live['notices'][0]['dc:date'] = '2026-09-29T11:00:00+09:00'
        self.assertIsNone(self.estimate(live=live)['pins'][0]['delay_seconds'])

    def test_route_notice_red_without_inventing_delay(self):
        status = {'stale': False, 'notices': [{'odpt:railway': self.railway, 'odpt:trainInformationText': {'ja': '一部列車に約10分の遅れ'}}]}
        pin = self.estimate(status=status)['pins'][0]
        self.assertTrue(pin['alert'])
        self.assertEqual(pin['progress'], 0.5)
        self.assertIsNone(pin['delay_seconds'])
        status['stale'] = True
        self.assertFalse(self.estimate(status=status)['pins'][0]['alert'])

    def test_unknown_station_not_fabricated(self):
        self.stations = []
        result = self.estimate()
        self.assertEqual(result['pins'], [])
        self.assertEqual(result['unmapped'], 1)

    def test_scheduled_window_after_midnight(self):
        from transit_experiment.timetable import scheduled_window
        stops = [('A', seconds('23:59'), seconds('23:59')), ('B', seconds('00:01'), seconds('00:01'))]
        self.assertEqual(scheduled_window(stops, seconds('00:00')), ('23:59', '翌00:01', 'travel'))

    def test_truncated_api_not_accepted(self):
        with patch('transit_experiment.web.fetch_records', return_value=[{}] * 1000):
            with self.assertRaises(ValueError):
                fetch_timetables('secret', 'Ginza', 'Weekday')


if __name__ == '__main__':
    unittest.main()
