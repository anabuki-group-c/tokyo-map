'use strict';

const CATEGORY_LABELS = {restaurant: '飲食店', tourism: '観光地'};
const CATEGORY_COLORS = {restaurant: '#d9480f', tourism: '#1c7ed6'};
const $ = (id) => document.getElementById(id);

let map;
let center = null;
let centerMarker = null;
let circle = null;
let placeLayer = null;
let markers = new Map();
let requestId = 0;
// '' when this page is served by `places serve`; the default server otherwise (e.g. VS Code Live Server).
const API_CANDIDATES = ['', 'http://127.0.0.1:8000'];
let apiBase = null;

function showMessage(text, kind = '') {
  $('message').textContent = text;
  $('message').className = kind;
}

function setCenter(lat, lon) {
  center = {lat, lon};
  $('center').textContent = `中心：${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  $('submit').disabled = apiBase === null;
  if (centerMarker) centerMarker.setLatLng([lat, lon]);
  else centerMarker = L.marker([lat, lon], {title: '検索の中心'}).addTo(map);
  search();
}

function popupContent(place) {
  const box = document.createElement('div');
  const name = document.createElement('strong');
  name.textContent = place.name ?? '（名称なし）';
  const detail = document.createElement('div');
  detail.textContent = `${CATEGORY_LABELS[place.category]}（${place.source_category ?? '分類不明'}）・${place.distance_m} m`;
  box.append(name, detail);
  if (place.address) {
    const address = document.createElement('div');
    address.textContent = place.address;
    box.append(address);
  }
  return box;
}

function renderResults(body) {
  placeLayer.clearLayers();
  markers = new Map();
  const list = $('results');
  list.replaceChildren();
  for (const place of body.places) {
    const marker = L.circleMarker([place.lat, place.lon], {
      radius: 7, weight: 2, color: '#fff', fillColor: CATEGORY_COLORS[place.category], fillOpacity: 0.9,
    }).bindPopup(popupContent(place)).addTo(placeLayer);
    markers.set(place.id, marker);

    const button = document.createElement('button');
    button.type = 'button';
    const name = document.createElement('span');
    name.className = 'name';
    const dot = document.createElement('span');
    dot.className = `dot ${place.category}`;
    name.append(dot, place.name ?? '（名称なし）');
    const distance = document.createElement('span');
    distance.className = 'distance';
    distance.textContent = `${place.distance_m} m`;
    const meta = document.createElement('span');
    meta.className = 'meta';
    meta.textContent = [CATEGORY_LABELS[place.category], place.source_category, place.address].filter(Boolean).join(' ・ ');
    button.append(name, distance, meta);
    button.addEventListener('click', () => {
      map.panTo(marker.getLatLng());
      marker.openPopup();
    });
    const item = document.createElement('li');
    item.append(button);
    list.append(item);
  }
}

async function search(event) {
  event?.preventDefault();
  if (apiBase === null) {
    showMessage('APIに接続できませんでした。`uv run places serve` でサーバを起動してください。', 'error');
    return;
  }
  if (!center) {
    showMessage('地図をクリックするか、現在地を取得して中心を指定してください。', 'warning');
    return;
  }
  if (!$('search').reportValidity()) return;
  const radius = Number($('radius').value);
  const params = new URLSearchParams({
    lat: center.lat, lon: center.lon, radius_m: radius, category: $('category').value, limit: $('limit').value,
  });
  if (circle) circle.setLatLng([center.lat, center.lon]).setRadius(radius);
  else circle = L.circle([center.lat, center.lon], {radius, color: '#1f7a63', weight: 2, fillOpacity: 0.06}).addTo(map);
  const current = ++requestId;
  showMessage('検索中…');
  let response, body;
  try {
    response = await fetch(`${apiBase}/api/v1/places/nearby?${params}`);
    body = await response.json();
  } catch {
    if (current === requestId) showMessage('APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
    return;
  }
  if (current !== requestId) return;  // A newer search has started.
  if (!response.ok) {
    placeLayer.clearLayers();
    $('results').replaceChildren();
    showMessage(body.error?.message ?? `エラーが発生しました（HTTP ${response.status}）。`, 'error');
    return;
  }
  renderResults(body);
  const notes = [body.count === 0 ? '該当する施設はありません。' : `${body.count}件（距離順）`];
  if (body.partial_coverage) notes.push('検索範囲の一部が東京都の外（他県や海）にかかっています。都外の施設は表示されません。');
  showMessage(notes.join(' '), body.partial_coverage ? 'warning' : '');
  // Computed from lat/lon, not circle.getBounds(), which is stale during a zoom animation.
  map.fitBounds(L.latLng(center.lat, center.lon).toBounds(radius * 2), {padding: [24, 24]});
}

function locate() {
  if (!navigator.geolocation) {
    showMessage('このブラウザは位置情報の取得に対応していません。地図をクリックして中心を指定してください。', 'error');
    return;
  }
  showMessage('現在地を取得中…');
  navigator.geolocation.getCurrentPosition(
    (position) => setCenter(position.coords.latitude, position.coords.longitude),
    () => showMessage('現在地を取得できませんでした。位置情報の許可を確認するか、地図をクリックしてください。', 'error'),
    {enableHighAccuracy: true, timeout: 10000},
  );
}

async function findApi() {
  for (const base of API_CANDIDATES) {
    try {
      const response = await fetch(`${base}/api/v1/places/coverage`);
      if (response.headers.get('Content-Type')?.startsWith('application/json')) return {base, response};
    } catch {
      // Not reachable from this page; try the next candidate.
    }
  }
  return null;
}

async function showCoverage() {
  const found = await findApi();
  if (!found) {
    showMessage('APIに接続できませんでした。`uv run places serve` でサーバを起動してください。', 'error');
    return;
  }
  apiBase = found.base;
  $('submit').disabled = !center;
  try {
    const response = found.response;
    const body = await response.json();
    if (!response.ok) {
      showMessage(body.error?.message ?? '施設データの対象範囲を取得できませんでした。', 'error');
      return;
    }
    // Tokyo reaches Minamitorishima, so keep the initial central-Tokyo view instead of fitting the whole boundary.
    L.geoJSON(body.boundary, {style: {color: '#5d7079', weight: 1.5, dashArray: '4 4', fill: false}, interactive: false}).addTo(map);
  } catch {
    showMessage('APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
  }
}

const BASEMAP_ATTRIBUTION = '<a href="https://maps.gsi.go.jp/development/ichiran.html">地理院タイル</a> | Places &copy; Overture Maps Foundation';
const PALE_VECTOR_STYLE = 'https://gsi-cyberjapan.github.io/gsivectortile-mapbox-gl-js/pale.json';

// 地理院ベクトルタイルの淡色地図から地図記号（アイコン）を除く。文字の注記は残す。
function withoutMapSymbols(style) {
  const layers = [];
  for (const layer of style.layers) {
    const layout = layer.layout || {};
    if (layer.type !== 'symbol' || !('icon-image' in layout)) {
      layers.push(layer);
    } else if ('text-field' in layout) {
      const {'icon-image': _icon, ...rest} = layout;
      layers.push({...layer, layout: rest});
    }
  }
  return {...style, layers};
}

function addRasterBaseMap() {
  L.tileLayer('https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png', {
    attribution: BASEMAP_ATTRIBUTION, minZoom: 5, maxZoom: 18,
  }).addTo(map);
}

// ベクトルタイルを使えない場合は、地図記号ありのラスタタイルで表示する。
async function addBaseMap() {
  if (!window.maplibregl || !L.maplibreGL) {
    addRasterBaseMap();
    return;
  }
  try {
    const response = await fetch(PALE_VECTOR_STYLE);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const style = withoutMapSymbols(await response.json());
    // 帰属表示は attributionControl.customAttribution から取られる（未指定だとスタイル側の表記で置き換わる）。
    L.maplibreGL({style, attributionControl: {customAttribution: BASEMAP_ATTRIBUTION}}).addTo(map);
  } catch (error) {
    console.warn('ベクトルタイルを読み込めませんでした', error);
    addRasterBaseMap();
  }
}

function init() {
  if (!window.L) {
    $('map-error').textContent = '地図ライブラリを読み込めませんでした。インターネット接続を確認してください。';
    $('map-error').hidden = false;
    return;
  }
  map = L.map('map', {minZoom: 5, maxZoom: 18}).setView([35.681236, 139.767125], 11);
  addBaseMap();
  placeLayer = L.layerGroup().addTo(map);
  map.on('click', (event) => setCenter(event.latlng.lat, event.latlng.lng));
  $('locate').addEventListener('click', locate);
  $('search').addEventListener('submit', search);
  showCoverage();
}

document.addEventListener('DOMContentLoaded', init);
