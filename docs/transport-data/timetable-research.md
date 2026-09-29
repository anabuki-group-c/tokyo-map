# 実時刻表の取得・適用状況

調査日: 2026-09-29

## 東京メトロ：適用済み

- 出典: 公共交通オープンデータセンター `odpt:TrainTimetable`
  - https://api.odpt.org/api/v4/odpt:TrainTimetable
  - https://ckan.odpt.org/dataset/train-tokyometro
- ローカルの成功キャッシュを確認: 平日ダイヤ9路線＋丸ノ内支線、計5,567件。
- 適用方法: 列車ごとの各駅の発着時刻、平日／土休日、深夜の日付を参照し、該当区間の経過時間で位置を補間する。固定の運転間隔・速度モデルではない。
- 画面の列車ポップアップに、その区間の発車予定時刻と到着予定時刻（停車中は到着・発車時刻）、データ更新日時を表示する。
- ODPTの時刻表に含まれない秒単位の動き・加減速は再現できない。実時刻表を使用していても矢印の位置は実測ではない。

## JR東日本：公式情報は確認、実時刻表の自動適用は権限待ち

### 公式Web時刻表

- トップ: https://timetables.jreast.co.jp/
- 東京駅: https://timetables.jreast.co.jp/timetable/list1039.html
- 新宿駅: https://timetables.jreast.co.jp/timetable/list0866.html
- サイトの注意書きを生成する公式スクリプト: https://timetables.jreast.co.jp/announce.js

公式駅時刻表・列車別時刻表・デジタル時刻表が閲覧できることを確認した。ただし、注意書きには時刻データの無断転載・複写・加工を禁じる旨がある。利用許諾の確認なくWebページを一括取り込みし、アプリ用データとして配信する実装は行っていない。

また、調査時の駅ページは `2610/` の2026年10月号に基づくデジタル時刻表をリンクしていた。閲覧可能な最新ページを、そのまま9月29日の運行データとみなして適用してはいけない。

### 機械利用向けGTFS

- カタログ: https://ckan.odpt.org/dataset/jreast_tokyo_area
- リソース: https://ckan.odpt.org/dataset/jreast_tokyo_area/resource/a6f842e9-e053-4be5-a926-87d0b49753d3
- カタログ記載の配信先（キー省略）:
  https://api-challenge.odpt.org/api/v4/files/JR-East/data/JR-East-Train-GTFS.zip
- 認証指定: `Access_Token_for_Challenge2026`。通常キーでは前回403を確認済み。今回もカタログがチャレンジ用トークンを指定していることを確認した。認証回避や別ホスト経由の取得はしない。

### 適用するための方法

1. 利用資格・条件を確認したチャレンジ用キーを `ODPT_CHALLENGE_KEY` に設定して再起動する。
2. または、利用権限のあるJR GTFS ZIPを入手し、`uv run transit import-jr-gtfs /path/to/feed.zip` でインポートして再起動する。

GTFSが読み込めた場合は簡易モデルより優先する。未取得の間は「簡易推定」として仮定の間隔・速度によるモデルを維持し、「実時刻表適用済み」とは表示しない。
