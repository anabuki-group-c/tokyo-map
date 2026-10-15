"""最長経路プランナーのベンチマークハーネス。

代表シナリオ（始点・終点・予算・フィルターの組合せ）で
「計算時間 × 解の乗車時間 × 探索ノード数」を測る。

使い方:
    cd transit_experiment
    PYTHONPATH=src uv run python benchmarks/bench_longpath.py [--seconds 3.0] [--json out.json]

ponytail: シナリオは手選択の代表駅ペア。網羅ベンチではない。
昇格路径: 駅対をランダムサンプリングして分布で比較する。
"""
from __future__ import annotations

import argparse
import cProfile
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transit_experiment.longpath import build_network  # noqa: E402

SCENARIOS = [
    # (名前, 始点, 終点, 予算分, allowed_routes(None=全線))
    ("新宿→東京 120分 全線", "新宿", "東京", 120, None),
    ("新宿→東京 180分 全線", "新宿", "東京", 180, None),
    ("渋谷→上野 120分 全線", "渋谷", "上野", 120, None),
    ("池袋→品川 180分 全線", "池袋", "品川", 180, None),
    ("東京→両国 240分 全線", "東京", "両国", 240, None),
    ("浅草→新宿 240分 全線", "浅草", "新宿", 240, None),
    ("新宿→新宿 120分 一周", "新宿", "新宿", 120, None),
    ("渋谷→上野 120分 鉄道のみ", "渋谷", "上野", 120, "rail"),
    ("東京→両国 240分 バスのみ", "東京", "両国", 240, "bus"),
]


def resolve(network, name: str):
    """駅名→ノードID（鉄道駅を優先、なければ部分一致）。"""
    exact = f"station:{name}"
    if exact in network.nodes:
        return exact
    for node_id, node in network.nodes.items():
        if node["name"] == name:
            return node_id
    for node_id, node in network.nodes.items():
        if name in node["name"]:
            return node_id
    raise KeyError(name)


def route_mode_filter(network, mode: str):
    return sorted(rid for rid, r in network.routes.items() if r["mode"] == mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--json", type=str, default="")
    parser.add_argument("--repeat", type=int, default=1, help="各シナリオの反復数（中央値を報告）")
    parser.add_argument("--profile", default="", help="探索のみのcProfile出力先")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    profiler = cProfile.Profile() if args.profile else None

    processed = Path(__file__).resolve().parents[1] / "data" / "processed"
    import os
    key = os.environ.get("ODPT_CONSUMER_KEY", "")
    started = time.monotonic()
    network, notes = build_network(processed, key)
    print(f"グラフ構築: {time.monotonic() - started:.2f}s  "
          f"nodes={len(network.nodes)} edges={len(network.edges)} notes={notes}")

    rail_routes = [rid for rid, r in network.routes.items() if r["mode"] == "rail"]
    bus_routes = [rid for rid, r in network.routes.items() if r["mode"] == "bus"]

    rows = []
    for name, start, goal, budget, filter_kind in SCENARIOS:
        if filter_kind == "rail":
            allowed = rail_routes
        elif filter_kind == "bus":
            allowed = bus_routes
        else:
            allowed = None
        runs = []
        for _ in range(args.repeat):
            started = time.monotonic()
            if profiler:
                profiler.enable()
            result = plan_safe(network, resolve(network, start), resolve(network, goal),
                               budget, allowed, args.seconds)
            if profiler:
                profiler.disable()
            runs.append((time.monotonic() - started, result))
            if result["nodes"]:
                assert result["nodes"][0] == resolve(network, start)
                assert result["nodes"][-1] == resolve(network, goal)
                assert result["minutes"] <= budget
                assert len(result["nodes"]) == len(result["edges"]) + 1
                sections, total = set(), 0
                for i, edge_id in enumerate(result["edges"]):
                    edge = network.edges[edge_id]
                    section = tuple(sorted((edge["u"], edge["v"])))
                    assert section not in sections
                    assert section == tuple(sorted(result["nodes"][i:i + 2]))
                    assert allowed is None or edge["route"] in allowed
                    sections.add(section)
                    total += round(edge["minutes"] * 10)
                assert total == round(result["minutes"] * 10)
        elapsed = statistics.median(wall for wall, _ in runs)
        # 中央値の質に対応する実在の結果を表示する（架空の経路を合成しない）。
        result = sorted((r for _, r in runs), key=lambda r: r["minutes"])[len(runs) // 2]
        rows.append({
            "scenario": name, "start": start, "goal": goal, "budget": budget,
            "status": result["status"], "minutes": result["minutes"],
            "budget_ratio": round(result["minutes"] / budget, 3) if result["minutes"] else 0.0,
            "wall_seconds": round(elapsed, 4), "explored": result.get("explored", 0),
            "runs": [{"minutes": r["minutes"], "wall_seconds": round(w, 4),
                      "explored": r.get("explored", 0), "status": r["status"]} for w, r in runs],
        })
        print(f"{name:30s} {result['status']:11s} "
              f"{result['minutes']:7.1f}分 ({result['minutes'] / budget * 100:5.1f}%) "
              f"wall={elapsed:6.2f}s explored={result.get('explored', 0)}")

    if profiler:
        profiler.dump_stats(args.profile)
        import pstats
        pstats.Stats(profiler).strip_dirs().sort_stats("tottime").print_stats(15)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {args.json}")


def plan_safe(network, start, goal, budget, allowed, seconds):
    from transit_experiment.longpath import plan
    return plan(network, start, goal, budget, allowed_routes=allowed, compute_seconds=seconds)


if __name__ == "__main__":
    main()
