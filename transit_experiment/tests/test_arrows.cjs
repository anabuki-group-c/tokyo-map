const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/transit_experiment/static/trains.js'), 'utf8'), context);
function draw(heading, drawing = true) {
  const calls = [];
  const ctx = {};
  for (const method of ['save', 'translate', 'rotate', 'beginPath', 'moveTo', 'lineTo', 'closePath', 'restore']) {
    ctx[method] = (...args) => calls.push([method, ...args]);
  }
  context.drawTrainArrow.call({options: {heading}, _point: {x: 100, y: 200}, _empty: () => false,
    _renderer: {_drawing: drawing, _ctx: ctx, _fillStroke: () => calls.push(['paint'])}});
  return calls;
}
assert.deepEqual(draw(90).find(c => c[0] === 'rotate'), ['rotate', Math.PI / 2]);
assert.deepEqual(draw(0).find(c => c[0] === 'moveTo'), ['moveTo', 0, -10]);
assert.deepEqual(draw(null).find(c => c[0] === 'moveTo'), ['moveTo', 0, -6]);
assert.deepEqual(draw(0, false), []);
assert.equal(draw(180).at(-1)[0], 'paint');
console.log('Canvas arrow drawing tests passed');
