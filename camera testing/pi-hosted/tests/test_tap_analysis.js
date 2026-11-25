'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const algorithm = 'fft21600-pre50ms-v2';
const legacy = () => ({times: [.25, .75, 1.25], mel: Array(64).fill(77), source: 'entered'});

function fixture(savedBaseline) {
  const elements = new Map(), calls = [];
  const saved = new Map(savedBaseline ? [['brick-mapper-tap-baseline', JSON.stringify(savedBaseline)]] : []);
  const el = id => {
    if (!elements.has(id)) elements.set(id, {textContent: '', files: []});
    return elements.get(id);
  };
  const score = {algorithm, validTimes: times => Array.isArray(times) && times.length === 3 && times.every(Number.isFinite),
    samplesFromBase64: () => new Float64Array(96000),
    burstTimes: () => ({times: [.25, .75, 1.25], source: 'detected'}),
    spectrum: (_samples, times) => { calls.push([...times]); return Array(64).fill(2); },
    compare: (a, b) => a.reduce((sum, value, i) => sum + Math.abs(value - b[i]), 0) / 64};
  const context = {window: {brickTapScore: score, brickStorage: {assertCurrentJob() {}},
    dispatchEvent() {}, addEventListener() {}}, document: {getElementById: el}, Event: class {},
    localStorage: {getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/tap-analysis.js'), 'utf8'), context);
  return {analysis: context.window.brickTapAnalysis, saved, calls, el};
}

test('old baseline stays saved but is never compared with new spectra', () => {
  const baseline = {name: 'Old baseline', mel: Array(64).fill(0)};
  const f = fixture(baseline);
  assert.equal(f.analysis.baseline(), null);
  assert.equal(f.analysis.result({tap_analysis: {...legacy(), algorithm}}), null);
  assert.equal(f.saved.get('brick-mapper-tap-baseline'), JSON.stringify(baseline));
  assert.match(f.el('baselineStatus').textContent, /updated scoring/);
});

test('old recordings are recalculated with their original entered timestamps', () => {
  const f = fixture();
  const old = legacy();
  old.times = [.2, .7, 1.2];
  const current = f.analysis.calculate('test-wave', old);
  assert.equal(current.algorithm, algorithm);
  assert.equal(current.source, 'entered');
  assert.deepEqual(f.calls, [old.times]);
  assert.ok(current.mel.every(value => value === 2));
  assert.equal(f.analysis.validAnalysis(old), false);
  assert.equal(f.analysis.validAnalysis(old, true), true);
  assert.equal(f.analysis.validAnalysis({...old, mel: ['invalid']}, true), false);
});

test('using a legacy recording as baseline computes new features first', () => {
  const f = fixture();
  f.analysis.useRecordingBaseline('test-wave', 'Reference', legacy());
  const baseline = JSON.parse(f.saved.get('brick-mapper-tap-baseline'));
  assert.equal(baseline.algorithm, algorithm);
  assert.ok(baseline.mel.every(value => value === 2));
  assert.equal(f.calls.length, 1);
});

test('the requested cutoff classifies below / at / above 5', () => {
  const f = fixture({name: 'Reference', mel: Array(64).fill(0), algorithm});
  for (const [value, status] of [[4.5, 'good'], [5, 'unknown'], [5.5, 'bad']]) {
    const result = f.analysis.result({tap_analysis: {...legacy(), mel: Array(64).fill(value), algorithm}});
    assert.equal(result.value, value);
    assert.equal(result.status, status);
  }
  assert.equal(f.analysis.result({tap_analysis: legacy()}), null);
});

test('background migration replaces old spectra but preserves a current concurrent edit', async () => {
  let record = {id: 'photo-1', blob: {}, tap_analysis: legacy()};
  const context = {window: {}, localStorage: {getItem: () => null}, setTimeout, clearTimeout,
    URL: {createObjectURL: () => 'blob:fixture'}, indexedDB: {open() {
      const request = {};
      queueMicrotask(() => {
        request.result = {transaction() {
          const transaction = {objectStore: () => ({get() {
            const read = {};
            queueMicrotask(() => {read.result = record; read.onsuccess(); queueMicrotask(() => transaction.oncomplete());});
            return read;
          }, put(value) {record = value;}})};
          return transaction;
        }};
        request.onsuccess();
      });
      return request;
    }}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/storage.js'), 'utf8'), context);
  const storage = context.window.brickStorage;
  const current = {...legacy(), algorithm, mel: Array(64).fill(2)};
  await storage.updateTapAnalysis('photo-1', current, {ifMissing: true});
  assert.equal(record.tap_analysis, current);
  const entered = {...current, times: [.3, .8, 1.3]};
  record = {...record, tap_analysis: entered};
  await storage.updateTapAnalysis('photo-1', current, {ifMissing: true});
  assert.equal(record.tap_analysis, entered);
});
