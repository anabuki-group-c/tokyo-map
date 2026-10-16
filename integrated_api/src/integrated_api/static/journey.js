'use strict';
// Long-route search: from the current location or a searched Tokyo address to a place picked in places.js.
(() => {
  const MODE_LABELS = {walk: '徒歩', metro: 'メトロ', jr: 'JR', bus: '都営バス'};
  const MODE_COLORS = {walk: '#495057', metro: '#7048e8', jr: '#2b8a3e', bus: '#e67700'};
  const GSI_SEARCH = 'https://msearch.gsi.go.jp/address-search/AddressSearch?q=';
  const $ = (id) => document.getElementById(id);

  let origin = null;
  let destination = null;
  let layer = null;
  let renderer = null;
  let requestId = 0;

  function showMessage(text, kind = '') {
    $('journey-message').textContent = text;
    $('journey-message').className = kind;
  }

  function setOrigin(lat, lon, label) {
    origin = {lat, lon, label};
    $('journey-origin').textContent = label;
    $('journey-candidates').replaceChildren();
  }

  function locate() {
    if (!navigator.geolocation) {
      showMessage('このブラウザは位置情報の取得に対応していません。住所で出発地を探してください。', 'error');
      return;
    }
    showMessage('現在地を取得中…');
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setOrigin(position.coords.latitude, position.coords.longitude, '現在地');
        showMessage('');
      },
      () => showMessage('現在地を取得できませんでした。位置情報の許可を確認するか、住所で出発地を探してください。', 'error'),
      {enableHighAccuracy: true, timeout: 10000},
    );
  }

  async function geocode() {
    const query = $('journey-address').value.trim();
    if (!query) {
      showMessage('住所を入力してください。', 'warning');
      return;
    }
    showMessage('住所を検索中…');
    let results;
    try {
      const response = await fetch(GSI_SEARCH + encodeURIComponent(query));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      results = await response.json();
    } catch {
      showMessage('住所検索（国土地理院）に接続できませんでした。', 'error');
      return;
    }
    // Only Tokyo: the timetables cover Tokyo, and the GSI search also returns loose matches all over Japan.
    const tokyo = results.filter((item) => item.properties?.title?.startsWith('東京都')).slice(0, 8);
    const list = $('journey-candidates');
    list.replaceChildren();
    if (!tokyo.length) {
      showMessage('東京都内の候補が見つかりませんでした。「渋谷区神南1」のように区市町村から入力してください。', 'warning');
      return;
    }
    showMessage('候補から出発地を選んでください。');
    for (const item of tokyo) {
      const [lon, lat] = item.geometry.coordinates;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = item.properties.title;
      button.addEventListener('click', () => {
        setOrigin(lat, lon, item.properties.title);
        showMessage('');
      });
      const entry = document.createElement('li');
      entry.append(button);
      list.append(entry);
    }
  }

  function clearRoute() {
    ++requestId;  // Ignore a search still in flight.
    layer.clearLayers();
    $('journey-result').replaceChildren();
    showMessage('');
  }

  function stopName(point, fallback) {
    return point.name ?? fallback;
  }

  function drawJourney(journey) {
    layer.clearLayers();
    const bounds = L.latLngBounds([]);
    for (const leg of journey.legs) {
      // path follows the track or road; without it, join the stops with straight lines.
      const points = leg.path ?? (leg.stops ?? [leg.from, leg.to]).map((p) => [p.lat, p.lon]);
      points.forEach((p) => bounds.extend(p));
      L.polyline(points, {
        renderer, weight: leg.mode === 'walk' ? 3 : 5, color: MODE_COLORS[leg.mode] ?? '#1d2a30', opacity: 0.95,
        dashArray: leg.mode === 'walk' ? '4 6' : null,
      }).bindTooltip(leg.mode === 'walk' ? '徒歩' : `${MODE_LABELS[leg.mode]} ${leg.line}`).addTo(layer);
    }
    const first = journey.legs[0].from;
    L.circleMarker([first.lat, first.lon], {renderer, radius: 7, color: '#fff', weight: 2, fillColor: '#1f7a63', fillOpacity: 1})
      .bindTooltip('出発地').addTo(layer);
    if (bounds.isValid()) map.fitBounds(bounds, {padding: [32, 32]});
  }

  function duration(seconds) {
    const minutes = Math.round(seconds / 60);
    return minutes >= 60 ? `${Math.floor(minutes / 60)}時間${minutes % 60}分` : `${minutes}分`;
  }

  function renderJourney(body) {
    const journey = body.journey;
    const box = $('journey-result');
    box.replaceChildren();
    const summary = document.createElement('p');
    summary.className = 'journey-summary';
    summary.textContent = `総所要時間 ${duration(journey.total_seconds)} ・ ${journey.depart} 発 → ${journey.arrive} 着`;
    const breakdown = document.createElement('p');
    breakdown.className = 'muted';
    breakdown.textContent = `内訳：乗車 ${duration(journey.ride_seconds)} ・ 待ち ${duration(journey.wait_seconds)} ・ 徒歩 ${duration(journey.walk_seconds)}`
      + ` ／ 推定移動距離 ${(journey.distance_m / 1000).toFixed(1)} km`;
    const legs = document.createElement('ol');
    legs.className = 'journey-legs';
    for (const leg of journey.legs) {
      const item = document.createElement('li');
      const mode = document.createElement('span');
      mode.className = 'journey-mode';
      mode.style.setProperty('--mode', MODE_COLORS[leg.mode] ?? '#1d2a30');
      mode.textContent = leg.mode === 'walk' ? '徒歩' : `${MODE_LABELS[leg.mode]} ${leg.line}`;
      const detail = document.createElement('span');
      detail.className = 'meta';
      const stops = leg.stops ? `・${leg.stops.length - 1}区間` : '';
      detail.textContent = `${stopName(leg.from, origin?.label ?? '出発地')} → ${stopName(leg.to, destination?.name ?? '目的地')}`
        + `（${leg.depart}–${leg.arrive}・${(leg.distance_m / 1000).toFixed(1)} km${stops}）`;
      item.append(mode, detail);
      legs.append(item);
    }
    const notes = document.createElement('p');
    notes.className = 'muted';
    notes.textContent = [...body.notes, `使用データ：${body.sources.join('、')}`].join(' ');
    box.append(summary, breakdown, legs, notes);
    drawJourney(journey);
  }

  async function search(event) {
    event.preventDefault();
    const current = ++requestId;
    if (!origin) {
      showMessage('出発地を選んでください（現在地または住所検索）。', 'warning');
      return;
    }
    if (!destination) {
      showMessage('目的地を選んでください。周辺施設の詳細から「ここへの遠回り経路」を押します。', 'warning');
      return;
    }
    if (!$('journey-search').reportValidity()) return;
    const params = new URLSearchParams({
      from_lat: origin.lat, from_lon: origin.lon, to_lat: destination.lat, to_lon: destination.lon, arrive: $('journey-arrive').value,
      radius_m: $('journey-radius').value,
    });
    if ($('journey-depart').value) params.set('depart', $('journey-depart').value);
    showMessage('遠回り経路を探索中…（最大10秒ほどかかります）');
    let response, body;
    try {
      response = await fetch(`/api/journey/longest?${params}`);
      body = await response.json();
    } catch {
      if (current === requestId) showMessage('経路探索APIに接続できませんでした。サーバが起動しているか確認してください。', 'error');
      return;
    }
    if (current !== requestId) return;  // A newer search has started.
    if (!response.ok || !body.journey) {
      layer.clearLayers();
      $('journey-result').replaceChildren();
      showMessage(body.error ?? `エラーが発生しました（HTTP ${response.status}）。`, 'error');
      return;
    }
    // 探索完了なら条件内で最長と確認済み。時間上限なら、探した範囲での最長。
    showMessage(body.complete ? '探索完了：この条件で最も時間のかかる経路です。' : '計算時間の上限に達しました。探索した範囲で最長の経路です（最長とは限りません）。',
      body.complete ? '' : 'warning');
    renderJourney(body);
  }

  function init() {
    if (!map) return;  // app.js reports a missing Leaflet.
    // Under the place markers (450), above the transit Canvas.
    map.createPane('journey').style.zIndex = 440;
    renderer = L.svg({pane: 'journey'});
    layer = L.layerGroup().addTo(map);
    map.on('placeselect', ({place}) => {
      destination = place;
      $('journey-destination').textContent = place.name ?? '（名称なし）';
      map.closePopup();
      $('journey-panel').scrollIntoView({behavior: 'smooth', block: 'start'});
    });
    $('journey-locate').addEventListener('click', locate);
    $('journey-geocode').addEventListener('click', geocode);
    $('journey-address').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();  // Search the address instead of submitting the route form.
        geocode();
      }
    });
    $('journey-clear').addEventListener('click', clearRoute);
    $('journey-search').addEventListener('submit', search);
  }

  init();
})();
