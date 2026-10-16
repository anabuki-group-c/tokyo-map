import unittest

from transit_experiment.journey import Areas, Network, Stop, longest_journey, metro_network

# Origin is next to A and the destination next to D (about 1.8 km apart, beyond walking range of each other).
A, D = Stop("A", "A駅", 35.0, 139.0), Stop("D", "D駅", 35.0, 139.02)
E, F = Stop("E", "E駅", 35.05, 139.0), Stop("F", "F駅", 35.05, 139.02)
H = Stop("H", "H駅", 34.95, 139.0)


def at(hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return hour * 3600 + minute * 60


def network():
    net = Network()
    net.add_trip("direct", "直通線", "metro", [(A, None, at("10:00")), (D, at("10:05"), None)])
    net.add_trip("loop", "環状線", "metro", [(A, None, at("10:01")), (E, at("10:10"), at("10:10")),
                                            (F, at("10:20"), at("10:20")), (D, at("10:30"), None)])
    # Arrives later than the direct train, but E -> H runs straight through A's area (an area already left)
    # without stopping there, so it can only be reached by riding 環状線 to E first.
    net.add_trip("revisit", "戻り線", "bus", [(E, None, at("10:11")), (H, at("10:22"), at("10:22")), (D, at("10:28"), None)])
    net.add_trip("late", "直通線", "metro", [(A, None, at("10:33")), (D, at("10:38"), None)])
    return net


class LongestJourneyTests(unittest.TestCase):
    origin, destination = (35.0, 139.0005), (35.0, 139.0195)

    def plan(self, deadline, **options):
        return longest_journey(network(), self.origin, self.destination, at("09:58"), at(deadline), **options)

    def lines(self, journey):
        return [leg["line"] for leg in journey["legs"] if leg["mode"] != "walk"]

    def test_arrives_as_late_as_possible_waiting_for_a_later_train(self):
        journey, complete = self.plan("10:40")
        self.assertTrue(complete)
        self.assertEqual(self.lines(journey), ["直通線"])
        self.assertEqual(journey["legs"][1]["depart"], "10:33")  # Waited for the late train, not the 10:00.
        parts = journey["ride_seconds"] + journey["wait_seconds"] + journey["walk_seconds"]
        self.assertEqual(journey["total_seconds"], parts)
        self.assertGreater(journey["total_seconds"], at("10:38") - at("09:58"))

    def test_never_re_enters_an_area_already_left(self):
        # Without the rule, 環状線 -> 戻り線 (back through A) arriving 10:28 would win.
        journey, complete = self.plan("10:29")
        self.assertTrue(complete)
        self.assertEqual(self.lines(journey), ["直通線"])
        self.assertEqual(journey["legs"][-1]["arrive"], "10:05")

    def test_loop_arrives_later_than_the_direct_train(self):
        journey, _ = self.plan("10:32")
        self.assertEqual(self.lines(journey), ["環状線"])

    def test_nothing_arrives_in_time(self):
        self.assertEqual(self.plan("10:03"), (None, True))

    def test_time_limit_reports_an_unfinished_search(self):
        journey, complete = self.plan("10:40", time_limit=-1)
        self.assertFalse(complete)

    def test_passing_through_an_area_counts_as_a_visit(self):
        stops = {s.id: s for s in (A, D, Stop("M", "M駅", 35.0, 139.01))}
        areas = Areas(stops, 300)
        a, d, m = (A.lat, A.lon), (D.lat, D.lon), (35.0, 139.01)
        moved = areas.move(areas.at(a), 0, a, d)  # Rides through M's area without stopping.
        self.assertIsNotNone(moved)
        self.assertIsNone(areas.move(*moved, d, m))  # Coming back into M is a revisit.

    def test_through_train_is_split_where_stations_are_unknown(self):
        table = {"odpt:railway": "odpt.Railway:TokyoMetro.Hanzomon", "odpt:trainNumber": "1", "odpt:calendar": "odpt.Calendar:Weekday",
                 "odpt:trainTimetableObject": [
                     {"odpt:departureStation": "X", "odpt:departureTime": "10:00"},
                     {"odpt:departureStation": "Y", "odpt:departureTime": "10:02"},
                     {"odpt:departureStation": "Other", "odpt:departureTime": "10:04"},
                     {"odpt:departureStation": "Z", "odpt:departureTime": "10:06"},
                     {"odpt:arrivalStation": "W", "odpt:arrivalTime": "10:08"}]}
        stations = [{"owl:sameAs": s, "dc:title": s, "geo:lat": 35, "geo:long": 139 + i / 100} for i, s in enumerate("XYZW")]
        hops = metro_network([table], stations, "Weekday").connections
        self.assertEqual([(c.frm, c.to) for c in hops], [("X", "Y"), ("Z", "W")])
        self.assertNotEqual(hops[0].trip, hops[1].trip)


if __name__ == "__main__":
    unittest.main()
