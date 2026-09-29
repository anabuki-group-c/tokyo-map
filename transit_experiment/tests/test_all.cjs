const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = name => fs.readFileSync(path.join(__dirname, '../src/transit_experiment/static', name), 'utf8');
const context = vm.createContext({modeNames: {jr: 'JR', bus: 'バス'}, extraNetworks: new Map(), URLSearchParams});
vm.runInContext(source('all.js'), context);
const network = {ready: true, routes: [{id: 'same', name: '路線'}],
  stations: [{id: 's', routes: ['same']}],
  geojson: {type: 'FeatureCollection', features: [{type: 'Feature', properties: {route: 'same'}, geometry: {type: 'MultiLineString', coordinates: []}}]}};
const merged = context.combineNetworks([['jr', network], ['bus', network]]);
assert.equal(merged.routes[0].id, 'jr:same');
assert.equal(merged.routes[1].id, 'bus:same');
assert.equal(merged.stations[1].routes[0], 'bus:same');
assert.equal(network.routes[0].id, 'same');
const now = Date.now();
const data = {generated_at: new Date(now).toISOString(), pins: Array.from({length: 500}, (_, i) => ({route: 'same', id: i})), total_visible: 500};
const combined = context.combinePositions([['jr', {...data, basis: 'headway_model', assumptions: {speed_kmh: 45}}], ['bus', data]], 600, now);
assert.equal(combined.pins.length, 600);
assert.equal(combined.pins.filter(p => p.source_mode === 'jr').length, 300);
assert.equal(combined.pins.filter(p => p.source_mode === 'bus').length, 300);
assert.equal(combined.pins[0].basis, 'headway_model');
assert.equal(combined.pins[0].assumptions.speed_kmh, 45);
assert.equal(combined.limited, true);
const freshOnly = context.combinePositions([['jr', {...data, generated_at: new Date(now - 200000).toISOString()}], ['bus', data]], 600, now);
assert.equal(freshOnly.pins.length, 500);
assert.match(freshOnly.message, /更新待ち/);

async function main() {
  const urls = [];
  context.fetch = async url => {
    urls.push(url);
    if (url.includes('mode=bus')) throw new Error('bus unavailable');
    return {ok: true, json: async () => url.includes('network') ? network : data};
  };
  const partialNetwork = await context.fetchExtraNetwork('all', undefined);
  assert.equal(partialNetwork.routes.length, 1);
  assert.match(partialNetwork.warning, /一部/);
  const partialPositions = await context.fetchExtraPositions('all', '', '139,35,140,36', undefined);
  assert.equal(partialPositions.pins.length, 500);
  assert.match(partialPositions.warning, /一部/);
  await context.fetchExtraPositions('all', 'jr:route:branch', '139,35,140,36', undefined);
  assert.match(urls.at(-1), /route=route%3Abranch/);
  assert.ok(urls.every(url => !url.includes('mode=all')));

  const nodes = new Map();
  const node = id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); };
  let added = 0, removed = 0, metroRendered = 0, extraRequested = 0;
  const ui = vm.createContext({
    document: {getElementById: node}, clearTimeout, setTimeout,
    map: {removeLayer: () => removed++},
    layers: [{layer: {addTo: () => added++}}],
    trainGroup: {clearLayers() {}}, selected: null, routes: [],
    updateFlow() {}, select() {}, renderTrainPins: () => metroRendered++,
  });
  vm.runInContext(source('all.js'), ui);
  vm.runInContext(source('extras.js'), ui);
  ui.requestExtra = () => extraRequested++;
  node('transport-mode').value = 'all';
  await ui.switchTransport();
  assert.equal(node('metro-panel').hidden, false);
  assert.equal(node('extra-panel').hidden, false);
  assert.equal(node('motion').hidden, true);
  assert.equal(added, 1);
  assert.ok(metroRendered > 0 && extraRequested > 0);
  node('transport-mode').value = 'bus';
  await ui.switchTransport();
  assert.equal(node('metro-panel').hidden, true);
  assert.equal(removed, 1);
  node('transport-mode').value = 'metro';
  await ui.switchTransport();
  assert.equal(node('extra-panel').hidden, true);
  assert.equal(added, 2);
  console.log('All-mode merge, cap, partial-failure and switching tests passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
