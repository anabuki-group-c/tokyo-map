# 鉄道データ

## 1. 路線の地理形状・駅位置: 国土数値情報 N02

JR東日本・東京メトロを含む全国の鉄道 GIS データです。地図上で路線を描画する基礎データとして利用します。

- データ詳細: <https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N02-v3_1.html>
- 最新掲載ファイル（令和4年、全国、GML）: <https://nlftp.mlit.go.jp/ksj/gml/data/N02/N02-22/N02-22_GML.zip>
- 形式: GML（ダウンロードファイルには SHP 形式も含まれる）

東京都の範囲、または運営会社で絞り込んで GeoJSON に変換する。

**取得できるデータ**

- 鉄道区間の線形座標（地図に描く路線）
- 駅の位置・駅名・駅コード
- 路線名、運営会社、鉄道区分、事業者種別

### N02 の主要カラム

| カラム | 内容 | 利用先 |
| --- | --- | --- |
| `N02_001` | 鉄道区分 | 種別の判別 |
| `N02_002` | 事業者種別 | JR・公営・民営等の判別 |
| `N02_003` | 路線名 | 路線の表示名 |
| `N02_004` | 運営会社 | JR東日本・東京メトロの絞り込み |
| `N02_005` | 駅名 | 駅名表示 |
| `N02_005c` | 駅コード | 駅の識別子として利用可能 |
| `N02_005g` | グループコード | 同一駅グループの識別 |
| `geometry` | 路線区間（LineString）または駅（Point）の座標 | 地図描画 |

> 駅は路線の一部として収録される。同じ駅が複数路線に存在するため、駅単位の表示では `N02_005g` を用いて統合を検討する。

## 2. 東京メトロ: ODPT

路線名、駅、駅順、時刻表などの最新マスタに使用します。ODPT アカウントとアクセストークンが必要です。

- 路線カタログ: <https://ckan.odpt.org/dataset/r_route-tokyometro>
- 駅カタログ: <https://ckan.odpt.org/dataset/r_station-tokyometro>
- GTFS: <https://ckan.odpt.org/dataset/train-tokyometro>

```text
GET https://api.odpt.org/api/v4/odpt:Railway?odpt:operator=odpt.Operator:TokyoMetro&acl:consumerKey=YOUR_ACCESS_TOKEN
GET https://api.odpt.org/api/v4/odpt:Station?odpt:operator=odpt.Operator:TokyoMetro&acl:consumerKey=YOUR_ACCESS_TOKEN
```

**取得できるデータ**

- 路線 ID・路線名・運営事業者・駅順
- 駅 ID・駅名・路線・駅ナンバリング・座標（提供時）
- GTFS では、路線・駅・列車便・時刻表・運行日・経路形状（提供時）
- カタログにある別データセットでは、運行情報、列車時刻表、駅時刻表、運賃、乗降者数も取得可能

### `odpt:Railway` の主要プロパティ

| プロパティ | 内容 |
| --- | --- |
| `owl:sameAs` | 路線 ID（例: `odpt.Railway:TokyoMetro.Ginza`） |
| `dc:title` | 路線名 |
| `odpt:operator` | 事業者 ID |
| `odpt:stationOrder` | 駅順の配列 |
| `odpt:stationOrder[].odpt:station` | 駅 ID |
| `odpt:stationOrder[].odpt:index` | 路線内の駅順 |
| `odpt:stationOrder[].odpt:stationTitle` | 駅名（日英など） |
| `odpt:railwayTitle` | 路線名の多言語表記 |

### `odpt:Station` の主要プロパティ

| プロパティ | 内容 |
| --- | --- |
| `owl:sameAs` | 駅 ID |
| `dc:title` | 駅名 |
| `odpt:operator` | 事業者 ID |
| `odpt:railway` | 所属路線 ID |
| `odpt:stationCode` | 駅ナンバリング／駅コード |
| `geo:lat` / `geo:long` | 緯度・経度（提供時） |
| `odpt:stationTitle` | 駅名の多言語表記 |

## 3. JR東日本

- 路線形状・駅位置の本番データには **N02 を利用する**。
- ODPT の JR東日本データは関東エリアの一部が対象。
- 一部は「公共交通オープンデータチャレンジ限定ライセンス」のため、本番利用前に規約を必ず確認する。

- 路線カタログ: <https://ckan.odpt.org/dataset/jreast__r_route>
- 駅カタログ: <https://ckan.odpt.org/dataset/jreast__r_station>
- GTFS カタログ: <https://ckan.odpt.org/dataset/jreast_tokyo_area>
