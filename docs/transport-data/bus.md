# 路線バスデータ

路線・バス停・経路形状には、ODPT が提供する **GTFS/GTFS-JP** を優先して使用します。

## 対象データセット

| 事業者 | GTFS/GTFS-JP | ライセンス |
| --- | --- | --- |
| 都営バス（東京都交通局） | <https://ckan.odpt.org/dataset/b_bus_gtfs_jp-toei> | CC BY 4.0 |
| 京王バス | <https://ckan.odpt.org/dataset/keio_bus_all_lines> | 公共交通オープンデータ基本ライセンス |
| 西武バス | <https://ckan.odpt.org/dataset/seibu_bus__b-bus_gtfs> | 公共交通オープンデータ基本ライセンス |
| 小田急バス | <https://ckan.odpt.org/dataset/odakyu_bus_aii_lines> | 公共交通オープンデータ基本ライセンス |
| 関東バス | <https://ckan.odpt.org/dataset/kanto_bus_all_lines> | 公共交通オープンデータ基本ライセンス |

各データセットのダウンロードには ODPT アカウントとアクセストークンが必要です。

**GTFS/GTFS-JP で取得できるデータ**

- 事業者、系統番号・系統名、表示色
- バス停 ID・名称・緯度経度・乗り場情報
- 行先別の運行便、往復方向、バス停の通過順
- 平日／休日などの運行日、発着時刻
- 路線の経路形状（`shapes.txt` が提供される場合）

なお、提供対象・カラムは事業者ごとに異なる。GTFS-Realtime を提供する事業者では、別途、車両位置・到着予測・運行アラートを取得できる場合がある。

## GTFS/GTFS-JP のカラム

### `routes.txt` — 路線・系統

| カラム | 内容 |
| --- | --- |
| `route_id` | 路線の一意 ID |
| `agency_id` | 事業者 ID |
| `route_short_name` | 系統番号などの短い表示名 |
| `route_long_name` | 路線・系統の正式名称 |
| `route_desc` | 補足説明 |
| `route_type` | 交通種別（バスは通常 `3`） |
| `route_color` | 路線表示色 |
| `route_text_color` | 文字表示色 |

### `stops.txt` — バス停

| カラム | 内容 |
| --- | --- |
| `stop_id` | バス停の一意 ID |
| `stop_name` | バス停名 |
| `stop_lat` | 緯度 |
| `stop_lon` | 経度 |
| `location_type` | 停留所種別 |
| `parent_station` | 親停留所 ID |
| `platform_code` | 乗り場番号・標識番号など |

### `trips.txt` — 路線の運行便

| カラム | 内容 |
| --- | --- |
| `route_id` | `routes.txt` の路線 ID |
| `service_id` | 運行日カレンダー ID |
| `trip_id` | 運行便の一意 ID |
| `trip_headsign` | 行先表示 |
| `direction_id` | 方向（往復の区別） |
| `shape_id` | `shapes.txt` の経路形状 ID |

### `stop_times.txt` — 停車順・時刻

| カラム | 内容 |
| --- | --- |
| `trip_id` | `trips.txt` の運行便 ID |
| `arrival_time` | 到着時刻 |
| `departure_time` | 発車時刻 |
| `stop_id` | `stops.txt` のバス停 ID |
| `stop_sequence` | 停車順 |
| `pickup_type` | 乗車可否 |
| `drop_off_type` | 降車可否 |

### `shapes.txt` — 経路形状

| カラム | 内容 |
| --- | --- |
| `shape_id` | 経路形状の一意 ID。`trips.shape_id` と結合する |
| `shape_pt_lat` | 経路点の緯度 |
| `shape_pt_lon` | 経路点の経度 |
| `shape_pt_sequence` | 経路点の並び順 |
| `shape_dist_traveled` | 起点からの累積距離（提供時） |

> `shapes.txt` が提供されない場合は、バス停の座標を `stop_sequence` 順に結んだ近似線となる。道路に沿った正確な線形は保証されない。

### 運行日

| ファイル | 主なカラム | 内容 |
| --- | --- | --- |
| `calendar.txt` | `service_id`, `monday`〜`sunday`, `start_date`, `end_date` | 曜日別の基本運行日 |
| `calendar_dates.txt` | `service_id`, `date`, `exception_type` | 祝日・臨時運行などの例外日 |

## 都営バスの個別 API

GTFS のほか JSON API も利用可能です。取得対象は、路線パターン、バス停、時刻表、運賃、バスロケーション、GTFS-Realtime の運行情報です。

- 路線: <https://ckan.odpt.org/dataset/b_busroute-toei>
- バス停: <https://ckan.odpt.org/dataset/b_busstop-toei>
- リアルタイム: <https://ckan.odpt.org/dataset/b_bus_gtfs_rt-toei>

```text
GET https://api.odpt.org/api/v4/odpt:BusroutePattern?odpt:operator=odpt.Operator:Toei&acl:consumerKey=YOUR_ACCESS_TOKEN
GET https://api.odpt.org/api/v4/odpt:BusstopPole?odpt:operator=odpt.Operator:Toei&acl:consumerKey=YOUR_ACCESS_TOKEN
```

## ライセンス

- 都営バス（CC BY 4.0）は、提供者名・データ名・ライセンスのクレジットを表示する。
- その他の事業者は、カタログに記載の個別ライセンス・利用規約を確認してから公開利用する。
