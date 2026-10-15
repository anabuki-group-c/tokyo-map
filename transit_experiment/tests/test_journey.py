import unittest

from transit_experiment.journey import Network, Stop, longest_journey, metro_network

# Origin is next to A and the destination next to D (about 1.8 km apart, beyond walking range of each other).
A, D = Stop("A", "A駅", 35.0, 139.0), Stop("D", "D駅", 35.0, 139.02)
E, F = Stop("E", "E駅", 35.05, 139.0), Stop("F", "F駅", 35.05, 139.02)
H = Stop("H", "H駅", 34.95, 139.01)


def at(hhmm):
    hour, minute = map(int, hhmm.split(":"))
    return hour * 3600 + minute * 60


def network():
    net = Network()
    net.add_trip("direct", "直通線", "metro", [(A, None, at("10:00")), (D, at("10:05"), None)])
    net.add_trip("loop", "環状線", "metro", [(A, None, at("10:01")), (E, at("10:10"), at("10:10")),
                                            (F, at("10:20"), at("10:20")), (D, at("10:30"), None)])
    # Longer than the loop, but only by passing A a second time.
    net.add_trip("revisit", "戻り線", "bus", [(E, None, at("10:11")), (A, at("10:16"), at("10:16")),
                                             (H, at("10:22"), at("10:22")), (D, at("10:28"), None)])
    return net


class LongestJourneyTests(unittest.TestCase):
    origin, destination = (35.0, 139.0005), (35.0, 139.0195)

    def plan(self, deadline):
        return longest_journey(network(), self.origin, self.destination, at("09:58"), at(deadline))

    def test_takes_the_longest_ride_that_arrives_in_time(self):
        journey = self.plan("10:40")
        rides = [leg for leg in journey["legs"] if leg["mode"] != "walk"]
        self.assertEqual([leg["line"] for leg in rides], ["環状線"])
        self.assertGreater(journey["ride_distance_m"], 12000)
        names = [stop["name"] for leg in rides for stop in leg["stops"]]
        self.assertEqual(len(names), len(set(names)), "a station was passed twice")

    def test_deadline_rules_out_the_detour(self):
        journey = self.plan("10:15")
        self.assertEqual([leg["line"] for leg in journey["legs"] if leg["mode"] != "walk"], ["直通線"])
        self.assertLessEqual(journey["arrive"], "10:15")

    def test_nothing_arrives_in_time(self):
        self.assertIsNone(self.plan("10:03"))

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
