'use strict';
let transportMode = 'metro';
const extraNetworks = new Map();
let extraLayers = null, extraPins = null, extraRenderer = null, ExtraArrow = null;
let extraBounds = null, extraSelection = '', extraRequest = 0, extraController = null, extraTimer = null;
let extraNetwork = null, extraStations = null;
function drawExtraStations() {
  extraStations?.clearLayers();
  if (!['jr', 'all'].includes(transportMode) || !extraStations) return;
  for (const station of extraNetwork?.stations || []) {
    if (extraSelection && !station.routes.includes(extraSelection)) continue;
    const marker = L.circleMarker([station.lat, station.lon], {
      radius: 4, weight: 1.5, color: '#d8efce', fillColor: '#203729', fillOpacity: 1,
    });
    const label = document.createElement('span'); label.textContent = station.name;
    const popup = document.createElement('div');
    popup.textContent = `${station.name}駅 / ${station.routes.join('・')} — 駅の位置です。列車の現在位置ではありません。`;
    marker.bindTooltip(label).bindPopup(popup).addTo(extraStations);
  }
}
const modeNames = {metro: '東京メトロ', jr: 'JR東日本', bus: '都営バス', all: 'すべて（メトロ・JR・都営バス）'};
function extraStatus(text) { document.getElementById('extra-status').textContent = text; }
function clearExtra() {
  if (extraLayers) { map.removeLayer(extraLayers); extraLayers = null; }
  if (extraPins) extraPins.clearLayers();
  extraStations?.clearLayers();
}
function styleExtraRoutes() {
  drawExtraStations();
  if (extraLayers) extraLayers.eachLayer(layer => {
    const visible = !extraSelection || layer.feature.properties.route === extraSelection;
    layer.setStyle({opacity: visible ? 0.7 : 0.07, weight: extraSelection && visible ? 4 : 2});
  });
  const route = extraNetwork?.routes.find(r => r.id === extraSelection);
  document.getElementById('selection').textContent = `${modeNames[transportMode]} / ${route?.name || '全路線'}`;
}
function drawExtraNetwork(network) {
  clearExtra(); extraNetwork = network;
  const lookup = new Map(network.routes.map(route => [route.id, route]));
  const select = document.getElementById('extra-route');
  select.replaceChildren(new Option('全路線', ''));
  for (const route of network.routes) select.add(new Option(route.name, route.id));
  if (!lookup.has(extraSelection)) extraSelection = '';
  select.value = extraSelection;
  extraLayers = L.geoJSON(network.geojson, {
    style: feature => ({color: safeExtraColor(lookup.get(feature.properties.route)?.color), weight: 2, opacity: 0.7, smoothFactor: 2}),
    onEachFeature: (feature, layer) => {
      const route = lookup.get(feature.properties.route);
      const label = document.createElement('span'); label.textContent = route?.name || '';
      layer.bindTooltip(label);
      layer.on('click', () => { extraSelection = feature.properties.route; select.value = extraSelection; styleExtraRoutes(); requestExtra(); });
    },
  }).addTo(map);
  extraBounds = extraLayers.getBounds();
  document.getElementById('extra-source').textContent = network.source || '';
  styleExtraRoutes();
}
function safeExtraColor(color) { return /^#[0-9a-f]{6}$/i.test(color || '') ? color : '#53b96b'; }
async function switchTransport() {
  transportMode = document.getElementById('transport-mode').value;
  extraSelection = '';
  extraRequest++; extraController?.abort(); clearTimeout(extraTimer);
  clearExtra();
  document.getElementById('estimate-badge').hidden = true;
  document.getElementById('metro-panel').hidden = !['metro', 'all'].includes(transportMode);
  document.getElementById('extra-panel').hidden = transportMode === 'metro';
  document.getElementById('motion').hidden = transportMode !== 'metro';
  for (const {layer} of layers) {
    if (['metro', 'all'].includes(transportMode)) layer.addTo(map);
    else map.removeLayer(layer);
  }
  updateFlow();
  if (trainGroup) trainGroup.clearLayers();
  if (transportMode === 'metro') {
    document.getElementById('selection').textContent = selected ? routes.find(r => r.id === selected).name : '東京メトロ / 全路線';
    renderTrainPins();
    return;
  }
  if (transportMode === 'all') {
    selected = null; select(null);
    renderTrainPins(); showAllBadge();
  }
  document.getElementById('selection').textContent = `${modeNames[transportMode]} / 読み込み中`;
  extraStatus('路線と時刻表を読み込み中…');
  requestExtra();
}
async function requestExtra() {
  clearTimeout(extraTimer);
  if (transportMode === 'metro' || document.hidden) return;
  const mode = transportMode, selection = extraSelection, request = ++extraRequest;
  extraController?.abort();
  const controller = new AbortController(); extraController = controller;
  const timeout = setTimeout(() => controller.abort(), 90000);
  try {
    const network = await fetchExtraNetwork(mode, controller.signal);
    if (request !== extraRequest || mode !== transportMode) return;
    if (extraNetwork !== network || !extraLayers) drawExtraNetwork(network);
    if (!network.ready) {
      extraStatus((network.message || '時刻表を取得中です。') + (network.stations?.length ? ` 駅ピン ${network.stations.length}駅（小さい丸は駅、列車ではありません）。` : ''));
      document.getElementById('estimate-badge').hidden = true;
      return;
    }
    const bounds = map.getBounds();
    const west = Math.max(-180, bounds.getWest()), east = Math.min(180, bounds.getEast());
    const coordinates = [west, Math.max(-90, bounds.getSouth()), east, Math.min(90, bounds.getNorth())].join(',');
    const result = await fetchExtraPositions(mode, selection, coordinates, controller.signal);
    if (request !== extraRequest || mode !== transportMode) return;
    extraPins.clearLayers();
    const stamp = Date.parse(result.generated_at);
    const stale = !Number.isFinite(stamp) || Date.now() - stamp > 180000;
    const lookup = new Map(network.routes.map(route => [route.id, route]));
    let shown = 0;
    const modelBased = result.basis === 'headway_model';
    if (!stale && document.getElementById('extra-show-pins').checked) for (const pin of result.pins) {
      const modelBased = pin.basis === 'headway_model' || result.basis === 'headway_model';
      const assumptions = pin.assumptions || result.assumptions;
      const route = lookup.get(pin.route);
      const marker = new ExtraArrow([pin.lat, pin.lon], {renderer: extraRenderer, radius: 11, weight: 1.5,
        heading: pin.heading, color: '#c2cbd0', fillColor: safeExtraColor(route?.color), fillOpacity: 0.95});
      const text = document.createElement('div'); text.style.whiteSpace = 'pre-line';
      text.textContent = `${route?.name || pin.route} / ${pin.headsign || ''}\n` +
        (modelBased ? '【簡易推定・実時刻表は未使用】\n' : '【GTFS時刻表による推定】\n') +
        `${pin.from} → ${pin.to}\n${modelBased ? 'モデルID' : '便'}: ${pin.number}\n` +
        (modelBased ? `仮定: ${pin.headway_minutes}分間隔 / 走行${assumptions.speed_kmh}km/h / 停車${assumptions.dwell_seconds}秒\n5時〜翌1時の等間隔運転モデル。実際の位置・本数・始終発とは異なります。遅延は無視。\n` : '遅延・運休・道路渋滞は未反映です。\n') + (pin.geometry_approximate ? '停留所・駅間の直線による近似です。\n' : '') +
        `計算: ${new Date(result.generated_at).toLocaleTimeString('ja-JP', {timeZone: 'Asia/Tokyo'})} JST`;
      marker.bindPopup(text).addTo(extraPins); shown++;
    }
    extraStatus(`${mode === 'all' ? 'JR・都営バス' : modeNames[mode]} ${network.routes.length}路線 / 画面内の推定 ${shown}台・本。${result.message || ''}` +
      (result.limited ? ' 描画上限600件です。拡大または路線を絞ってください。' : '') +
      (result.warning ? ` ${result.warning}` : '') + (network.warning ? ` ${network.warning}` : '') + (stale ? ' 古い位置情報は非表示にしています。' : ''));
    const badge = document.getElementById('estimate-badge'); badge.hidden = shown === 0;
    badge.textContent = modelBased ? 'JR 簡易推定 · 仮の間隔・速度 / 実時刻表は未使用 / 遅延無視' : `${modeNames[mode]} / 時刻表推定 · 遅延・運休${mode === 'bus' ? '・渋滞' : ''}は未反映`;
    if (mode === 'all') showAllBadge();
  } catch {
    if (request === extraRequest && mode === transportMode) {
      extraPins?.clearLayers(); document.getElementById('estimate-badge').hidden = true;
      extraStatus('読み込みに失敗しました。保存データ・サーバー状態を確認し、再試行します。');
    }
  } finally {
    if (mode === 'all' && transportMode === 'all' && request === extraRequest) showAllBadge();
    clearTimeout(timeout);
    if (request === extraRequest && mode === transportMode) extraTimer = setTimeout(requestExtra, 30000);
  }
}
function fitTransport() {
  const bounds = transportMode === 'all' ? L.latLngBounds([]) : transportMode === 'metro' ? networkBounds : extraBounds;
  if (transportMode === 'all') {
    if (networkBounds?.isValid()) bounds.extend(networkBounds);
    if (extraBounds?.isValid()) bounds.extend(extraBounds);
  }
  if (bounds?.isValid()) map.fitBounds(bounds, {padding: [40, 40]});
}
function initExtraModes() {
  map.createPane('extra-pins'); map.getPane('extra-pins').style.zIndex = 450;
  extraRenderer = L.canvas({pane: 'extra-pins', padding: 0.1});
  ExtraArrow = L.CircleMarker.extend({_updatePath: drawTrainArrow});
  extraPins = L.layerGroup().addTo(map);
  extraStations = L.layerGroup().addTo(map);
  document.getElementById('transport-mode').disabled = false;
  document.getElementById('transport-mode').addEventListener('change', switchTransport);
  document.getElementById('extra-route').addEventListener('change', event => { extraSelection = event.target.value; styleExtraRoutes(); requestExtra(); });
  document.getElementById('extra-show-pins').addEventListener('change', requestExtra);
  let movementTimer;
  map.on('moveend', () => { clearTimeout(movementTimer); if (transportMode !== 'metro') movementTimer = setTimeout(requestExtra, 300); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && transportMode !== 'metro') requestExtra();
    if (document.hidden) { extraRequest++; extraController?.abort(); clearTimeout(extraTimer); }
  });
}
