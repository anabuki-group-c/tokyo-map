'use strict';
let trainData = null, trainGroup = null, trainRenderer = null, TrainArrow = null;
function drawTrainArrow() {
  // Leaflet 1.9.4 Canvas path hook: no DOM/SVG node per train.
  const renderer = this._renderer;
  if (!renderer._drawing || this._empty()) return;
  const ctx = renderer._ctx;
  const known = Number.isFinite(this.options.heading);
  ctx.save();
  ctx.translate(this._point.x, this._point.y);
  ctx.rotate(known ? this.options.heading * Math.PI / 180 : 0);
  ctx.beginPath();
  if (known) {
    ctx.moveTo(0, -10); ctx.lineTo(6, 7); ctx.lineTo(0, 3); ctx.lineTo(-6, 7);
  } else {
    // Do not invent a northbound direction if the path is unavailable.
    ctx.moveTo(0, -6); ctx.lineTo(6, 0); ctx.lineTo(0, 6); ctx.lineTo(-6, 0);
  }
  ctx.closePath();
  ctx.restore();
  renderer._fillStroke(ctx, this);
}
function renderTrainPins() {
  if (!['metro', 'all'].includes(transportMode)) { trainGroup?.clearLayers(); return; }
  if (!map || !trainData) return;
  if (!trainGroup) {
    map.createPane('trains'); map.getPane('trains').style.zIndex = 450;
    trainRenderer = L.canvas({pane: 'trains', padding: 0.1});
    TrainArrow = L.CircleMarker.extend({_updatePath: drawTrainArrow});
    trainGroup = L.layerGroup().addTo(map);
  }
  trainGroup.clearLayers();
  const age = Date.now() - Date.parse(trainData.generated_at);
  const old = !Number.isFinite(age) || age > 180000;
  const show = document.getElementById('estimated-pins').checked;
  let displayed = 0, alerts = 0;
  if (!old && !trainData.error && show) for (const pin of trainData.pins) {
    const id = pin.railway.split('.').pop();
    const route = routes.find(r => r.id === id || (r.id === 'Marunouchi' && id === 'MarunouchiBranch'));
    if (selected && route?.id !== selected) continue;
    const marker = new TrainArrow([pin.lat, pin.lon], {
      renderer: trainRenderer, radius: 11, weight: 1.5, heading: pin.heading,
      color: pin.alert ? '#ff5c63' : pin.status_known ? '#ffffff' : '#9aa9b3',
      fillColor: pin.alert ? '#ff5c63' : route?.color || '#81e5c3', fillOpacity: 0.9,
    });
    const label = document.createElement('div');
    label.style.whiteSpace = 'pre-line';
    const delay = pin.delay_seconds != null
      ? `列車別遅延 ${Math.round(pin.delay_seconds / 6) / 10}分を反映`
      : pin.alert ? '路線に運行のお知らせあり。遅延量は不明のため時間補正なし。' : '定刻ダイヤを仮定（列車別の実際の遅れは未確認）';
    const planned = pin.planned_start && pin.planned_end
      ? (pin.planned_kind === 'dwell'
        ? `\n時刻表: ${pin.planned_start} 着 → ${pin.planned_end} 発`
        : `\n時刻表: ${pin.planned_start} 発 → ${pin.planned_end} 着`)
      : '';
    label.textContent = `${route?.name || id} / 列車 ${pin.number || '番号不明'}\n【ODPTの実時刻表に基づく推定位置】\n` +
      (pin.from === pin.to ? `${pin.from} 駅` : `${pin.from} → ${pin.to} / 区間の約${Math.round(pin.progress * 100)}%`) +
      planned + `\n${Number.isFinite(pin.heading) ? `進行方向: ${pin.heading_target}方面（停車中・終着では発着方向）` : '進行方向不明（ひし形）'}\n${delay}\n` + (pin.geometry_approximate ? '線路経路未接続のため両駅間を直線補間。\n' : '') +
      `計算時刻: ${new Date(trainData.generated_at).toLocaleTimeString('ja-JP', {timeZone: 'Asia/Tokyo'})} JST\n` +
      `時刻表更新: ${pin.timetable_updated_at || '不明'}\n実測位置ではありません。運休・臨時変更や駅間の加減速は反映できない場合があります。`;
    marker.bindPopup(label).addTo(trainGroup);
    displayed++; if (pin.alert) alerts++;
  }
  const status = document.getElementById('train-status');
  const pending = trainData.pending_routes || [];
  const stale = trainData.stale_routes || [];
  const failed = trainData.failed_routes || [];
  const day = trainData.calendar === 'SaturdayHoliday' ? '土休日' : '平日';
  if (trainData.error) status.textContent = '推定位置を取得できません。再試行を待っています。';
  else if (old) status.textContent = '推定位置は更新待ちです。古いピンは非表示にしています。';
  else {
    status.textContent = `ODPT実時刻表 ${trainData.timetable_count || 0}件を適用 / 推定 ${displayed}本 / ${day}ダイヤ（${trainData.service_date}）` +
      (alerts ? ` · お知らせ/遅延あり ${alerts}本` : '') +
      (pending.length ? ` · 時刻表取得待ち ${pending.length}路線` : '') +
      (stale.length ? ` · 古い時刻表キャッシュ ${stale.length}路線` : '') +
      (failed.length ? ` · 時刻表取得失敗 ${failed.length}路線（再試行待ち）` : '') +
      (trainData.unmapped ? ` · 駅座標不明 ${trainData.unmapped}本` : '') +
      (trainData.status_stale ? ' · 運行情報未確認' : '') +
      (trainData.pins.length === 0 ? ' · 対象時刻の推定列車なし（運休の判定ではありません）' : '');
  }
  const badge = document.getElementById('estimate-badge');
  badge.hidden = displayed === 0;
  badge.textContent = `メトロ：実時刻表を適用 · 駅間位置は推定${alerts ? ' / 赤＝運行のお知らせ・遅延あり' : ''}`;
  if (transportMode === 'all') showAllBadge();
}
async function refreshTrains() {
  if (!['metro', 'all'].includes(transportMode)) { setTimeout(refreshTrains, 30000); return; }
  try {
    const response = await fetch('/api/estimates', {signal: AbortSignal.timeout(90000)});
    if (!response.ok) throw new Error('Unavailable');
    trainData = await response.json();
  } catch {
    trainData = {pins: [], error: true};
  }
  renderTrainPins();
  // Only asks our server to recompute positions; this does not refetch ODPT timetables.
  setTimeout(() => {
    if (!document.hidden) refreshTrains();
    else {
      const resume = () => {
        if (!document.hidden) { document.removeEventListener('visibilitychange', resume); refreshTrains(); }
      };
      document.addEventListener('visibilitychange', resume);
    }
  }, 30000);
}
function startTrainPolling() {
  document.getElementById('estimated-pins').addEventListener('change', renderTrainPins);
  setInterval(() => { if (!document.hidden) renderTrainPins(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) renderTrainPins(); });
  refreshTrains();
}
