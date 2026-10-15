'use strict';
// Nearby place search on the transit map. Adapted from nearby_places_api/static/app.js;
// wrapped in a function so its names do not collide with the transit scripts' globals (map, select, ...).
(() => {
  const CATEGORY_LABELS = {restaurant: '飲食店', tourism: '観光地'};
  const CATEGORY_COLORS = {restaurant: '#d9480f', tourism: '#1c7ed6'};
  const $ = (id) => document.getElementById(id);

  let center = null;
  let centerMarker = null;
  let circle = null;
  let placeLayer = null;
  let renderer = null;
  let ready = false;
  let requestId = 0;

  function showMessage(text, kind = '') {
    $('place-message').textContent = text;
    $('place-message').className = kind;
  }

  function setCenter(lat, lon, label = null) {
    center = {lat, lon};
    $('place-center').textContent = `中心：${label ?? `${lat.toFixed(6)}, ${lon.toFixed(6)}`}`;
    $('place-submit').disabled = !ready;
    if (centerMarker) centerMarker.setLatLng([lat, lon]);
    else centerMarker = L.marker([lat, lon], {title: '検索の中心'}).addTo(map);
    // Results and the circle belong to the old center; drop them even if the new search stops at validation.
    clearResults();
    circle?.remove();
    circle = null;
    search();
  }

  function clearResults() {
    placeLayer.clearLayers();
    $('place-results').replaceChildren();
  }

  function clearAll() {
    ++requestId;  // Ignore a search still in flight.
    clearResults();
    circle?.remove();
    centerMarker?.remove();
    circle = centerMarker = center = null;
    $('place-center').textContent = '中心：未指定';
    $('place-submit').disabled = true;
    showMessage('');
  }

  // Only http(s) links are shown; the API filters too, but the page should not trust that alone.
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
    } catch {
      return null;
    }
  }

  function popupContent(place) {
    const box = document.createElement('div');
    box.className = 'popup';
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
    const website = safeUrl(place.website);
    if (website) {
      const link = document.createElement('a');
      link.href = website;
      link.textContent = 'ホームページ';
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const line = document.createElement('div');
      line.append(link);
      box.append(line);
    }
    const route = document.createElement('button');
    route.type = 'button';
    route.className = 'popup-action';
    route.textContent = 'ここへの遠回り経路';
    // journey.js listens for this; places.js does not need to know about routes.
    route.addEventListener('click', () => map.fire('placeselect', {place}));
    box.append(route);
    return box;
  }

  function renderResults(body) {
    clearResults();
    for (const place of body.places) {
      const marker = L.circleMarker([place.lat, place.lon], {
        renderer, radius: 7, weight: 2, color: '#fff', fillColor: CATEGORY_COLORS[place.category], fillOpacity: 0.9,
      }).bindPopup(popupContent(place)).addTo(placeLayer);

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
      meta.textContent = [CATEGORY_LABELS[place.category], place.source_category, place.address,
        safeUrl(place.website) && 'HPあり'].filter(Boolean).join(' ・ ');
      button.append(name, distance, meta);
      button.addEventListener('click', () => {
        map.panTo(marker.getLatLng());
        marker.openPopup();
      });
      const item = document.createElement('li');
      item.append(button);
      $('place-results').append(item);
    }
  }

  async function search(event) {
    event?.preventDefault();
    // Invalidate any search in flight first, so its response cannot be shown if this one stops early.
    const current = ++requestId;
    if (!ready) {
      showMessage('施設検索APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
      return;
    }
    if (!center) {
      showMessage('地図をクリックするか、現在地を取得して中心を指定してください。', 'warning');
      return;
    }
    if (!$('place-search').reportValidity()) return;
    const radius = Number($('place-radius').value);
    const params = new URLSearchParams({
      lat: center.lat, lon: center.lon, radius_m: radius, category: $('place-category').value, limit: $('place-limit').value,
    });
    if (circle) circle.setLatLng([center.lat, center.lon]).setRadius(radius);
    // Not interactive, so clicks inside the circle still move the center.
    else circle = L.circle([center.lat, center.lon], {radius, renderer, interactive: false, color: '#1f7a63', weight: 2, fillOpacity: 0.06}).addTo(map);
    showMessage('検索中…');
    let response, body;
    try {
      response = await fetch(`/api/v1/places/nearby?${params}`);
      body = await response.json();
    } catch {
      if (current === requestId) {
        clearResults();
        showMessage('施設検索APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
      }
      return;
    }
    if (current !== requestId) return;  // A newer search has started.
    if (!response.ok) {
      clearResults();
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

  async function showCoverage() {
    try {
      const response = await fetch('/api/v1/places/coverage');
      const body = await response.json();
      if (!response.ok) {
        showMessage(body.error?.message ?? '施設データの対象範囲を取得できませんでした。', 'error');
        return;
      }
      ready = true;
      $('place-submit').disabled = !center;
      L.geoJSON(body.boundary, {renderer, style: {color: '#5d7079', weight: 1.5, dashArray: '4 4', fill: false}, interactive: false}).addTo(map);
    } catch {
      showMessage('施設検索APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
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

  // The page's basemap (app.js skips its dark tiles because #map has data-basemap).
  // ベクトルタイルを使えない場合は、地図記号ありのラスタタイルで表示する。
  async function addBaseMap() {
    map.setMinZoom(5).setMaxZoom(18);
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
    // app.js creates the map synchronously; it stays null when Leaflet failed to load (app.js shows that error).
    if (!map) return;
    addBaseMap();
    // Above the transit lines, stations and train arrows. SVG, not Canvas: a Canvas in a higher pane
    // would swallow clicks meant for the transit layers below it.
    map.createPane('places').style.zIndex = 450;
    renderer = L.svg({pane: 'places'});
    placeLayer = L.layerGroup().addTo(map);
    // Clicks on routes, stations, arrows and place markers would otherwise also fire a map click and move the
    // center; the transit scripts have no map click handler, so this default changes nothing for them.
    // Both are needed: Path sets its own default, and L.geoJSON passes the Layer default to the lines it makes.
    L.Layer.mergeOptions({bubblingMouseEvents: false});
    L.Path.mergeOptions({bubblingMouseEvents: false});
    map.on('click', (event) => setCenter(event.latlng.lat, event.latlng.lng));
    // Fired by the transit scripts when a metro or JR station is clicked.
    map.on('stationclick', ({latlng, name}) => setCenter(latlng.lat, latlng.lng, `${name}駅 付近`));
    $('place-locate').addEventListener('click', locate);
    $('place-clear').addEventListener('click', clearAll);
    $('place-search').addEventListener('submit', search);
    showCoverage();
  }

  init();
})();
