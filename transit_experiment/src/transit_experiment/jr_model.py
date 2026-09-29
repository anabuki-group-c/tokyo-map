"""Explicit headway simulation for JR when an actual timetable is unavailable.

Station orders are manually curated. Headways, speed, dwell and service hours
are visualization assumptions, NOT operator schedules or measured train positions.
"""
from __future__ import annotations

import bisect
import json
import math
from datetime import datetime, timedelta

from transit_experiment.timetable import JST, RailGeometry, distance

# id, display name, color, assumed headway (minutes), N02 track names, station order
MODELS = [
    ("Yamanote", "山手線", "#80c241", 5, "山手線 東海道線 東北線", "東京 有楽町 新橋 浜松町 田町 高輪ゲートウェイ 品川 大崎 五反田 目黒 恵比寿 渋谷 原宿 代々木 新宿 新大久保 高田馬場 目白 池袋 大塚 巣鴨 駒込 田端 西日暮里 日暮里 鶯谷 上野 御徒町 秋葉原 神田 東京"),
    ("ChuoRapid", "中央線（東京〜高尾）", "#f15a22", 5, "中央線 東北線", "東京 神田 御茶ノ水 四ツ谷 新宿 中野 高円寺 阿佐ヶ谷 荻窪 西荻窪 吉祥寺 三鷹 武蔵境 東小金井 武蔵小金井 国分寺 西国分寺 国立 立川 日野 豊田 八王子 西八王子 高尾"),
    ("ChuoSobu", "中央・総武線（収録区間）", "#ffd400", 6, "中央線 総武線", "三鷹 吉祥寺 西荻窪 荻窪 阿佐ヶ谷 高円寺 中野 東中野 大久保 新宿 代々木 千駄ヶ谷 信濃町 四ツ谷 市ヶ谷 飯田橋 水道橋 御茶ノ水 秋葉原 浅草橋 両国 錦糸町 亀戸 平井 新小岩 小岩 市川 本八幡 下総中山 西船橋 船橋"),
    ("KeihinTohoku", "京浜東北線（大宮〜鶴見）", "#00b2e5", 6, "東北線 東海道線", "大宮 さいたま新都心 与野 北浦和 浦和 南浦和 蕨 西川口 川口 赤羽 東十条 王子 上中里 田端 西日暮里 日暮里 鶯谷 上野 御徒町 秋葉原 神田 東京 有楽町 新橋 浜松町 田町 高輪ゲートウェイ 品川 大井町 大森 蒲田 川崎 鶴見"),
    ("Saikyo", "埼京線（大崎〜大宮）", "#00ac9a", 10, "山手線 赤羽線 東北線", "大崎 恵比寿 渋谷 新宿 池袋 板橋 十条 赤羽 北赤羽 浮間舟渡 戸田公園 戸田 北戸田 武蔵浦和 中浦和 南与野 与野本町 北与野 大宮"),
    ("Joban", "常磐線（上野〜柏）", "#36b378", 10, "常磐線 東北線", "上野 日暮里 三河島 南千住 北千住 松戸 柏"),
    ("Keiyo", "京葉線（東京〜南船橋）", "#d71954", 10, "京葉線", "東京 八丁堀 越中島 潮見 新木場 葛西臨海公園 舞浜 新浦安 市川塩浜 二俣新町 南船橋"),
    ("Nambu", "南武線", "#ffd400", 10, "南武線", "川崎 尻手 矢向 鹿島田 平間 向河原 武蔵小杉 武蔵中原 武蔵新城 武蔵溝ノ口 津田山 久地 宿河原 登戸 中野島 稲田堤 矢野口 稲城長沼 南多摩 府中本町 分倍河原 西府 谷保 矢川 西国立 立川"),
    ("Yokohama", "横浜線（収録区間）", "#80c241", 10, "横浜線", "菊名 新横浜 小机 鴨居 中山 十日市場 長津田 成瀬 町田 古淵 淵野辺 矢部 相模原 橋本 相原 八王子みなみ野 片倉 八王子"),
    ("Musashino", "武蔵野線", "#f15a22", 10, "武蔵野線", "府中本町 北府中 西国分寺 新小平 新秋津 東所沢 新座 北朝霞 西浦和 武蔵浦和 南浦和 東浦和 東川口 南越谷 越谷レイクタウン 吉川 吉川美南 新三郷 三郷 南流山 新松戸 新八柱 東松戸 市川大野 船橋法典 西船橋"),
    ("Ome", "青梅線", "#f15a22", 15, "青梅線", "立川 西立川 東中神 中神 昭島 拝島 牛浜 福生 羽村 小作 河辺 東青梅 青梅 宮ノ平 日向和田 石神前 二俣尾 軍畑 沢井 御嶽 川井 古里 鳩ノ巣 白丸 奥多摩"),
    ("Itsukaichi", "五日市線", "#f15a22", 15, "五日市線", "拝島 熊川 東秋留 秋川 武蔵引田 武蔵増戸 武蔵五日市"),
]
ASSUMPTIONS = {"speed_kmh": 45, "dwell_seconds": 30, "start_hour": 5, "end_hour": 25}
MESSAGE = "簡易推定：仮定した運転間隔・速度に基づくモデルです。実時刻表・実位置・実際の本数ではありません。遅延は無視しています。"


class JRModel:
    def __init__(self, processed, models=MODELS):
        records = json.loads((processed / "jr-east-stations.geojson").read_text())["features"]
        candidates = {}
        for feature in records:
            properties, geometry = feature["properties"], feature["geometry"]
            if geometry["type"] == "LineString" and geometry["coordinates"]:
                first, last = geometry["coordinates"][0], geometry["coordinates"][-1]
                point = ((first[0] + last[0]) / 2, (first[1] + last[1]) / 2)
            elif geometry["type"] == "Point":
                point = tuple(geometry["coordinates"][:2])
            else:
                continue
            candidates.setdefault(properties.get("N02_005"), []).append((properties.get("N02_003"), point))
        names = {name: name for model in models for name in model[4].split()}
        self.geometry = RailGeometry(processed / "jr-east-railroads.geojson", names)
        self.routes, self.services, self.features, self.stations, self.omitted = [], [], [], {}, []
        for route, title, color, headway, tracks, order in models:
            allowed = tracks.split()
            station_names = order.split()
            if any(name not in candidates for name in station_names):
                self.omitted.append(route)
                continue  # Never silently connect across a missing station.
            points = [next((p for track, p in candidates[name] if track in allowed), candidates[name][0][1]) for name in station_names]
            graph = {}
            for track in allowed:
                for node, neighbors in self.geometry.graphs.get(track, {}).items():
                    graph.setdefault(node, {}).update(neighbors)
            self.geometry.names[route] = route
            self.geometry.graphs[route] = graph
            lines = []
            for a, b in zip(points, points[1:]):
                self.prepare_path(route, a, b)
                lines.append(self.geometry.paths[(route, a, b)][0])
            self.features.append({"type": "Feature", "properties": {"route": route}, "geometry": {"type": "MultiLineString", "coordinates": lines}})
            self.routes.append({"id": route, "name": title, "color": color, "headway_minutes": headway})
            for name, point in zip(station_names, points):
                station = self.stations.setdefault((name, point), {"id": f"{name}:{point}", "name": name, "lon": point[0], "lat": point[1], "routes": []})
                if route not in station["routes"]:
                    station["routes"].append(route)
            for direction in (0, 1):
                chain = list(zip(station_names, points))
                if direction:
                    chain.reverse()
                legs, ends, total = [], [], 0.0
                for (origin, a), (target, b) in zip(chain, chain[1:]):
                    self.prepare_path(route, a, b)
                    _, _, length, _ = self.geometry.paths[(route, a, b)]
                    # Degrees-to-metres approximation; running time excludes dwell.
                    run = max(30, length * 111_000 / (ASSUMPTIONS["speed_kmh"] / 3.6))
                    legs.append((origin, target, a, b, total, run))
                    total += ASSUMPTIONS["dwell_seconds"] + run
                    ends.append(total)
                self.services.append((route, direction, headway * 60, legs, ends, total))

    def prepare_path(self, route, a, b):
        self.geometry.position(route, a, b, 0)
        key = (route, a, b)
        path = self.geometry.paths[key]
        direct = distance(a, b)
        # N02 parallel tracks can snap to disconnected platforms and produce huge
        # detours. Do not interpret those as a plausible station-to-station journey.
        if path[2] > max(direct * 3, direct + .02):
            self.geometry.paths[key] = [a, b], [direct], direct, True

    def network(self):
        return {"routes": self.routes, "stations": list(self.stations.values()),
                "geojson": {"type": "FeatureCollection", "features": self.features},
                "ready": True, "basis": "headway_model", "message": MESSAGE, "assumptions": ASSUMPTIONS,
                "omitted_routes": self.omitted, "source": "国土数値情報 N02 (2022年) / JR東日本の駅・線形を使った簡易モデル"}

    def positions(self, now=None):
        now = (now or datetime.now(JST)).astimezone(JST)
        day = (now - timedelta(hours=3)).date()
        midnight = datetime.combine(day, datetime.min.time(), JST)
        elapsed = (now - midnight).total_seconds() - ASSUMPTIONS["start_hour"] * 3600
        operating_seconds = (ASSUMPTIONS["end_hour"] - ASSUMPTIONS["start_hour"]) * 3600
        pins = []
        if 0 <= elapsed < operating_seconds:
            for route, direction, interval, legs, ends, duration in self.services:
                for departure in range(max(0, math.floor((elapsed - duration) / interval) + 1), math.floor(elapsed / interval) + 1):
                    age = elapsed - departure * interval
                    index = bisect.bisect_right(ends, age)
                    if index >= len(legs):
                        continue
                    origin, target, a, b, start, run = legs[index]
                    fraction = max(0, min(1, (age - start - ASSUMPTIONS["dwell_seconds"]) / run))
                    point, approximate = self.geometry.position(route, a, b, fraction)
                    bearing = self.geometry.heading(route, a, b, fraction)
                    pins.append({"id": f"model:{day}:{route}:{direction}:{departure}", "number": f"モデル{direction + 1}-{departure + 1}",
                                 "route": route, "headsign": target + "方面", "from": origin, "to": target,
                                 "lon": point[0], "lat": point[1], "heading": bearing, "geometry_approximate": approximate,
                                 "basis": "headway_model", "headway_minutes": interval / 60})
        return {"pins": pins, "generated_at": now.isoformat(), "ready": True, "basis": "headway_model",
                "message": MESSAGE, "assumptions": ASSUMPTIONS, "omitted_routes": self.omitted}
