'use strict';
// Namespace IDs: a JR route and a bus route may have the same source ID.
function combineNetworks(entries) {
  const routes = [], features = [], stations = [];
  for (const [mode, network] of entries) {
    for (const route of network.routes) routes.push({...route, id: `${mode}:${route.id}`, name: `${modeNames[mode]} / ${route.name}`});
    for (const feature of network.geojson.features) features.push({...feature, properties: {...feature.properties, route: `${mode}:${feature.properties.route}`}});
    for (const station of network.stations || []) stations.push({...station, id: `${mode}:${station.id}`, routes: station.routes.map(id => `${mode}:${id}`)});
  }
  return {routes, stations, geojson: {type: 'FeatureCollection', features},
    ready: entries.some(([, network]) => network.ready), source: entries.map(([, network]) => network.source || '').join(' / '),
    message: entries.map(([, network]) => network.message || '').join(' ')};
}
function combinePositions(entries, cap = 600, now = Date.now()) {
  const queues = [], messages = [], warnings = [], times = [];
  let totalVisible = 0, limited = false;
  for (const [mode, result] of entries) {
    const updated = Date.parse(result.generated_at);
    if (!Number.isFinite(updated) || now - updated > 180000) {
      messages.push(`${modeNames[mode]}は更新待ちです。`); continue;
    }
    times.push(updated);
    queues.push((result.pins || []).map(pin => ({...pin, route: `${mode}:${pin.route}`, source_mode: mode,
      basis: pin.basis || result.basis, assumptions: result.assumptions})));
    totalVisible += result.total_visible ?? result.pins.length;
    limited ||= !!result.limited;
    if (result.message) messages.push(`${modeNames[mode]}: ${result.message}`);
    if (result.warning) warnings.push(`${modeNames[mode]}: ${result.warning}`);
  }
  // Round-robin prevents the bus fleet from starving JR (or vice versa) at the cap.
  const pins = [];
  for (let index = 0; queues.some(queue => index < queue.length) && pins.length < cap; index++) {
    for (const queue of queues) if (queue[index] && pins.length < cap) pins.push(queue[index]);
  }
  return {pins, ready: true, generated_at: new Date(times.length ? Math.min(...times) : now).toISOString(),
    total_visible: totalVisible, limited: limited || totalVisible > pins.length, message: messages.join(' '), warning: warnings.join(' ')};
}
async function fetchExtraNetwork(mode, signal) {
  if (mode !== 'all') {
    let network = extraNetworks.get(mode);
    if (!network || !network.ready) {
      const response = await fetch(`/api/extra-network?mode=${mode}`, {signal});
      if (!response.ok) throw new Error('Network unavailable');
      network = await response.json(); extraNetworks.set(mode, network);
    }
    return network;
  }
  const modes = ['jr', 'bus'];
  const results = await Promise.allSettled(modes.map(mode => fetchExtraNetwork(mode, signal)));
  const entries = results.flatMap((result, i) => result.status === 'fulfilled' ? [[modes[i], result.value]] : []);
  if (!entries.length) throw new Error('Networks unavailable');
  const previous = extraNetworks.get('all');
  if (previous && previous.entries.length === entries.length && entries.every(([mode, network], i) => previous.entries[i][0] === mode && previous.entries[i][1] === network)) return previous;
  const merged = {...combineNetworks(entries), entries};
  if (entries.length !== modes.length) merged.warning = '一部の交通機関の路線を読み込めません。';
  extraNetworks.set('all', merged);
  return merged;
}
async function fetchExtraPositions(mode, selection, bounds, signal) {
  if (mode !== 'all') {
    const query = new URLSearchParams({mode, bounds});
    if (selection) query.set('route', selection);
    const response = await fetch('/api/extra-positions?' + query, {signal});
    if (!response.ok) throw new Error('Positions unavailable');
    return response.json();
  }
  const selectedMode = selection.split(':')[0];
  const modes = selection ? [selectedMode].filter(mode => ['jr', 'bus'].includes(mode)) : ['jr', 'bus'];
  const results = await Promise.allSettled(modes.map(mode => fetchExtraPositions(mode, selection ? selection.slice(mode.length + 1) : '', bounds, signal)));
  const entries = results.flatMap((result, i) => result.status === 'fulfilled' ? [[modes[i], result.value]] : []);
  if (!entries.length) throw new Error('Positions unavailable');
  const merged = combinePositions(entries);
  if (entries.length !== modes.length) merged.warning += ' 一部の交通機関の位置を読み込めません。';
  return merged;
}
function showAllBadge() {
  const badge = document.getElementById('estimate-badge');
  badge.hidden = false;
  badge.textContent = 'すべて / 矢印は推定位置 · JRは簡易モデルの場合あり';
}
