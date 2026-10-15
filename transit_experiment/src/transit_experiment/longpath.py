"""最長乗車経路実験（/route）。

グラフ: 無向。エッジ = 「ある路線で1区間乗る」こと（同じ両端の辺は使用権を共有）。重み = 乗車時間（分）。
- JR: jr_model.MODELS の12系統の駅順 + N02駅座標。所要時間は簡易モデルと同じ
  仮定（45km/h、最低30秒）。追加データ取得は不要（jr-east-stations.geojson が必要）。
- 東京メトロ: ODPT の odpt:Railway（odpt:stationOrder）+ odpt:Station 座標。
  所要時間は距離÷35km/hの仮定（停車時間込みの概算値）。24時間のディスクキャッシュ。
- 都営バス: bus-gtfs.sqlite の stop_times から区間ごとの実ダイヤ時間の中央値。
  フォールバックは距離÷15km/h。バス停名が「<駅名>駅…」のときだけ鉄道ノードに統合。
乗り換え = ノードの一致（JR・メトロは駅名一致で統合、乗り換え時間は仕様どおり0分）。

探索: 辺を二度通らない（逆走も不可）トレイル（頂点の再訪可）で、目的 = 乗車時間合計の最大化、
制約 = 合計が予算（分）以内でゴールに到着。NP-hard なので分岐限定法の any-time 探索。
"""
from __future__ import annotations

import heapq
import json
import sqlite3
import statistics
import math
import threading
import time
from pathlib import Path

from transit_experiment.jr_model import ASSUMPTIONS, MODELS
from transit_experiment.timetable import distance

METERS_PER_DEGREE = 111_000
BUS_FALLBACK_KMH = 15.0
METRO_COLORS = {
    "Ginza": "#ff9500", "Marunouchi": "#f45468", "MarunouchiBranch": "#f45468",
    "Hibiya": "#bec7ce", "Tozai": "#32bce3", "Chiyoda": "#31cf98",
    "Yurakucho": "#d8bb72", "Hanzomon": "#ae8cfa", "Namboku": "#35c9bb",
    "Fukutoshin": "#c59068",
}
DEFAULT_COMPUTE_SECONDS = 3.0


class Network:
    def __init__(self):
        self.nodes = {}   # id -> {id, name, lon, lat, modes:set}
        self.edges = []   # {u, v, minutes, route}
        self.routes = {}  # id -> {id, name, mode, color}
        self.adj = {}     # node id -> [(edge index, neighbour, minutes)] 降順

    def node(self, node_id, name, lon, lat, mode):
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = {"id": node_id, "name": name, "lon": lon, "lat": lat, "modes": {mode}}
        else:
            existing["modes"].add(mode)
        return node_id

    def route(self, route_id, name, mode, color):
        self.routes.setdefault(route_id, {"id": route_id, "name": name, "mode": mode, "color": color})

    def edge(self, u, v, minutes, route_id):
        minutes = round(minutes, 1)
        if u == v or minutes <= 0:
            return
        self.edges.append({"u": u, "v": v, "minutes": minutes, "route": route_id})

    def finalize(self):
        self.adj = {node: [] for node in self.nodes}
        for index, edge in enumerate(self.edges):
            self.adj[edge["u"]].append((index, edge["v"], edge["minutes"]))
            self.adj[edge["v"]].append((index, edge["u"], edge["minutes"]))
        # 最長の区間から試す貪欲ダイブで良い解を早く見つけ、限定値を強くする。
        for neighbours in self.adj.values():
            neighbours.sort(key=lambda item: item[2], reverse=True)


def build_jr(network: Network, processed: Path):
    path = processed / "jr-east-stations.geojson"
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    candidates = {}
    for feature in features:
        properties, geometry = feature["properties"], feature["geometry"]
        if geometry["type"] == "LineString" and geometry["coordinates"]:
            first, last = geometry["coordinates"][0], geometry["coordinates"][-1]
            point = ((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)
        elif geometry["type"] == "Point":
            point = tuple(geometry["coordinates"][:2])
        else:
            continue
        candidates.setdefault(properties.get("N02_005"), []).append((properties.get("N02_003"), point))
    speed = ASSUMPTIONS["speed_kmh"] / 3.6
    for slug, title, color, _headway, tracks, order in MODELS:
        allowed = set(tracks.split())
        names = order.split()
        points = []
        for name in names:
            options = candidates.get(name)
            if not options:
                points = None
                break
            points.append(next((p for track, p in options if track in allowed), options[0][1]))
        if not points:
            continue  # 駅データが欠けている系統は静かに省く（jr_model と同じ方針）。
        route_id = f"jr:{slug}"
        network.route(route_id, title, "rail", color)
        for name, point in zip(names, points):
            network.node(f"station:{name}", name, point[0], point[1], "rail")
        for (a_name, a), (b_name, b) in zip(zip(names, points), zip(names[1:], points[1:])):
            seconds = max(ASSUMPTIONS["dwell_seconds"], distance(a, b) * METERS_PER_DEGREE / speed)
            network.edge(f"station:{a_name}", f"station:{b_name}", seconds / 60, route_id)


def _metro_payload(key: str, cache_path: Path):
    """odpt:Railway + odpt:Station を24時間ディスクキャッシュ。失敗時は期限切れでも再利用。"""
    stale = None
    if cache_path.exists():
        try:
            stale = json.loads(cache_path.read_text(encoding="utf-8"))
            if not key or time.time() - stale.get("fetched", 0) < 86400:
                return stale
        except (OSError, ValueError):
            stale = None
    from transit_experiment.web import fetch_records
    try:
        payload = {
            "fetched": time.time(),
            "railways": fetch_records(key, "https://api.odpt.org/api/v4/odpt:Railway"),
            "stations": fetch_records(key, "https://api.odpt.org/api/v4/odpt:Station"),
        }
    except Exception:
        if stale is not None:
            return stale
        raise
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def build_metro(network: Network, processed: Path, key: str):
    if not key and not (processed / "realtime" / "metro-network-cache.json").exists():
        return "東京メトロ: ODPTキー未設定のため含めていません。"
    try:
        payload = _metro_payload(key, processed / "realtime" / "metro-network-cache.json")
    except Exception:
        return "東京メトロ: 路線データを取得できませんでした。"
    stations = {record.get("owl:sameAs"): record for record in payload["stations"]}
    for railway in payload["railways"]:
        same_as = str(railway.get("owl:sameAs", ""))
        route_id = "metro:" + same_as.rsplit(".", 1)[-1]
        network.route(route_id, railway.get("dc:title") or route_id, "rail", METRO_COLORS.get(route_id[6:], "#888888"))
        chain = []
        for entry in sorted(railway.get("odpt:stationOrder") or [], key=lambda e: e["odpt:index"]):
            record = stations.get(entry.get("odpt:station"))
            if record and record.get("dc:title") and isinstance(record.get("geo:lat"), (int, float)):
                chain.append((record["dc:title"], float(record["geo:long"]), float(record["geo:lat"])))
            else:
                chain.append(None)
        for a, b in zip(chain, chain[1:]):
            if a is None or b is None:
                continue
            a_name, a_lon, a_lat = a
            b_name, b_lon, b_lat = b
            network.node(f"station:{a_name}", a_name, a_lon, a_lat, "rail")
            network.node(f"station:{b_name}", b_name, b_lon, b_lat, "rail")
            # ponytail: 直線距離÷35km/h（停車込みの概算）。N02線形に沿った補正は未実装、
            # 地下鉄実速度は非公開。限界: 区間時間が実ダイヤと±数十%ずれる。昇格路径:
            # ODPT odpt:StationTimetable から区間平均所要を取る。
            seconds = max(60.0, distance((a_lon, a_lat), (b_lon, b_lat)) * METERS_PER_DEGREE / (35 / 3.6))
            network.edge(f"station:{a_name}", f"station:{b_name}", seconds / 60, route_id)


def bus_rail_base(name: str):
    """「新宿駅西口」→「新宿」。駅を含まない停留所名は統合しない。"""
    index = name.find("駅")
    return name[:index] if index > 0 else None


def build_bus(network: Network, processed: Path):
    db_path = processed / "bus-gtfs.sqlite"
    if not db_path.exists():
        return "都営バス: GTFS未取得（サーバー起動時に自動取得されます）。"
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        samples = {}  # (route, stop_a, stop_b) ソート済みペア -> [分]
        used_stops = set()
        trip, route, chain = None, None, []
        for row in db.execute(
            "SELECT tm.trip, t.route, tm.stop, tm.departure, tm.arrival FROM times tm"
            " JOIN trips t ON t.id = tm.trip ORDER BY tm.trip, tm.seq"
        ):
            if row[0] != trip:
                _sample_legs(samples, used_stops, route, chain)
                trip, route, chain = row[0], row[1], []
            chain.append((row[2], row[3], row[4]))
        _sample_legs(samples, used_stops, route, chain)
        routes = {r[0]: r for r in db.execute("SELECT id, name, color FROM routes")}
        # 鉄道ノード（駅名統合）を先に確定させてからバス停を登録する。
        rail_names = {node["name"]: node_id for node_id, node in network.nodes.items()
                      if node_id.startswith("station:")}
        stop_nodes = {}
        named_stops = {}
        for stop_id, name, lon, lat in db.execute("SELECT id, name, lon, lat FROM stops ORDER BY id"):
            if stop_id not in used_stops:
                continue
            base = bus_rail_base(name or "")
            # ponytail: 「<駅名>駅…」の接頭一致でのみ鉄道と統合（乗り換え0分）。
            # 限界: 駅名を含まない停留所・別名の停留所は乗り換え不可。昇格路径: 座標近接マッチ。
            node_id = rail_names.get(base) if base else None
            # ponytail: 同名かつ150m以内の対向停留所を同一ノードにする。
            # 道路形状の一致判定は未実装。異名の同一道路区間は識別できない。
            if not node_id:
                node_id = next((n for n in named_stops.get(name, [])
                    if distance((lon, lat), (network.nodes[n]["lon"], network.nodes[n]["lat"]))
                    * METERS_PER_DEGREE <= 150), None)
            node_id = node_id or f"bus:{stop_id}"
            network.node(node_id, name, lon, lat, "bus")
            named_stops.setdefault(name, []).append(node_id)
            stop_nodes[stop_id] = node_id
    finally:
        db.close()
    for route_id, name, color in routes.values():
        network.route(f"bus:{route_id}", name, "bus", "#" + (color or "53b96b"))
    coordinates = {node_id: (node["lon"], node["lat"]) for node_id, node in network.nodes.items()}
    for (route_id, a, b), values in samples.items():
        u, v = stop_nodes.get(a), stop_nodes.get(b)
        if not u or not v:
            continue
        minutes = statistics.median(values) if values else None
        if minutes is None:
            points = coordinates.get(u), coordinates.get(v)
            if not all(points):
                continue
            minutes = distance(*points) * METERS_PER_DEGREE / (BUS_FALLBACK_KMH / 3.6) / 60
        network.edge(u, v, max(0.5, minutes), f"bus:{route_id}")


def _sample_legs(samples, used_stops, route, chain):
    for (a, a_departure, _), (b, _, b_arrival) in zip(chain, chain[1:]):
        used_stops.add(a)
        used_stops.add(b)
        key = (route, min(a, b), max(a, b))
        samples.setdefault(key, [])
        if a_departure is not None and b_arrival is not None and b_arrival > a_departure:
            samples.setdefault(key, []).append((b_arrival - a_departure) / 60)


def build_network(processed: Path, key: str = ""):
    network = Network()
    notes = []
    try:
        build_jr(network, processed)
    except (OSError, ValueError, KeyError):
        notes.append("JR: 駅データがありません（uv run transit import-static-railways）。")
    metro_note = build_metro(network, processed, key)
    if metro_note:
        notes.append(metro_note)
    bus_note = build_bus(network, processed)
    if bus_note:
        notes.append(bus_note)
    network.finalize()
    return network, notes


def _dijkstra_from(source: int, incident):
    """全ノードの source からの最短重み（無向グラフなので全ノード→source の下界）。"""
    infinite = float("inf")
    distances = [infinite] * len(incident)
    distances[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        current, node = heapq.heappop(queue)
        if current > distances[node]:
            continue
        for _edge_id, neighbour, minutes in incident[node]:
            candidate = current + minutes
            if candidate < distances[neighbour]:
                distances[neighbour] = candidate
                heapq.heappush(queue, (candidate, neighbour))
    return distances


def plan(network: Network, start: str, goal: str, budget_minutes: float,
         used_edges=(), allowed_routes=None, compute_seconds=DEFAULT_COMPUTE_SECONDS,
         clock=time.monotonic):
    """最大乗車時間のトレイル（駅再訪可）。静的所要時間モデル。

    最短経路を初期解にし、Dijkstraの帰着時間下界で枝刈りする時間制限DFS。
    全探索完了または予算上界到達ならoptimal、それ以外はtime_limit。
    同じ両端ノードの区間は路線・方向を問わず一度だけ使用できる。
    """
    budget = float(budget_minutes)
    limit = float(compute_seconds)
    if not math.isfinite(budget) or not 0 <= budget <= 1440:
        raise ValueError("budget must be finite and within 0..1440 minutes")
    if not math.isfinite(limit) or not 0 < limit <= 30:
        raise ValueError("compute_seconds must be within 0..30")
    if start not in network.nodes or goal not in network.nodes:
        raise ValueError("unknown node")
    if not isinstance(used_edges, (list, tuple, set)) or any(
        type(i) is not int or not 0 <= i < len(network.edges) for i in used_edges
    ):
        raise ValueError("unknown used edge")
    if allowed_routes is not None and (not isinstance(allowed_routes, (list, tuple, set))
            or any(not isinstance(r, str) or r not in network.routes for r in allowed_routes)):
        raise ValueError("unknown route")
    # 0.1分の整数演算。浮動小数点の誤差で締切ちょうどの解を落とさない。
    budget = math.floor(budget * 10 + 1e-8)
    deadline = clock() + limit
    result = {"status": "infeasible", "minutes": 0.0, "edges": [], "nodes": [], "explored": 0}
    allowed = None if allowed_routes is None else set(allowed_routes)
    # 同じ両端の区間は路線違いでも共通の資源。別系統で逆走する抜け道を塞ぐ。
    pairs = [tuple(sorted((e["u"], e["v"]))) for e in network.edges]
    resources = {pair: i for i, pair in enumerate(dict.fromkeys(pairs))}
    edge_resources = [resources[pair] for pair in pairs]
    used_pairs = {pairs[i] for i in used_edges}
    node_ids = list(network.nodes)
    index_of = {node_id: i for i, node_id in enumerate(node_ids)}
    incident = [[] for _ in node_ids]
    seen = set()
    for edge_id, edge in enumerate(network.edges):
        if pairs[edge_id] in used_pairs or (allowed is not None and edge["route"] not in allowed):
            continue
        u, v, weight = index_of[edge["u"]], index_of[edge["v"]], round(edge["minutes"] * 10)
        # フィルター後、同一区間・同時間は同じ探索状態に至るので代表路線だけ残す。
        # 時間が違う並行辺は締切への収まり方が異なるため、どちらも必要。
        equivalent = (resources[pairs[edge_id]], weight)
        if equivalent in seen:
            continue
        seen.add(equivalent)
        incident[u].append((edge_id, v, weight))
        incident[v].append((edge_id, u, weight))
    for neighbours in incident:
        neighbours.sort(key=lambda item: item[2], reverse=True)
    source, target = index_of[start], index_of[goal]
    lower = _dijkstra_from(target, incident)
    if lower[source] > budget:
        return result
    # 最短経路を初期実行可能解にする。探索打ち切りでも必ず帰着可能な候補を返す。
    best_path, best_nodes, node = [], [source], source
    while node != target:
        edge_id, neighbour, weight = next(item for item in incident[node]
            if item[2] + lower[item[1]] == lower[node])
        best_path.append(edge_id)
        best_nodes.append(neighbour)
        node = neighbour
    best_gain = lower[source]
    path, nodes = [], [source]
    # ponytail: DFSは指数時間。時間上限で暫定解を返す。大規模化時はビーム探索等へ。
    # 明示スタックで長い経路にも対応し、HTTPスレッド間の再帰上限変更を避ける。
    # DFSの現経路だけを保持するので巨大整数マスクの複製は不要。
    # 各区間の占有をpush/popと対にして更新する（並行辺も同じ使用権）。
    occupied = bytearray(len(resources))
    stack = [(source, 0, iter(incident[source]))]
    explored, status = 0, "optimal"
    while stack and best_gain < budget:
        if explored % 128 == 0 and clock() >= deadline:
            status = "time_limit"
            break
        node, elapsed, choices = stack[-1]
        choice = next(choices, None)
        if choice is None:
            stack.pop()
            if path:
                occupied[edge_resources[path.pop()]] = 0
                nodes.pop()
            continue
        explored += 1
        edge_id, neighbour, weight = choice
        resource = edge_resources[edge_id]
        if occupied[resource] or elapsed + weight + lower[neighbour] > budget:
            continue
        gain = elapsed + weight
        path.append(edge_id)
        nodes.append(neighbour)
        if neighbour == target and gain > best_gain:
            best_gain, best_path, best_nodes = gain, path.copy(), nodes.copy()
        occupied[resource] = 1
        stack.append((neighbour, gain, iter(incident[neighbour])))
    result.update(status=status, minutes=best_gain / 10, edges=best_path,
                  nodes=[node_ids[i] for i in best_nodes], explored=explored, compute_seconds=limit)
    return result


class Service:
    """/route 用のグラフ構築（初回リクエスト時・プロセス内キャッシュ）と計画API。"""

    def __init__(self, processed: Path, key: str, compute_seconds=DEFAULT_COMPUTE_SECONDS):
        self.processed, self.key = processed, key
        self.compute_seconds = float(compute_seconds)
        self._lock = threading.Lock()
        self._network = None
        self._notes = []

    def network_data(self):
        with self._lock:
            if self._network is None:
                self._network, self._notes = build_network(self.processed, self.key)
            return self._network, self._notes

    def get_network(self):
        network, notes = self.network_data()
        nodes = [{"id": node["id"], "name": node["name"], "lon": node["lon"], "lat": node["lat"],
                  "modes": sorted(node["modes"])} for node in network.nodes.values()]
        return {"notes": notes, "nodes": nodes, "edges": network.edges,
                "routes": list(network.routes.values())}

    def plan(self, body):
        network, notes = self.network_data()
        result = plan(
            network,
            str(body["start"]), str(body["goal"]), float(body["budget_minutes"]),
            used_edges=body.get("used", []),
            allowed_routes=body.get("allowed_routes"),
            compute_seconds=self.compute_seconds,
        )
        result["notes"] = notes
        return result
