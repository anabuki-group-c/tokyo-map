'use strict';
/* 最長経路実験のフロントエンド。状態（現在地・使用済み区間・累積乗車時間）は
   画面内でのみ保持。再計画はサーバーに現状態を送って行う。 */
const $ = (id) => document.getElementById(id);
const network = { nodes: new Map(), edges: [], routes: new Map(), byName: new Map() };
const state = { current: null, used: new Set(), elapsed: 0, budgetTotal: null, started: false, plan: null, cursor: 0, detour: 0, simulationStart: null, busy: false };
let map = null;
const routeLayers = new Map();
let planLayer = null, currentMarker = null, planNodes = [];

function status(text) { $('status').textContent = text; }

function resolveName(name) {
  const node = network.byName.get((name || '').trim());
  return node ? node.id : null;
}

function nodeName(id) {
  const node = network.nodes.get(id);
  return node ? node.name : id;
}

function targetDate() {
  const value = $('arrive-by').value;
  const target = new Date(value);
  return value && Number.isFinite(target.getTime()) ? target : null;
}

function remainingBudget() {
  if (state.started) return state.budgetTotal - state.elapsed - state.detour;
  const target = targetDate();
  return target ? (target - new Date()) / 60000 : null;
}

function usedSegment(index) {
  const edge = network.edges[index];
  return [...state.used].some(i => {
    const e = network.edges[i];
    return (e.u === edge.u && e.v === edge.v) || (e.v === edge.u && e.u === edge.v);
  });
}

function invalidatePlan() {
  state.plan = null;
  state.cursor = 0;
  renderPlan();
  updateProgress();
}

function checkedRoutes() {
  const boxes = [...document.querySelectorAll('#filters input[type=checkbox]')];
  const checked = boxes.filter(b => b.checked).map(b => b.dataset.route);
  return checked.length === boxes.length ? null : checked;
}

function pickStation(node) {
  if (state.busy) return;
  if (!state.started && !$('start').value) $('start').value = node.name;
  else $('goal').value = node.name;
  invalidatePlan();
  status(`「${node.name}」を設定しました。`);
}

function drawCurrent() {
  if (currentMarker) currentMarker.remove();
  currentMarker = null;
  if (!state.current) return;
  const node = network.nodes.get(state.current);
  if (!node) return;
  currentMarker = L.circleMarker([node.lat, node.lon], {
    radius: 6, color: '#ffffff', weight: 3, fillOpacity: 1, fillColor: '#2d6cdf',
  }).addTo(map).bindTooltip(`現在地: ${node.name}`).openTooltip();
}

function drawPlan() {
  if (planLayer) { planLayer.remove(); planLayer = null; }
  planNodes = [];
  const result = state.plan;
  if (!result || !result.nodes || result.nodes.length < 2) return;
  planNodes = result.nodes;
  const segments = [];
  for (let i = 0; i < result.edges.length; i++) {
    const a = network.nodes.get(result.nodes[i]);
    const b = network.nodes.get(result.nodes[i + 1]);
    if (a && b) segments.push([[a.lat, a.lon], [b.lat, b.lon]]);
  }
  planLayer = L.polyline(segments, { color: '#ffe14d', weight: 5, opacity: .95 }).addTo(map);
  map.fitBounds(planLayer.getBounds(), { padding: [30, 30] });
}

function renderPlan() {
  const panel = $('plan-panel');
  const result = state.plan;
  if (!result || !result.nodes.length) {
    panel.hidden = true;
    if (planLayer) { planLayer.remove(); planLayer = null; }
    planNodes = [];
    return;
  }
  panel.hidden = false;
  // 同一路線の連続区間をひとつの「乗り区間」にまとめる。
  const legs = [];
  for (let i = 0; i < result.edges.length; i++) {
    const edge = network.edges[result.edges[i]];
    const route = network.routes.get(edge.route) || { name: edge.route };
    const last = legs[legs.length - 1];
    if (last && last.route === edge.route) {
      last.minutes += edge.minutes;
      last.to = nodeName(result.nodes[i + 1]);
      last.count += 1;
    } else {
      legs.push({ route: edge.route, name: route.name, minutes: edge.minutes, from: nodeName(result.nodes[i]), to: nodeName(result.nodes[i + 1]), count: 1 });
    }
  }
  $('plan-summary').textContent = `合計乗車 ${result.minutes}分（${result.edges.length}区間）· 探索 ${result.explored.toLocaleString()}ノード · ${result.status === 'optimal' ? '予算内全探索ずみ' : '時間打ち切りの暫定解'}`;
  $('plan-legs').replaceChildren(...legs.map(leg => {
    const item = document.createElement('li');
    item.textContent = `${leg.name}: ${leg.from} → ${leg.to}（${Math.round(leg.minutes)}分・${leg.count}区間）`;
    return item;
  }));
  $('depart').disabled = state.started;
  $('advance').disabled = !state.started || state.cursor >= result.edges.length;
  updateProgress();
  drawPlan();
}

function updateProgress() {
  const now = state.started ? new Date(state.simulationStart + (state.elapsed + state.detour) * 60000).toLocaleString() : '未出発';
  $('progress').textContent = `${now} / 現在: ${state.current ? nodeName(state.current) : '—'} / 乗車 ${state.elapsed.toFixed(1)}分 / 道草 ${state.detour}分 / 残り ${(remainingBudget() || 0).toFixed(1)}分`;
  const choices = [];
  if (state.started) network.edges.forEach((e, i) => {
    if ((e.u === state.current || e.v === state.current) && !usedSegment(i)) {
      choices.push(new Option(`${network.routes.get(e.route).name} → ${nodeName(e.u === state.current ? e.v : e.u)} (${e.minutes}分) [${i}]`, i));
    }
  });
  $('actual-edge').replaceChildren(...choices);
}

async function doPlan() {
  if (state.busy) return;
  const start = state.current || resolveName($('start').value);
  const goal = resolveName($('goal').value);
  if (!start || !goal) { status('出発と到着の駅名を入力してください（入力候補から選択）。'); return; }
  const budget = remainingBudget();
  if (budget === null || budget < 0 || budget > 1440 || (!budget && start !== goal)) { status('締切は現在から24時間以内に設定してください。'); return; }
  state.busy = true;
  document.querySelectorAll('button, input, select').forEach(e => { e.disabled = true; });
  status('探索中…（数秒かかることがあります）');
  try {
    const response = await fetch('/api/route/plan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start, goal, budget_minutes: budget, used: [...state.used], allowed_routes: checkedRoutes() }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = await response.json();
    $('notes').textContent = (result.notes || []).join(' ');
    if (!result.nodes || !result.nodes.length) {
      state.plan = null;
      status(result.status === 'time_limit'
        ? '制限時間内に有効な経路を見つけられませんでした。予算を伸ばすか経路フィルターを減らしてください。'
        : '条件を満たす経路がありません（予算不足・未使用区間の不足など）。');
      renderPlan();
      return;
    }
    state.plan = result;
    state.cursor = 0;
    if (!state.current) state.current = start;
    status('経路が決まりました。「出発」→「次の駅へ」で進めます。');
    renderPlan();
  } catch (error) {
    status(`計画に失敗しました: ${error.message}`);
  } finally {
    state.busy = false;
    document.querySelectorAll('button, input, select').forEach(e => { e.disabled = false; });
    $('start').disabled = state.started;
    $('arrive-by').disabled = state.started;
    renderPlan();
  }
}

function depart() {
  if (!state.plan || state.started) return;
  const budget = remainingBudget();
  if (budget === null || state.plan.minutes > budget) {
    invalidatePlan(); status('出発までに残り時間が減りました。再計画してください。'); return;
  }
  state.budgetTotal = budget;
  state.simulationStart = Date.now();
  state.started = true;
  state.current = state.plan.nodes[0];
  $('start').disabled = true;
  $('arrive-by').disabled = true;
  drawCurrent(); renderPlan();
  status('手動シミュレーション開始。乗車・道草の分数で時計が進みます。');
}

function ride(index) {
  const edge = network.edges[index];
  if (!state.started || !edge || usedSegment(index) || (edge.u !== state.current && edge.v !== state.current)) {
    status('現在地に接続する未使用区間を選んでください。'); return false;
  }
  state.used.add(index);
  state.elapsed = Math.round((state.elapsed + edge.minutes) * 10) / 10;
  state.current = edge.u === state.current ? edge.v : edge.u;
  drawCurrent(); updateProgress();
  return true;
}

function advance() {
  const result = state.plan;
  if (!result || state.cursor >= result.edges.length) return;
  if (!ride(result.edges[state.cursor])) return;
  state.cursor += 1;
  renderPlan();
  if (state.cursor === result.edges.length) status('ゴール到着。使用済み区間は保持されています。');
}

function relocate() {
  const node = resolveName($('here').value);
  if (!node) { status('駅・停留所名を入力してください。'); return; }
  // テスト用位置訂正。実際に乗った区間は「予定外乗車」で先に記録する。
  state.current = node;
  invalidatePlan(); drawCurrent();
  status(`位置を訂正しました。使用済み${state.used.size}区間を保持。未記録の移動は自動推測しません。`);
}

function takeDetour() {
  const minutes = Number($('detour-minutes').value);
  if (!state.started || !Number.isFinite(minutes) || minutes <= 0 || minutes > 1440) {
    status('出発後に道草の分数（0超〜1440）を指定してください。'); return;
  }
  state.detour += minutes;
  invalidatePlan();
  status('道草終了・再乗車。締切予算を減らし、使用済み区間を保持しました。再計画してください。');
}

function applyFilterStyles() {
  invalidatePlan();
  const allowed = new Set(checkedRoutes() || [...routeLayers.keys()]);
  for (const [routeId, layer] of routeLayers) {
    layer.setStyle({ opacity: allowed.has(routeId) ? .45 : .07 });
  }
}

function buildFilters() {
  const groups = [['rail', '電車（JR・東京メトロ）'], ['bus', '都営バス']];
  const container = $('filters');
  for (const [mode, title] of groups) {
    const routes = [...network.routes.values()].filter(route => route.mode === mode).sort((a, b) => a.name.localeCompare(b.name, 'ja'));
    if (!routes.length) continue;
    const details = document.createElement('details');
    details.open = mode === 'rail';
    const summary = document.createElement('summary');
    const toggle = document.createElement('button');
    toggle.className = 'mini';
    toggle.textContent = '全解除 / 全選択';
    toggle.onclick = (event) => {
      event.preventDefault();
      const boxes = [...details.querySelectorAll('input')];
      const enable = !boxes.every(box => box.checked);
      boxes.forEach(box => { box.checked = enable; });
      applyFilterStyles();
    };
    summary.textContent = `${title}（${routes.length}）`;
    summary.appendChild(toggle);
    details.appendChild(summary);
    for (const route of routes) {
      const label = document.createElement('label');
      label.className = 'route-option';
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = true;
      box.dataset.route = route.id;
      box.onchange = applyFilterStyles;
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = route.color;
      label.append(box, swatch, document.createTextNode(route.name));
      details.appendChild(label);
    }
    container.appendChild(details);
  }
}

async function init() {
  map = L.map('map', { preferCanvas: true });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 19, updateWhenIdle: true,
  }).addTo(map);
  const now = new Date(Date.now() + 2 * 3600 * 1000);
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  $('arrive-by').value = now.toISOString().slice(0, 16);
  const payload = await (await fetch('/api/route/network')).json();
  for (const node of payload.nodes) {
    network.nodes.set(node.id, node);
    if (!network.byName.has(node.name) || node.modes.includes('rail')) network.byName.set(node.name, node);
  }
  network.edges = payload.edges;
  for (const route of payload.routes) network.routes.set(route.id, route);
  $('notes').textContent = (payload.notes || []).join(' ');

  const segments = new Map();
  for (const edge of network.edges) {
    const a = network.nodes.get(edge.u);
    const b = network.nodes.get(edge.v);
    if (!a || !b) continue;
    if (!segments.has(edge.route)) segments.set(edge.route, []);
    segments.get(edge.route).push([[a.lat, a.lon], [b.lat, b.lon]]);
  }
  const bounds = [];
  for (const [routeId, lines] of segments) {
    const route = network.routes.get(routeId) || {};
    const layer = L.polyline(lines, { color: route.color || '#888888', weight: 2.5, opacity: .45 }).addTo(map);
    layer.bindTooltip(route.name || routeId, { sticky: true });
    routeLayers.set(routeId, layer);
    bounds.push(...lines.flat());
  }
  for (const node of network.nodes.values()) {
    if (!node.modes.includes('rail')) continue;
    bounds.push([node.lat, node.lon]);
    L.circleMarker([node.lat, node.lon], { radius: 2.5, color: '#dfe3e8', weight: 1, opacity: .7, fillOpacity: .9 })
      .addTo(map).bindTooltip(node.name).on('click', () => pickStation(node));
  }
  if (bounds.length) map.fitBounds(L.latLngBounds(bounds).pad(.05));
  buildFilters();

  const datalist = $('stations');
  datalist.append(...[...network.byName.values()]
    .sort((a, b) => a.name.localeCompare(b.name, 'ja'))
    .map(node => new Option(node.name)));
  $('plan').onclick = doPlan;
  $('depart').onclick = depart;
  $('advance').onclick = advance;
  $('relocate').onclick = relocate;
  $('detour').onclick = takeDetour;
  $('actual-ride').onclick = () => {
    if ($('actual-edge').value !== '' && ride(Number($('actual-edge').value))) {
      invalidatePlan(); status('予定外の乗車を記録しました。現在地から再計画してください。');
    }
  };
  $('goal').onchange = invalidatePlan;
  $('arrive-by').onchange = invalidatePlan;
  $('start').onchange = () => { if (!state.started) state.current = null; invalidatePlan(); };
  $('reset').onclick = () => { if (confirm('使用済み区間も消して最初から始めますか？')) location.reload(); };
  updateProgress();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => init().catch(e => status(`初期化失敗: ${e.message}`)));
else init().catch(e => status(`初期化失敗: ${e.message}`));
