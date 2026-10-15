"""最長経路プランナーの自己チェック。外部API・ファイルなし。"""
import time
import unittest

from transit_experiment.longpath import Network, bus_rail_base, plan


def small_network(*edges, routes=("r",)):
    net = Network()
    for route in routes:
        net.route(route, route, "rail", "#000000")
    for u, v, minutes, route in edges:
        net.node(u, u, 139.0, 35.0, "rail")
        net.node(v, v, 139.0, 35.0, "rail")
        net.edge(u, v, minutes, route)
    net.finalize()
    return net


class PlanTests(unittest.TestCase):
    def test_greedy_trap_finds_global_optimum(self):
        # 貪欲（最長区間優先）の初手 S-A(10) は罠。正解は S-B-C-G の15分。
        net = small_network(("S", "A", 10, "r"), ("A", "G", 1, "r"),
                            ("S", "B", 5, "r"), ("B", "C", 5, "r"), ("C", "G", 5, "r"))
        result = plan(net, "S", "G", 20)
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["minutes"], 15)
        self.assertEqual(result["nodes"], ["S", "B", "C", "G"])

    def test_edges_never_repeat_not_even_reversed(self):
        # 三角形: S→A→G→S は3辺すべて別エッジなので許可（出発地=目的地で一周）。
        net = small_network(("S", "A", 5, "r"), ("A", "G", 5, "r"), ("G", "S", 5, "r"))
        result = plan(net, "S", "S", 30)
        self.assertEqual(result["minutes"], 15)
        self.assertEqual(len(result["edges"]), len(set(result["edges"])))
        # ゴールが途中の駅なら最長は S→G→A の10分。使った2辺は相異なり、往復は不可。
        direct = plan(net, "S", "A", 30)
        self.assertEqual(direct["minutes"], 10)
        self.assertEqual(direct["nodes"], ["S", "G", "A"])

    def test_budget_makes_line_infeasible_or_fits(self):
        net = small_network(("S", "A", 10, "r"), ("A", "G", 10, "r"))
        self.assertEqual(plan(net, "S", "G", 15)["status"], "infeasible")
        result = plan(net, "S", "G", 20)
        self.assertEqual(result["minutes"], 20)
        self.assertEqual(result["nodes"], ["S", "A", "G"])

    def test_used_edges_are_excluded(self):
        net = small_network(("S", "A", 10, "r"), ("A", "G", 10, "r"))
        result = plan(net, "A", "G", 20, used_edges=(0,))
        self.assertEqual(result["minutes"], 10)
        self.assertEqual(plan(net, "S", "G", 20, used_edges=(0, 1))["status"], "infeasible")

    def test_allowed_routes_filter(self):
        net = small_network(("S", "G", 3, "a"), ("S", "G", 7, "b"), routes=("a", "b"))
        self.assertEqual(plan(net, "S", "G", 10)["minutes"], 7)
        self.assertEqual(plan(net, "S", "G", 10, allowed_routes=["a"])["minutes"], 3)

    def test_time_limit_returns_best_effort(self):
        # 8x8 格子で重みを少しずつ変える。打ち切り時刻を過ぎたら速やかに返す。
        net = Network()
        net.route("r", "r", "rail", "#000000")
        for y in range(8):
            for x in range(8):
                net.node(f"n{x}{y}", f"n{x}{y}", 139 + x * .01, 35 + y * .01, "rail")
        for y in range(8):
            for x in range(8):
                if x < 7:
                    net.edge(f"n{x}{y}", f"n{x + 1}{y}", 1 + (x + y) % 3, "r")
                if y < 7:
                    net.edge(f"n{x}{y}", f"n{x}{y + 1}", 1 + (x * y) % 3, "r")
        net.finalize()
        started = time.monotonic()
        result = plan(net, "n00", "n77", 60, compute_seconds=0.05)
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn(result["status"], ("optimal", "time_limit"))
        self.assertGreaterEqual(result["explored"], 1)

    def test_start_equals_goal_allows_empty_plan(self):
        net = small_network(("S", "A", 5, "r"))
        result = plan(net, "S", "S", 3)  # 予算3分では一周できない。
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["edges"], [])

    def test_parallel_routes_share_physical_section(self):
        net = small_network(("A", "B", 3, "a"), ("A", "B", 7, "b"), routes=("a", "b"))
        self.assertEqual(plan(net, "A", "A", 20)["minutes"], 0)
        self.assertEqual(plan(net, "B", "A", 20, used_edges=[0])["status"], "infeasible")

    def test_fractional_exact_deadline_and_zero(self):
        net = small_network(("S", "A", .1, "r"), ("A", "G", .2, "r"))
        self.assertEqual(plan(net, "S", "G", .3)["minutes"], .3)
        self.assertEqual(plan(net, "S", "S", 0)["nodes"], ["S"])

    def test_invalid_inputs_are_not_silently_ignored(self):
        net = small_network(("S", "G", 1, "r"))
        for budget in [float("nan"), float("inf"), -1, 1441]:
            with self.assertRaises(ValueError):
                plan(net, "S", "G", budget)
        for used in [[-1], [100], [True], "0", None]:
            with self.assertRaises(ValueError):
                plan(net, "S", "G", 10, used_edges=used)
        with self.assertRaises(ValueError):
            plan(net, "S", "G", 10, allowed_routes="r")

    def test_timeout_keeps_feasible_seed(self):
        net = small_network(("S", "A", 1, "r"), ("A", "G", 1, "r"))
        ticks = iter([0, 100])
        result = plan(net, "S", "G", 10, clock=lambda: next(ticks))
        self.assertEqual(result["status"], "time_limit")
        self.assertEqual(result["nodes"], ["S", "A", "G"])

    def test_long_chain_avoids_python_recursion(self):
        net = small_network(*[(str(i), str(i + 1), .1, "r") for i in range(1200)])
        self.assertEqual(plan(net, "0", "1200", 121)["minutes"], 120)

    def test_random_small_graphs_against_exhaustive_oracle(self):
        import random
        rng = random.Random(42)
        for _ in range(30):
            edges = [(str(a), str(b), rng.randint(1, 5), "r")
                     for a in range(5) for b in range(a + 1, 5) if rng.random() < .6]
            net = small_network(*edges)
            if "0" not in net.nodes or "4" not in net.nodes:
                continue
            def brute(node, used, elapsed):
                best = elapsed if node == "4" else -1
                for i, neighbour, weight in net.adj[node]:
                    if i not in used and elapsed + weight <= 15:
                        best = max(best, brute(neighbour, used | {i}, elapsed + weight))
                return best
            expected = brute("0", set(), 0)
            result = plan(net, "0", "4", 15)
            self.assertEqual(result["minutes"] if result["nodes"] else -1, expected)

    def test_equivalent_routes_do_not_duplicate_search(self):
        net = small_network(*[("S", "A", 1, "r")] * 100,
                            *[("A", "G", 1, "r")] * 100)
        result = plan(net, "S", "G", 10)
        self.assertEqual(result["status"], "optimal")
        self.assertEqual(result["minutes"], 2)
        self.assertLessEqual(result["explored"], 4)

    def test_parallel_filtered_trails_against_exhaustive_oracle(self):
        # 区間の占有解除、異なる重みの並行辺、フィルター前の誤った重複除去を検出。
        import random
        rng = random.Random(2026)
        for _ in range(80):
            edges = [(str(a), str(b), rng.randint(1, 6) / 10, route)
                     for a in range(4) for b in range(a + 1, 4)
                     for route in ("a", "b") if rng.random() < .65]
            net = small_network(*edges, routes=("a", "b"))
            if "0" not in net.nodes or "3" not in net.nodes:
                continue
            goal = rng.choice(("0", "3"))
            allowed = rng.choice((None, ["a"], ["b"], []))
            used = [0] if rng.random() < .3 else []
            pairs = [frozenset((e["u"], e["v"])) for e in net.edges]
            budget = rng.randint(0, 25)
            def brute(node, taken, elapsed):
                best = elapsed if node == goal else -1
                for i, neighbour, minutes in net.adj[node]:
                    weight = round(minutes * 10)
                    if (pairs[i] not in taken and elapsed + weight <= budget
                            and (allowed is None or net.edges[i]["route"] in allowed)):
                        best = max(best, brute(neighbour, taken | {pairs[i]}, elapsed + weight))
                return best
            expected = brute("0", {pairs[i] for i in used}, 0)
            result = plan(net, "0", goal, budget / 10, used_edges=used, allowed_routes=allowed)
            self.assertEqual(result["status"], "infeasible" if expected < 0 else "optimal")
            self.assertEqual(round(result["minutes"] * 10) if result["nodes"] else -1, expected)
            sections = [pairs[i] for i in result["edges"]]
            self.assertEqual(len(sections), len(set(sections)))
            self.assertFalse(set(sections) & {pairs[i] for i in used})

    def test_http_plan_validation_and_route_page(self):
        import json
        import tempfile
        import threading
        from pathlib import Path
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from transit_experiment.longpath import Service
        from transit_experiment.web import make_handler
        with tempfile.TemporaryDirectory() as tmp:
            service = Service(Path(tmp), "not-a-real-secret")
            service._network = small_network(("S", "G", 5, "r"))
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(None, Path(tmp), longpath_service=service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                for path in ["/route", "/route.js", "/api/route/network"]:
                    with urlopen(base + path) as response:
                        self.assertNotIn(b"not-a-real-secret", response.read())
                def request(body):
                    return urlopen(Request(base + "/api/route/plan", json.dumps(body).encode(),
                                           {"Content-Type": "application/json"}))
                with request({"start": "S", "goal": "G", "budget_minutes": 5}) as response:
                    self.assertEqual(json.load(response)["minutes"], 5)
                with self.assertRaises(HTTPError) as error:
                    request({"start": "S", "goal": "G", "budget_minutes": float("nan")})
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class HelperTests(unittest.TestCase):
    def test_missing_bus_times_preserve_fallback_section(self):
        from transit_experiment.longpath import _sample_legs
        samples, stops = {}, set()
        _sample_legs(samples, stops, "r", [("A", None, None), ("B", None, None)])
        self.assertEqual(samples, {("r", "A", "B"): []})
        self.assertEqual(stops, {"A", "B"})

    def test_metro_missing_station_does_not_bridge_gap(self):
        import json
        import tempfile
        from pathlib import Path
        from transit_experiment.longpath import build_metro
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "realtime").mkdir()
            payload = {"fetched": 0, "railways": [{"owl:sameAs": "TokyoMetro.Test",
                "odpt:stationOrder": [{"odpt:index": 2, "odpt:station": "C"},
                                      {"odpt:index": 0, "odpt:station": "A"},
                                      {"odpt:index": 1, "odpt:station": "missing"},
                                      {"odpt:index": 3, "odpt:station": "D"}]}],
                "stations": [{"owl:sameAs": n, "dc:title": n, "geo:lat": 35, "geo:long": 139}
                             for n in ["A", "C", "D"]]}
            (root / "realtime" / "metro-network-cache.json").write_text(json.dumps(payload), encoding="utf8")
            net = Network()
            build_metro(net, root, "")
            self.assertEqual([(e["u"], e["v"]) for e in net.edges], [("station:C", "station:D")])

    def test_bus_rail_base(self):
        self.assertEqual(bus_rail_base("新宿駅西口"), "新宿")
        self.assertEqual(bus_rail_base("東京駅八重洲口"), "東京")
        self.assertEqual(bus_rail_base("両国駅前"), "両国")
        self.assertIsNone(bus_rail_base("浅草雷門"))
        self.assertIsNone(bus_rail_base("駅前"))


if __name__ == "__main__":
    unittest.main()
