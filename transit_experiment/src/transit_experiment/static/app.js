'use strict';
const routes = [
  ['Ginza', '銀座線', 'G', '#ff9500'], ['Marunouchi', '丸ノ内線', 'M', '#f45468'],
  ['Hibiya', '日比谷線', 'H', '#bec7ce'], ['Tozai', '東西線', 'T', '#32bce3'],
  ['Chiyoda', '千代田線', 'C', '#31cf98'], ['Yurakucho', '有楽町線', 'Y', '#d8bb72'],
  ['Hanzomon', '半蔵門線', 'Z', '#ae8cfa'], ['Namboku', '南北線', 'N', '#35c9bb'],
  ['Fukutoshin', '副都心線', 'F', '#c59068'],
].map(([id, name, symbol, color]) => ({id, name, symbol, color}));
let selected = null, payload = null, map = null, networkBounds;
const layers = [];
let flowLayer = null;
const flowFeatures = new Map();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let motion = false;
function updateFlow() {
  if (flowLayer && map) { map.removeLayer(flowLayer); flowLayer = null; }
  if (!map || transportMode !== 'metro' || !motion || !selected || document.hidden || reducedMotion.matches) return;
  const feature = flowFeatures.get(selected);
  if (!feature) return;
  flowLayer = L.geoJSON(feature, {
    renderer: L.svg({pane: 'flows'}), interactive: false,
    style: {color: routes.find(r => r.id === selected).color, weight: 3,
      dashArray: '7 53', className: 'route-flow', smoothFactor: 2},
  }).addTo(map);
}
document.addEventListener('visibilitychange', updateFlow);
reducedMotion.addEventListener('change', () => { if (reducedMotion.matches) motion = false; setMotion(); });
const byFeature = feature => routes.find(r => (feature.properties.N02_003 || '').includes(r.name));
function select(id) {
  selected = selected === id ? null : id;
  document.getElementById('selection').textContent = transportMode === 'all'
    ? `すべて / メトロ: ${selected ? routes.find(r => r.id === selected).name : '全路線'}`
    : selected ? routes.find(r => r.id === selected).name : '東京メトロ / 全路線';
  for (const {layer, route, kind} of layers) {
    const visible = !selected || route?.id === selected;
    layer.setStyle({opacity: visible ? (kind === 'base' ? 0.65 : 0.95) : 0.06, fillOpacity: visible ? 1 : 0.06});
  }
  updateFlow();
  renderTrainPins();
  render();
}
function render() {
  const container = document.getElementById('lines');
  container.replaceChildren();
  for (const route of routes) {
    const notices = (payload?.notices || []).filter(n => {
      const id = (n['odpt:railway'] || '').split('.').pop();
      return id === route.id || (route.id === 'Marunouchi' && id === 'MarunouchiBranch');
    });
    const texts = [...new Set(notices.map(n => {
      const text = n['odpt:trainInformationText'];
      return typeof text === 'string' ? text : text?.ja || '';
    }).filter(Boolean))];
    const normal = texts.length > 0 && texts.every(t => t.includes('平常どおり'));
    const button = document.createElement('button');
    button.className = `line${selected === route.id ? ' active' : ''}`;
    button.setAttribute('aria-pressed', String(selected === route.id));
    button.style.setProperty('--line', route.color);
    const symbol = document.createElement('span'); symbol.className = 'symbol'; symbol.textContent = route.symbol;
    const name = document.createElement('span'); name.className = 'line-name'; name.textContent = route.name;
    const en = document.createElement('span'); en.className = 'line-en'; en.textContent = route.id.toUpperCase(); name.append(en);
    const state = document.createElement('span'); state.className = `state${texts.length && !normal ? ' alert' : ''}`;
    state.textContent = texts.length ? (payload.stale ? '保存情報' : normal ? '平常運転' : 'お知らせ') : '情報なし';
    button.append(symbol, name, state);
    if (texts.length && (!normal || selected === route.id)) {
      const detail = document.createElement('span'); detail.className = 'notice'; detail.textContent = texts.join(' / '); button.append(detail);
    }
    button.addEventListener('click', () => select(route.id));
    container.append(button);
  }
}
async function getJSON(url) {
  const response = await fetch(url, {signal: AbortSignal.timeout(35000)});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
async function refreshStatus() {
  try {
    payload = await getJSON('/api/status');
    const updated = payload.updated_at ? new Date(payload.updated_at).toLocaleString('ja-JP', {timeZone: 'Asia/Tokyo'}) + ' JST' : '未取得';
    document.getElementById('updated').textContent = `最終取得 ${updated}${payload.stale ? ' · 保存情報 / 更新待ち' : ''}`;
    const warning = document.getElementById('warning');
    warning.hidden = !payload.error; warning.textContent = payload.error || '';
  } catch {
    if (payload) payload.stale = true;
    document.getElementById('updated').textContent = payload?.updated_at ? `最終取得 ${new Date(payload.updated_at).toLocaleString('ja-JP', {timeZone: 'Asia/Tokyo'})} JST · 保存情報` : '運行情報 未取得';
    const warning = document.getElementById('warning'); warning.hidden = false;
    warning.textContent = 'サーバーに接続できません。運行情報は最新ではない可能性があります。';
  } finally {
    render();
    // Completion-based polling: no overlapping requests. ODPT TTL is enforced server-side.
    setTimeout(() => { if (document.hidden) scheduleVisible(); else refreshStatus(); }, 120000);
  }
}
function scheduleVisible() {
  const resume = () => { if (!document.hidden) { document.removeEventListener('visibilitychange', resume); refreshStatus(); } };
  document.addEventListener('visibilitychange', resume);
}
async function initMap() {
  try {
    if (!window.L) throw new Error('地図ライブラリを読み込めません。ネットワーク接続を確認してください。');
    map = L.map('map', {zoomControl: false, preferCanvas: true, zoomAnimation: false, fadeAnimation: false}).setView([35.68, 139.76], 12);
    L.control.zoom({position: 'bottomright'}).addTo(map);
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>', maxZoom: 19, updateWhenIdle: true, keepBuffer: 1,
    }).addTo(map);
    const [railroads, stations] = await Promise.all([getJSON('/data/railroads'), getJSON('/data/stations')]);
    map.createPane('flows'); map.getPane('flows').style.zIndex = 410; map.getPane('flows').style.pointerEvents = 'none';
    map.createPane('stations'); map.getPane('stations').style.zIndex = 420;
    // One multipart geometry per route instead of hundreds of separate layers.
    const grouped = {type: 'FeatureCollection', features: routes.map(route => ({
      type: 'Feature', properties: {N02_003: route.name},
      geometry: {type: 'MultiLineString', coordinates: railroads.features
        .filter(feature => byFeature(feature)?.id === route.id)
        .flatMap(feature => feature.geometry.type === 'MultiLineString' ? feature.geometry.coordinates : [feature.geometry.coordinates])},
    })).filter(feature => feature.geometry.coordinates.length)};
    for (const feature of grouped.features) flowFeatures.set(byFeature(feature).id, feature);
    const base = L.geoJSON(grouped, {
      style: feature => ({color: byFeature(feature)?.color || '#94a3b8', weight: 4, opacity: 0.65, smoothFactor: 2}),
      onEachFeature: (feature, layer) => {
        const route = byFeature(feature); layers.push({layer, route, kind: 'base'});
        if (route) { layer.bindTooltip(route.name); layer.on('click', () => select(route.id)); }
      },
    }).addTo(map);
    networkBounds = base.getBounds();
    if (networkBounds.isValid()) map.fitBounds(networkBounds, {padding: [40, 40]});
    const seen = new Set();
    for (const feature of stations.features) {
      const route = byFeature(feature), geometry = feature.geometry;
      if (!route || geometry.type !== 'LineString' || !geometry.coordinates.length) continue;
      const coordinates = geometry.coordinates;
      const start = coordinates[0], end = coordinates[coordinates.length - 1];
      const center = [(start[1] + end[1]) / 2, (start[0] + end[0]) / 2];
      const stationName = feature.properties.N02_005 || '駅';
      const key = `${route.id}:${stationName}`; if (seen.has(key)) continue; seen.add(key);
      const marker = L.circleMarker(center, {pane: 'stations', radius: 3.5, color: route.color, weight: 1.5, fillColor: '#101b21', fillOpacity: 1}).addTo(map);
      const label = document.createElement('span'); label.textContent = `${stationName} / ${route.name}`;
      marker.bindTooltip(label); layers.push({layer: marker, route, kind: 'station'});
    }
    initExtraModes();
    renderTrainPins();
    // Reapply a selection made while the GeoJSON was loading.
    if (selected) { const id = selected; selected = null; select(id); }
  } catch (error) {
    const el = document.getElementById('map-error'); el.hidden = false;
    el.textContent = `地図を読み込めませんでした。${error.message} — 路線データがない場合は transit import-static-railways を実行してください。`;
  }
}
document.getElementById('reset').addEventListener('click', () => { selected = null; select(null); });
document.getElementById('fit').addEventListener('click', fitTransport);
function setMotion() {
  const button = document.getElementById('motion');
  button.textContent = `選択路線の動き ${motion ? 'ON' : 'OFF'}`;
  button.setAttribute('aria-pressed', String(motion));
  button.disabled = reducedMotion.matches;
  button.title = reducedMotion.matches ? '端末の視差効果を減らす設定に従い停止しています' : '路線を選択すると、その1路線だけ動きます';
  updateFlow();
}
document.getElementById('motion').addEventListener('click', () => { motion = !motion; setMotion(); });
setMotion(); render(); initMap(); refreshStatus(); startTrainPolling();
