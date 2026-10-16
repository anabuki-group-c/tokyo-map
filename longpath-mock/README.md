# longpath-mock — 最長乗車経路実験（/route）

東京マップ（`../transit_experiment`）から **mock として分離した別用途プロジェクト**。
「乗車時間合計を最大化する経路」を探索する実験だけを含みます。マップ表示・時刻表・JR/バスの
列車位置推定など本体機能は含みません。

## 起動

```bash
cd longpath-mock
uv sync
uv run route serve --host 127.0.0.1 --port 8000
```

<http://localhost:8000/route> を開きます。`.env` に `ODPT_CONSUMER_KEY` があれば東京メトロも含まれます
（`transit_experiment/.env` からコピー済み。キーなしでも保存キャッシュで動作可）。

## 構成

本体から切り出したもの:

- `src/longpath_mock/longpath.py` — グラフ構築（JR 12系統・メトロ・都営バス）と最長経路探索
- `src/longpath_mock/jr_model.py` — JR の駅順・速度仮定（`MODELS` / `ASSUMPTIONS` のみ。表示用クラスは削除）
- `src/longpath_mock/web.py` — `/api/route/plan`、`/api/route/network`、静的配信のみの最小サーバー
- `static/route.html` `route.js`、`tests/`（探索アルゴリズムとUI状態のセルフチェック）
- `data/processed/` — `jr-east-stations.geojson`、`bus-gtfs.sqlite`、メトロキャッシュのコピー

モデル・制約・既知の限界は本体 README の「最長経路実験（/route）」の節を参照してください
（0.1分単位の固定所要時間モデル、乗換0分、NP-hard の時間打ち切り探索など、仕様は同一です）。

## セルフチェック

```bash
uv run python -m unittest discover -s tests -v
node tests/test_route.cjs
```
