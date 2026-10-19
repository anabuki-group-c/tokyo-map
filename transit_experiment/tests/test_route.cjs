// Runnable UI state checks with minimal DOM, no browser/dependencies/network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, { value: '', textContent: '', disabled: false,
    replaceChildren() {}, hidden: false });
  return elements.get(id);
};
const ctx = vm.createContext({ console, Date, Map, Set, Number, Math,
  document: { getElementById: get, readyState: 'loading', addEventListener() {},
    createElement: () => ({}), querySelectorAll: () => [] },
  Option: function(text, value) { this.text = text; this.value = value; },
});
vm.runInContext(fs.readFileSync('src/transit_experiment/static/route.js', 'utf8'), ctx);
vm.runInContext(`
  const realRenderPlan = renderPlan;
  let renders = 0, draws = 0;
  drawCurrent = () => {}; drawPlan = () => { draws++; }; renderPlan = () => { renders++; };
  network.routes.set('r', { name: 'r' });
  for (const id of ['A', 'B', 'C', 'G']) {
    network.nodes.set(id, { id, name: id }); network.byName.set(id, { id, name: id });
  }
  network.edges = [
    { u:'A', v:'B', minutes:5, route:'r' },
    { u:'B', v:'C', minutes:5, route:'r' },
    { u:'C', v:'A', minutes:5, route:'r' },
    { u:'A', v:'G', minutes:5, route:'r' },
    { u:'B', v:'A', minutes:7, route:'r' },
  ];
  state.started = true; state.current = 'A'; state.budgetTotal = 60; state.simulationStart = Date.now();
  state.plan = { edges:[0,1,2,3], nodes:['A','B','C','A','G'] };
  advance(); advance(); advance(); advance();
`, ctx);
assert.equal(vm.runInContext('state.current', ctx), 'G');
assert.equal(vm.runInContext('state.used.size', ctx), 4);
assert.equal(vm.runInContext('state.elapsed', ctx), 20);
assert.equal(vm.runInContext('usedSegment(4)', ctx), true);
get('detour-minutes').value = '30';
vm.runInContext('takeDetour()', ctx);
assert.equal(vm.runInContext('remainingBudget()', ctx), 10);
assert.equal(vm.runInContext('state.elapsed', ctx), 20); // no reward for lunch
assert.equal(vm.runInContext('state.used.size', ctx), 4);
get('here').value = 'C';
vm.runInContext('relocate()', ctx);
assert.equal(vm.runInContext('state.current', ctx), 'C');
assert.equal(vm.runInContext('state.used.size', ctx), 4);
get('start').value = 'A'; get('goal').value = 'G';
let request;
ctx.fetch = async (_url, options) => {
  request = JSON.parse(options.body);
  return { ok: true, json: async () => ({ status:'infeasible', nodes:[], edges:[], notes:[] }) };
};
(async () => {
  await vm.runInContext('doPlan()', ctx);
  assert.equal(request.start, 'C');
  assert.equal(request.budget_minutes, 10);
  assert.deepEqual(request.used, [0, 1, 2, 3]);
  get('detour-minutes').value = '11';
  vm.runInContext('takeDetour()', ctx);
  assert.equal(vm.runInContext('remainingBudget()', ctx), -1);
  request = null;
  await vm.runInContext('doPlan()', ctx);
  assert.equal(request, null); // past deadline cannot be accepted as a zero-length solution
  assert.match(get('status').textContent, /実績は保持/);
  // A successful request renders once, and an upper-bound optimum is not labelled exhaustive.
  vm.runInContext(`
    state.started = false; state.current = 'C'; state.used.clear();
    state.elapsed = 0; state.detour = 0;
  `, ctx);
  get('start').value = '';
  vm.runInContext("pickStation(network.nodes.get('A'))", ctx);
  assert.equal(vm.runInContext('state.current', ctx), null);
  get('arrive-by').value = new Date(Date.now() + 3600000).toISOString();
  ctx.fetch = async (_url, options) => {
    request = JSON.parse(options.body);
    return { ok: true, json: async () => ({ status:'optimal', minutes:5,
      nodes:['A','G'], edges:[3], explored:1, notes:[] }) };
  };
  vm.runInContext('renders = 0', ctx);
  await vm.runInContext('doPlan()', ctx);
  assert.equal(request.start, 'A');
  assert.equal(vm.runInContext('renders', ctx), 1);
  assert.equal(vm.runInContext('draws', ctx), 1);
  vm.runInContext('realRenderPlan()', ctx);
  assert.match(get('plan-summary').textContent, /予算上界到達/);
  assert.equal(get('depart').disabled, false);
  assert.equal(get('advance').disabled, true);
  vm.runInContext('depart(); advance()', ctx);
  assert.equal(vm.runInContext('state.current', ctx), 'G');
  assert.equal(vm.runInContext('state.elapsed', ctx), 5);
  assert.equal(vm.runInContext('state.used.has(3)', ctx), true);
  assert.equal(vm.runInContext('draws', ctx), 1); // advancing does not rebuild/refit the unchanged polyline
  vm.runInContext('realRenderPlan()', ctx);
  assert.equal(get('advance').disabled, true);

  // Filters distinguish all routes from none; failed HTTP restores the UI lock.
  const boxes = [{ checked: false, dataset: { route: 'r' }, disabled: false }];
  ctx.document.querySelectorAll = selector => selector.startsWith('#filters') ? boxes : [...elements.values(), ...boxes];
  await vm.runInContext('doPlan()', ctx);
  assert.deepEqual(request.allowed_routes, []);
  boxes[0].checked = true;
  await vm.runInContext('doPlan()', ctx);
  assert.equal(request.allowed_routes, null);
  ctx.fetch = async () => ({ ok: false, status: 400 });
  await vm.runInContext('doPlan()', ctx);
  assert.equal(vm.runInContext('state.busy', ctx), false);
  assert.equal(get('plan').disabled, false);
  assert.equal(get('start').disabled, true);
  assert.equal(get('arrive-by').disabled, true);
  assert.match(get('status').textContent, /HTTP 400/);
  console.log('route UI state checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
