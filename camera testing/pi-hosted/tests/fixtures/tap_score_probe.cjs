'use strict';
// Offline numerical probe: reads fixtures only and never connects to a Pi.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/tap-score.js'), 'utf8'), context);
const score = context.window.brickTapScore;
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const started = performance.now();
const spectra = request.cases.map(item => {
  const samples = score.decodeWav(fs.readFileSync(item.wav));
  return score.spectrum(samples, item.times);
});
process.stdout.write(JSON.stringify({algorithm: score.algorithm, spectra,
  scores: spectra.map(mel => score.compare(spectra[0], mel)),
  elapsedMs: performance.now() - started}));
