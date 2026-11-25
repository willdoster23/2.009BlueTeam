'use strict';
// Real automatic.js logic with isolated controls/storage: no Pi or motor calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

async function fixture(storedRoute = null) {
  const elements = new Map(), records = [], reservations = new Set(), moves = [];
  function el(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', disabled: false, hidden: false, open: false, textContent: '', dataset: {},
      classList: {toggle() {}}, validity: {valid: true},
      reportValidity: () => true, addEventListener() {}, setAttribute() {}, removeAttribute() {},
      get valueAsNumber() { return this.value === '' ? NaN : Number(this.value); }
    });
    return elements.get(id);
  }
  for (const [id, value] of Object.entries({inspectionMode: 'hardware', brickTapZone: 'center',
    routeDirection: 'right', routeCoverage: 'count', routeRows: '2', routeColumns: '1', routeXSpacing: '7',
    routeYSpacing: '2.7', routeYLimit: '100', inspectionTaps: 'burst', yDistance: '2.7'})) el(id).value = value;
  const roof = {bootId: 'roof-1', xAxis: {mode: 'live', ready: true, maxJogInches: 6},
    yAxis: {mode: 'live', ready: true, upReady: true, downReady: true,
      routeLimitSupported: true, maxJogInches: 12, direction: 1, rawCounts: 0, counts: 0},
    position: {x: 0, y: 0, xInches: 0, yInches: 0, yZeroed: true},
    relays: {mode: 'live-four', ready: true, autoArm: true, enabled: true, busy: false}};
  const state = {roof: {data: roof}, robot: {reachable: true, data: {tap: {available: true, mode: 'idle'}}}};
  let reference = {x: 0, y: 0, xStep: 7, yStep: 2.7, bootId: roof.bootId, yZero: 0, wallId: 'wall-1'};
  let savedRoute, number = 0, inspectionBusy = false;
  const api = {
    state: () => state, movementBusy: () => false, tapping: () => false, positionMeasured: () => true,
    busy: value => { inspectionBusy = value; },
    setInspectionMode: value => { el('inspectionMode').value = value; },
    moveY: async (direction, inches) => {
      moves.push({direction, inches});
      return api.update(roof.position.yInches + (direction === 'down' ? inches : -inches));
    },
    moveX: async (direction, inches) => {
      moves.push({direction, inches});
      roof.position = {...roof.position, xInches: roof.position.xInches + (direction === 'left' ? -inches : inches)};
      return roof.position;
    },
    stopX: async () => {}, stopY: async () => {}, winch: async () => {},
    tap: async () => ({audioWav: 'test-audio', peakPwm: 210, peakMs: 50}),
    update(y) {
      roof.position = {...roof.position, y: y * 100, yInches: y};
      roof.yAxis.rawCounts = roof.yAxis.counts = y * 100;
      return roof.position;
    }
  };
  const storage = {
    id: () => 'test-' + ++number, route: async () => storedRoute, validRoute: () => true,
    saveRoute: async value => { savedRoute = structuredClone(value); },
    reference: async () => reference, saveReference: async value => { reference = value; },
    brickKey: brick => `${brick.source}:${brick.course}:${brick.column}`,
    reserveTap: async (brick, zone) => {
      const key = storage.brickKey(brick) + ':' + zone;
      if (reservations.has(key)) return false;
      reservations.add(key); return true;
    },
    releaseTap: async () => {}, positionMetadata: position => ({y_inches: position.yInches}),
    save: async (_, width, height, position, inspection) => {
      const record = {id: ++number, ...position, ...inspection}; records.push(record); return {record};
    }
  };
  const context = vm.createContext({document: {body: {dataset: {preview: 'false'}},
    getElementById: el, addEventListener() {}}, blueInspection: api, brickStorage: storage,
    brickTapAnalysis: {calculate: () => null, baseline: () => null},
    brickCamera: {ready: () => true, capture: async () => ({blob: {}, width: 1, height: 1})},
    addEventListener() {}, dispatchEvent() {}, Event: class {}, AbortController, DOMException,
    setTimeout, clearTimeout, console});
  context.window = context;
  const source = fs.readFileSync(path.join(__dirname, '../app/automatic.js'), 'utf8');
  vm.runInContext(source.replace(/\}\)\(\);\s*$/,
    'window.routeTest = {visit, measuredBrick, routeSummary};\n})();'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {el, api, roof, moves, records, storage, test: context.routeTest,
    get busy() { return inspectionBusy; }, get savedRoute() { return savedRoute; }};
}

function visit(f, {start = 0, target = 2.7, direction = 1, limit = 100} = {}) {
  f.api.update(start);
  return f.test.visit({row: 1, column: 0, axis: 'y', direction},
    {rows: 2, columns: 1, xStep: 7, yStep: target, limit},
    {bootId: f.roof.bootId, xInches: 0, yInches: 0}, true, new AbortController().signal);
}

test('automatic Y accepts half an inch short or past in either direction', async () => {
  for (const direction of [-1, 1]) for (const offset of [-.5, .5]) {
    const f = await fixture();
    let calls = 0;
    f.api.moveY = async () => { calls++; return f.api.update(2.7 + offset); };
    const position = await visit(f, {start: direction === 1 ? 0 : 5.4, direction});
    assert.equal(position.yInches, 2.7 + offset);
    assert.equal(calls, 1, 'No extra correction request inside the half-inch window');
  }
});

test('a stop already within half an inch does not move or skip its capture', async () => {
  for (const start of [2.2, 2.4, 3.0, 3.2]) {
    const f = await fixture();
    const position = await visit(f, {start});
    assert.equal(position.yInches, start);
    assert.equal(f.moves.length, 0);
  }
});

test('long route moves use measured remaining travel across early-stopping jogs', async () => {
  const f = await fixture();
  f.api.moveY = async (_, inches) => {
    f.moves.push(inches); return f.api.update(f.roof.position.yInches + inches - .5);
  };
  const position = await visit(f, {target: 24});
  assert.equal(position.yInches, 23.5);
  assert.deepEqual(f.moves, [12, 12, 1]);
});

test('larger Y error and no progress still fail, and the lower limit stays strict', async () => {
  for (const actual of [3.2001, 0]) {
    const f = await fixture(); f.api.moveY = async () => f.api.update(actual);
    await assert.rejects(visit(f), /half-inch target window/);
  }
  const f = await fixture(); f.api.moveY = async () => f.api.update(3);
  await assert.rejects(visit(f, {limit: 2.7}), /lower Y limit/);
});

test('brick assignment uses the planned row when actual Y is within half an inch', async () => {
  const f = await fixture(), ref = {x: 0, y: 0, xStep: 7, yStep: 1};
  assert.equal(f.test.measuredBrick({xInches: 0, yInches: 1.5}, ref, 1).course, 2);
  assert.throws(() => f.test.measuredBrick({xInches: 0, yInches: 1.5001}, ref, 1), /between mapped bricks/);
});

test('after failure settings unlock, resave permits a fresh run, and saved taps remain', async () => {
  const f = await fixture();
  await f.el('routeSave').onclick();
  f.api.moveY = async () => { throw new Error('Simulated move failure'); };
  await f.el('routeRun').onclick();
  assert.equal(f.records.length, 1);
  assert.equal(f.busy, false);
  assert.equal(f.el('routeSave').disabled, false);
  assert.equal(f.el('routeRows').disabled, false);
  assert.equal(f.el('routePlanner').open, true);
  assert.equal(f.el('routeRun').disabled, true, 'Failure requires saving before a new run');
  assert.match(f.el('routeSaveStatus').textContent, /Adjust the settings and Save Route/);
  const original = f.records[0];
  f.el('routeRows').value = '3';
  f.el('routeYSpacing').value = '5.4';  // Sampling every second brick preserves the wall grid.
  await f.el('routeSave').onclick();
  assert.equal(f.savedRoute.inputs.routeYSpacing, '5.4');
  assert.equal(f.el('routeRun').disabled, false);
  f.api.moveY = async (_, inches) => f.api.update(f.roof.position.yInches + inches - .5);
  await f.el('routeRun').onclick();
  assert.match(f.el('routeProgress').textContent, /^Route complete/);
  assert.equal(f.records.length, 3, 'The previously saved location is skipped, not tapped again');
  assert.equal(f.records[0], original, 'Earlier evidence is preserved');
  assert.equal(f.busy, false);
});

test('resaving after a controller change preserves the wall grid with new route spacing', async () => {
  const f = await fixture();
  f.roof.bootId = 'roof-2';
  f.el('routeYSpacing').value = '5.4';
  await f.el('routeSave').onclick();
  const reference = await f.storage.reference();
  assert.equal(reference.bootId, 'roof-2');
  assert.equal(reference.yStep, 2.7);
  assert.equal(reference.wallId, 'wall-1');
  assert.equal(f.savedRoute.inputs.routeYSpacing, '5.4');
  assert.equal(f.el('routeRun').disabled, false);
});

test('both starting sides run the full serpentine route in the selected X direction', async () => {
  for (const direction of ['right', 'left']) {
    const f = await fixture();
    f.el('routeDirection').value = direction;
    f.el('routeColumns').value = '3';
    await f.el('routeSave').onclick();
    assert.equal(f.savedRoute.inputs.routeDirection, direction);
    assert.match(f.el('routeSummary').textContent, direction === 'left'
      ? /Start right → move left/ : /Start left → move right/);
    await f.el('routeRun').onclick();
    assert.match(f.el('routeProgress').textContent, /^Route complete/);
    assert.equal(f.records.length, 6);
    assert.deepEqual(f.moves.map(move => move.direction),
      ['down', direction, direction, 'up', direction, direction, 'down']);
    const sign = direction === 'left' ? -1 : 1;
    assert.equal(f.roof.position.xInches, sign * 14);
    assert.deepEqual(f.records.map(record => record.brick.column),
      [1, 1, 1 + sign, 1 + sign, 1 + 2 * sign, 1 + 2 * sign]);
    assert.deepEqual(f.records.map(record => record.brick.course), [1, 2, 2, 1, 1, 2]);
    assert.equal(f.el('routeDirection').disabled, false);
    f.el('routeDirection').value = direction === 'left' ? 'right' : 'left';
    f.test.routeSummary();
    assert.equal(f.el('routeRun').disabled, true, 'Changed direction must be saved before running');
  }
});

test('saved starting side restores, and legacy routes retain left-to-right travel', async () => {
  const f = await fixture();
  f.el('routeDirection').value = 'left';
  await f.el('routeSave').onclick();
  const restored = await fixture(f.savedRoute);
  assert.equal(restored.el('routeDirection').value, 'left');
  assert.equal(restored.el('routeRun').disabled, false);
  const legacy = structuredClone(f.savedRoute);
  delete legacy.inputs.routeDirection;
  const old = await fixture(legacy);
  assert.equal(old.el('routeDirection').value, 'right');
  assert.equal(old.el('routeRun').disabled, false);
});

test('stored routes validate both directions and legacy backups, rejecting invalid direction', () => {
  const context = vm.createContext({window: {}, localStorage: {getItem: () => null}});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/storage.js'), 'utf8'), context);
  const valid = context.window.brickStorage.validRoute;
  const route = {version: 1, hardware: true, zone: 'center', inputs: {
    routeCoverage: 'count', routeRows: '2', routeColumns: '3', routeXSpacing: '7',
    routeYSpacing: '2.7', routeYLimit: '100'}};
  assert.equal(valid(route), true);
  for (const direction of ['right', 'left']) {
    route.inputs.routeDirection = direction;
    assert.equal(valid(route), true);
  }
  for (const direction of ['', 'up', null, 1]) {
    route.inputs.routeDirection = direction;
    assert.equal(valid(route), false);
  }
});

test('X zero shifts the wall reference without changing brick identities or repeating taps', async () => {
  const f = await fixture();
  f.roof.xAxis.coordinateEpoch = 'x-1';
  f.roof.xAxis.zeroInches = 0;
  await f.el('routeSave').onclick();
  await f.el('routeRun').onclick();
  const old = [...f.records];
  f.roof.xAxis.zeroInches = 7;
  f.roof.position = {...f.roof.position, xInches: -7};
  f.api.update(0);
  await f.el('routeSave').onclick();
  const reference = await f.storage.reference();
  assert.equal(reference.x, -7);
  assert.equal(reference.xZero, 7);
  assert.equal(reference.wallId, 'wall-1');
  await f.el('routeRun').onclick();
  assert.match(f.el('routeProgress').textContent, /^Route complete/);
  assert.equal(f.records.length, old.length, 'Zeroing X must not allow the same bricks to be tapped again');
  assert.deepEqual(f.records, old, 'Earlier evidence and its recorded coordinates stay unchanged');
});

test('an X reference change during the route stops before another motion or capture', async () => {
  const f = await fixture();
  await f.el('routeSave').onclick();
  f.api.tap = async () => {
    f.roof.xAxis.zeroInches = 7;
    return {audioWav: 'test-audio'};
  };
  await f.el('routeRun').onclick();
  assert.match(f.el('routeProgress').textContent, /X reference changed/);
  assert.equal(f.moves.length, 0);
  assert.equal(f.records.length, 1);
});
