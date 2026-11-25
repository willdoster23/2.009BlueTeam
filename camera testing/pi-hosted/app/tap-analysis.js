'use strict';
(() => {
  const score = window.brickTapScore;
  const storage = window.brickStorage;
  const baselineKey = 'brick-mapper-tap-baseline';
  const cutoff = 5;
  const panels = new WeakMap();
  const validMel = values => Array.isArray(values) && values.length === 64 && values.every(Number.isFinite);
  const currentAlgorithm = value => value?.algorithm === score.algorithm;
  const validBaseline = value => value && currentAlgorithm(value) && typeof value.name === 'string' && value.name.length <= 256 && validMel(value.mel);
  const validAnalysis = (value, allowLegacy = false) => value && (allowLegacy || currentAlgorithm(value)) && score.validTimes(value.times) && validMel(value.mel);
  const el = id => document.getElementById(id);

  function readBaseline() {
    try {
      const value = JSON.parse(localStorage.getItem(baselineKey));
      return validBaseline(value) ? value : null;
    } catch {
      return null;
    }
  }

  let baseline = readBaseline();
  function announceBaseline() {
    el('baselineStatus').textContent = baseline ? 'Baseline: ' + baseline.name
      : 'Record or import a baseline for the updated scoring calculation.';
    el('recordBaseline').textContent = baseline ? 'Record new baseline' : 'Record baseline';
    window.dispatchEvent(new Event('brickanalysischange'));
  }

  function setBaseline(value) {
    storage.assertCurrentJob();
    if (!validBaseline(value)) throw new Error('The baseline recording is invalid.');
    localStorage.setItem(baselineKey, JSON.stringify(value));
    baseline = value;
    announceBaseline();
  }

  function clearBaseline() {
    localStorage.removeItem(baselineKey);
    baseline = null;
  }

  function restoreBaseline(value) {
    if (!baseline && validBaseline(value)) setBaseline(value);
  }

  function calculate(wav, previous) {
    try {
      const samples = score.samplesFromBase64(wav);
      const timing = score.validTimes(previous?.times, samples.length)
        ? {times: previous.times, source: previous.source} : score.burstTimes(samples);
      return timing ? {...timing, mel: score.spectrum(samples, timing.times), algorithm: score.algorithm} : null;
    } catch {
      return null;
    }
  }

  function useRecordingBaseline(wav, name, analysis = calculate(wav)) {
    if (!validAnalysis(analysis)) analysis = calculate(wav, analysis);
    if (!validAnalysis(analysis)) throw new Error('The recording was silent, incomplete, or unreadable. Record the baseline again.');
    setBaseline({name: name.slice(0, 256), mel: analysis.mel, algorithm: score.algorithm});
  }

  function result(record) {
    if (!baseline || !validAnalysis(record.tap_analysis)) return null;
    const value = score.compare(baseline.mel, record.tap_analysis.mel);
    return {value, status: value < cutoff ? 'good' : value > cutoff ? 'bad' : 'unknown'};
  }

  function scoreText(comparison) {
    const {status, value} = comparison;
    const label = {good: 'Similar', bad: 'Dissimilar', unknown: 'At cutoff'}[status];
    return `${label} · difference ${value.toFixed(2)}`;
  }

  function show(container, record, persist) {
    panels.get(container)?.abort();
    container.replaceChildren();
    const wav = record.tap_burst && record.tap_audio?.[0];
    container.hidden = !wav;
    if (!wav) return;

    const controller = new AbortController();
    panels.set(container, controller);
    let analysis = validAnalysis(record.tap_analysis) ? record.tap_analysis : calculate(wav, record.tap_analysis);
    const summary = document.createElement('p');
    summary.className = 'tap-score-result';
    const actions = document.createElement('div');
    actions.className = 'tap-score-actions';
    const button = label => {
      const item = document.createElement('button');
      item.type = 'button';
      item.textContent = label;
      return item;
    };
    const csv = button('Download impact CSV');
    const useBaseline = button('Use as baseline');
    const details = document.createElement('details');
    const title = document.createElement('summary');
    title.textContent = 'Timing and CSV';
    const timingStatus = document.createElement('p');
    timingStatus.className = 'hint';
    const form = document.createElement('form');
    const inputs = Array.from({length: 3}, (_, i) => {
      const label = document.createElement('label');
      label.textContent = `Tap ${i + 1} (s)`;
      const input = document.createElement('input');
      input.type = 'number';
      input.min = '0';
      input.step = 'any';
      input.required = true;
      input.value = analysis?.times[i] ?? '';
      label.append(input);
      form.append(label);
      return input;
    });
    const update = button('Save impact times');
    update.type = 'submit';
    form.append(update);
    const feedback = document.createElement('p');
    feedback.className = 'hint';
    feedback.setAttribute('role', 'status');

    function paint() {
      const comparison = result({tap_analysis: analysis});
      summary.textContent = comparison ? scoreText(comparison)
        : !analysis ? 'This recording is silent, incomplete, or unreadable.' : 'Add a baseline to score this brick.';
      summary.dataset.result = comparison?.status ?? '';
      csv.disabled = useBaseline.disabled = !analysis;
      timingStatus.textContent = analysis?.source === 'scheduled'
        ? 'Times estimated from the recorded start and 2 Hz cadence.' : '';
    }
    csv.onclick = () => {
      const file = new Blob([score.impactCsv(analysis.times)], {type: 'text/csv;charset=utf-8'});
      const base = record.name.replace(/\.png$/i, '-three-taps').replace(/\.wav$/i, '');
      storage.download(file, base + '.csv');
    };
    useBaseline.onclick = () => {
      try {
        const name = Number.isFinite(record.x) && Number.isFinite(record.y)
          ? `Brick at X ${record.x_inches ?? record.x}, Y ${record.y_inches ?? record.y}`
          : record.name;
        useRecordingBaseline(wav, name, analysis);
        feedback.textContent = 'This recording is now the baseline.';
      } catch (error) {
        feedback.textContent = error.message;
      }
    };
    form.onsubmit = async event => {
      event.preventDefault();
      update.disabled = true;
      try {
        const samples = score.samplesFromBase64(wav);
        const times = inputs.map(input => input.valueAsNumber);
        const next = {times, mel: score.spectrum(samples, times), source: 'entered', algorithm: score.algorithm};
        if (persist) await persist(next);
        analysis = next;
        paint();
        feedback.textContent = 'Impact times saved.';
      } catch (error) {
        feedback.textContent = error.message;
      } finally {
        update.disabled = false;
      }
    };
    actions.append(useBaseline);
    details.append(title, timingStatus, csv, form);
    container.append(summary, actions, details, feedback);
    window.addEventListener('brickanalysischange', paint, {signal: controller.signal});
    paint();
    if (persist && analysis && !validAnalysis(record.tap_analysis)) {
      persist(analysis, {ifMissing: true}).catch(error => { feedback.textContent = error.message; });
    }
  }

  const input = el('baselineFiles');
  el('chooseBaseline').onclick = () => input.click();
  input.onchange = async () => {
    try {
      const files = Array.from(input.files);
      const wav = files.find(file => /\.wav$/i.test(file.name));
      const csv = files.find(file => /\.csv$/i.test(file.name));
      if (files.length !== 2 || !wav || !csv) throw new Error('Select one baseline WAV and its CSV together.');
      if (wav.size > 8 * 1024 * 1024 || csv.size > 65536) throw new Error('The baseline files are too large.');
      const samples = score.decodeWav(new Uint8Array(await wav.arrayBuffer()));
      const times = score.readTimes(await csv.text(), samples.length);
      setBaseline({name: wav.name.slice(0, 256), mel: score.spectrum(samples, times), algorithm: score.algorithm});
      el('baselineFileFeedback').textContent = 'Baseline imported.';
    } catch (error) {
      el('baselineFileFeedback').textContent = error.message;
    } finally {
      input.value = '';
    }
  };
  window.addEventListener('storage', event => {
    if (event.key !== baselineKey) return;
    baseline = readBaseline();
    announceBaseline();
  });
  announceBaseline();

  window.brickTapAnalysis = {calculate, show, result, scoreText, useRecordingBaseline, validAnalysis, clearBaseline, restoreBaseline, baseline: () => baseline};
})();
