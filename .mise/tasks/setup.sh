#!/usr/bin/env bash
# 環境構築一式: uv sync -> Overtureデータ取得(済みならスキップ) -> SQLite取込
# データを再取得したい場合は FORCE=1 mise run setup
set -euo pipefail

cd "$(dirname "$0")/../../nearby_places_api"

# シェルセッション開始後にmiseがuvを導入した場合の保険
if ! command -v uv >/dev/null 2>&1; then
  UV_DIR="$(mise where uv 2>/dev/null || true)"
  [ -n "$UV_DIR" ] && export PATH="$(cygpath "$UV_DIR" 2>/dev/null || echo "$UV_DIR"):$PATH"
fi

echo "== 1/3: dependencies (uv sync)"
uv sync

RELEASE="${OVERTURE_RELEASE:-2026-09-23.1}"
mkdir -p data/raw

if [ -f data/raw/tokyo-places-mainland.geojsonseq ] \
  && [ -f data/raw/tokyo-division-area.geojsonseq ] \
  && [ "${FORCE:-0}" != "1" ]; then
  echo "== 2/3: Overture data (already downloaded, release: $RELEASE)"
else
  echo "== 2/3: Overture data (release: $RELEASE)"
  dl() {
    echo ">> downloading $3 (bbox=$1, type=$2)"
    uvx "overturemaps" download --bbox="$1" -f geojsonseq --type="$2" -r "$RELEASE" -o "data/raw/$3"
  }
  # 境界: 東京駅付近の小さなbboxで、島しょ部を含む都全体の division_area が取れる
  dl "139.76,35.68,139.77,35.69" division_area tokyo-division-area.geojsonseq
  # 本土（23区・多摩地域）。他県の施設は取込時に境界で除外される
  dl "138.94,35.49,139.93,35.90" place tokyo-places-mainland.geojsonseq
  # 伊豆諸島 / 小笠原諸島
  dl "139.10,32.40,140.40,34.80" place tokyo-places-izu.geojsonseq
  dl "141.20,24.20,142.30,27.80" place tokyo-places-ogasawara.geojsonseq
fi

echo "== 3/3: import to places.sqlite"
uv run places import data/raw/tokyo-places-*.geojsonseq --boundary data/raw/tokyo-division-area.geojsonseq

echo
echo "done! Start the API with: mise run dev"
