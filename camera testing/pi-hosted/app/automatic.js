'use strict';
(() => {
  const el = id => document.getElementById(id);
  const ui = window.blueInspection;
  const storage = window.brickStorage;
  const scoring = window.brickTapAnalysis;
  const isPreview = document.body.dataset.preview === 'true';
  const routeInputs = ['routeCoverage', 'routeRows', 'routeColumns', 'routeXSpacing', 'routeYSpacing', 'routeYLimit'];
  let controller = null, lastSaved = null, reviewed = null, saving = null, editing = false;
  let tapRequestId = null, sampleY = 0;
  const pending = [];
  const reviewButtons = [['reviewSimilar', 'good'], ['reviewDifferent', 'review'],
    ['reviewVisual', 'visual'], ['reviewUnknown', 'unknown'], ['reviewSaveNotes', null]];
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

  function check(signal) {
    signal.throwIfAborted();
  }

  function yZero(roof) {
    const axis = roof.yAxis;
    return [axis?.rawCounts, axis?.counts, axis?.direction].every(Number.isFinite)
      ? axis.rawCounts - axis.counts * axis.direction : null;
  }

  function measuredBrick(position, reference) {
    const x = (position.xInches - reference.x) / reference.xStep;
    const y = (position.yInches - reference.y) / reference.yStep;
    const column = Math.round(x), course = Math.round(y);
    if (Math.abs(x - column) > .37 || Math.abs(y - course) > .29) {
      throw new Error('Position is between mapped bricks. Check the tapper and the wall reference before tapping.');
    }
    return {source: 'measured', course: course + 1, column: column + 1};
  }

  function routeSettings(originY, live, validate = false) {
    const toLimit = el('routeCoverage').value === 'limit';
    const columns = el('routeColumns').valueAsNumber;
    el('routeRowsField').hidden = toLimit;
    el('routeYLimit').required = live || toLimit;
    el('routeXSpacing').required = columns > 1;
    const fields = toLimit ? routeInputs.filter(id => id !== 'routeRows') : routeInputs;
    if (!fields.every(id => validate ? el(id).reportValidity() : el(id).validity.valid)) {
      return {error: 'Enter the brick spacing, coverage, and lowest Y for the route.'};
    }
    const limit = el('routeYLimit').value === '' ? null : el('routeYLimit').valueAsNumber;
    if (limit !== null && originY > limit) return {error: 'Move above the lowest Y before starting the route.'};
    const yStep = el('routeYSpacing').valueAsNumber;
    const fits = limit === null ? Infinity : Math.floor((limit - originY) / yStep + 1e-9) + 1;
    const requested = el('routeRows').valueAsNumber;
    const rows = toLimit ? fits : Math.min(requested, fits);
    if (!Number.isSafeInteger(rows * columns)) return {error: 'Check the route dimensions and spacing.'};
    return {rows, columns, xStep: columns > 1 ? el('routeXSpacing').valueAsNumber : 0, yStep, limit,
      shortened: !toLimit && rows < requested};
  }

  function routeSummary() {
    if (controller) return;
    const hardware = el('inspectionMode').value === 'hardware';
    el('routeRun').textContent = hardware ? 'Run route on rig' : 'Simulate route';
    const originY = Math.max(0, ui.state()?.roof.data?.position?.yInches ?? 0);
    const {error, rows, columns, xStep, yStep, shortened} = routeSettings(originY, hardware && !isPreview);
    const summary = error ?? `${rows * columns} stops · ${rows} per column · Y ${(originY + yStep * (rows - 1)).toFixed(2)} in at the last course · ${(xStep * (columns - 1)).toFixed(2)} in right.`
      + (shortened ? ' Course count reduced to fit lowest Y.' : '');
    if (el('routeSummary').textContent !== summary) el('routeSummary').textContent = summary;
    el('setupRouteSummary').textContent = summary;
  }

  function* routeStops({rows, columns}) {
    for (let column = 0; column < columns; column++) {
      for (let index = 0; index < rows; index++) {
        const row = column % 2 === 0 ? index : rows - index - 1;
        const axis = index > 0 ? 'y' : column > 0 ? 'x' : null;
        const direction = axis === 'x' || column % 2 === 0 ? 1 : -1;
        yield {row, column, axis, direction};
      }
    }
  }

  function samplePosition(xInches, yInches) {
    return {x: Math.round(xInches * 400), y: Math.round(yInches * 100),
      xInches, yInches, yZeroed: true, source: 'simulated-rooftop'};
  }

  async function visit(step, plan, origin, controlled, signal) {
    const targetX = origin.xInches + step.column * plan.xStep;
    const targetY = origin.yInches + step.row * plan.yStep;
    if (!controlled) {
      await pause(180);
      check(signal);
      return samplePosition(targetX, targetY);
    }
    const roof = ui.state()?.roof.data;
    let position = roof?.position;
    if (!position || roof.bootId !== origin.bootId) throw new Error('The rooftop controller changed during the route.');
    if (!['xInches', 'yInches'].every(key => Number.isFinite(position[key]))) {
      throw new Error('The encoders did not report a position for this stop.');
    }
    if (plan.limit !== null && position.yInches > plan.limit) throw new Error('Route stopped at the lower Y limit.');
    if (!step.axis) return position;
    const target = step.axis === 'x' ? targetX : targetY;
    const key = step.axis + 'Inches';
    const remaining = () => (target - position[key]) * step.direction;
    if (remaining() < -0.1) return null;
    const max = roof[step.axis + 'Axis'].maxJogInches ?? (step.axis === 'x' ? 6 : 12);
    const jogs = Math.ceil(remaining() / max);
    const notes = new Set();
    for (let jog = 0; jog < jogs && remaining() >= 0.1; jog++) {
      check(signal);
      const inches = Math.min(remaining(), max);
      position = await (step.axis === 'x'
        ? ui.moveX('right', inches, {signal})
        : ui.moveY(step.direction > 0 ? 'down' : 'up', inches, {signal, routeLimitInches: plan.limit}));
      if (!Number.isFinite(position?.[key])) throw new Error('The encoders did not report a position after moving.');
      if (plan.limit !== null && position.yInches > plan.limit) throw new Error('Route stopped at the lower Y limit.');
      if (position.yMoveNote) notes.add(position.yMoveNote);
    }
    if (Math.abs(remaining()) > 0.1) {
      notes.add(`Measured ${step.axis.toUpperCase()} is ${Math.abs(remaining()).toFixed(2)} in from the planned stop.`);
    }
    return notes.size ? {...position, yMoveNote: [...notes].join(' ')} : position;
  }

  async function captureStop(position, {live, burst, signal, status, label, brick, zone, stationary = false}) {
    if (!['x', 'y', 'xInches', 'yInches'].every(key => Number.isFinite(position?.[key]))) {
      throw new Error('The encoders did not report a position for this stop.');
    }
    check(signal);
    status.textContent = label + ' · capturing photo';
    const frame = await window.brickCamera.capture();
    check(signal);
    if (brick?.source === 'preview' && (frame.nearJoint || storage.brickKey(frame.brick) !== storage.brickKey(brick))) {
      throw new Error('Route paused before tapping: the simulated camera sees a joint or a different brick. Check slip and alignment.');
    }
    if (brick) {
      if (!await storage.reserveTap(brick, zone)) return false;
      if (signal.aborted) {
        await storage.releaseTap(brick, zone);
        check(signal);
      }
    }
    const recordings = [];
    let peakPwm, peakMs;
    if (burst) {
      status.textContent = label + (live ? ' · recording three taps…' : ' · simulating three taps…');
      if (live || isPreview) {
        tapRequestId = storage.id();
        let result;
        try {
          result = await ui.tap('burst', tapRequestId);
          check(signal);
          if (!result.audioWav) throw new Error(result.audioError || 'No recording was received for this burst.');
        } catch (error) {
          if (!brick || signal.aborted) throw error;
          throw new Error(`${error.message} This tap may have fired. The ${zone} location is held to prevent a repeat.`);
        }
        recordings.push(result.audioWav);
        peakPwm = result.peakPwm;
        peakMs = result.peakMs;
        tapRequestId = null;
      } else {
        await pause(2500);
        check(signal);
      }
    }
    pending.push({frame, position, simulated: !live, burst, recordings, brick, zone, peakPwm, peakMs, stationary});
    await savePending();
    return true;
  }

  async function run(isRoute = false, stationary = false) {
    if (controller) return;
    if (!isRoute && !el('yDistance').reportValidity()) return;
    const burst = stationary || isRoute || el('inspectionTaps').value === 'burst';
    const live = !isPreview && el('inspectionMode').value === 'hardware';
    const controlled = live || isPreview;
    const state = ui.state();
    const roof = state?.roof.data;
    const robot = state?.robot.reachable ? state.robot.data : null;
    const status = el(stationary ? 'stationaryStatus' : 'inspectionStatus');
    if (ui.movementBusy() || ui.tapping()) {
      status.textContent = 'Wait for the current operation to finish.';
      return;
    }
    if (stationary && controlled && (!ui.positionMeasured() || !roof?.position.yZeroed)) {
      status.textContent = 'Set Y zero here at your roof reference before recording a photo on the map. Tap once and Tap 3 times can be used without Y zero.';
      return;
    }
    if (!window.brickCamera.ready() || !roof) {
      status.textContent = 'Wait for live video and a fresh position.';
      return;
    }
    if (controlled && !stationary && !(roof.xAxis?.ready && roof.xAxis.mode === 'live' &&
      (roof.yAxis?.downReady ?? roof.yAxis?.ready) && roof.yAxis.mode === 'live')) {
      status.textContent = 'Connect the encoders and set Y zero.';
      return;
    }
    const origin = {bootId: roof.bootId, xInches: roof.position.xInches ?? 0,
      yInches: Math.max(0, roof.position.yInches ?? 0)};
    const plan = isRoute && !stationary ? routeSettings(origin.yInches, live, true) : null;
    if (plan?.error) {
      status.textContent = plan.error;
      return;
    }
    if (controlled && plan && plan.limit !== null && !roof.yAxis.routeLimitSupported) {
      status.textContent = 'Update the rooftop controller to use the lower Y limit.';
      return;
    }
    if (controlled && plan?.columns > 1 && plan.rows > 1 && !(roof.yAxis.upReady ?? roof.yAxis.ready)) {
      status.textContent = 'The route needs measured Up and Down.';
      return;
    }
    if (controlled && plan && roof.position.yInches < -0.075) {
      status.textContent = 'Start at or below Y zero for a route.';
      return;
    }
    if (controlled && ((!stationary && (roof.relays.mode !== 'live-four' || !roof.relays.ready ||
      !(roof.relays.autoArm || roof.relays.enabled) || roof.relays.busy || roof.relays.action)) ||
      roof.poweroff?.scheduled || roof.poweroff?.robotScheduled ||
      (burst && (!robot?.tap?.available || robot.tap.mode !== 'idle' || robot.poweroff?.scheduled)))) {
      status.textContent = roof.relays.autoArm ? 'Wait for the controls to be idle.'
        : 'Arm the winch and wait for the controls to be idle.';
      return;
    }

    controller = new AbortController();
    const {signal} = controller;
    const inputs = ['inspectionRun', 'inspectionMode', 'inspectionTaps', 'routeRun', 'routeUseSample',
      'brickTapHere', 'brickTapZone', 'routeReference', 'previewSlip',
      'routeSampleSpan', 'routeSampleIntervals', ...routeInputs];
    for (const id of inputs) el(id).disabled = true;
    el('inspectionCancel').hidden = false;
    ui.busy(true);
    try {
      const options = {live, burst, signal, status};
      if (stationary) {
        const position = roof.position;
        const reference = live ? await storage.reference() : null;
        let brick = isPreview ? window.brickCamera.previewIdentity(position) : null;
        if (reference?.bootId === roof.bootId && reference.yZero === yZero(roof)) {
          const x = (position.xInches - reference.x) / reference.xStep;
          const y = (position.yInches - reference.y) / reference.yStep;
          if (Math.abs(x - Math.round(x)) <= .37 && Math.abs(y - Math.round(y)) <= .29) {
            brick = measuredBrick(position, reference);
          }
        }
        const zone = el('brickTapZone').value;
        const captured = await captureStop(position, {...options, label: 'Current position', brick, zone, stationary: true});
        status.textContent = !captured ? `This brick’s ${zone} location is already recorded or held.`
          : pending.length ? 'Recording complete. Use Retry saving to save the photo and audio.'
          : 'Photo and three-tap recording saved at the current position. Open the brick map or Audio recordings.';
      } else if (plan) {
        const reference = live ? await storage.reference() : null;
        if (live && (!reference || reference.bootId !== roof.bootId || reference.yZero !== yZero(roof))) {
          status.textContent = 'Align with the first brick and set the wall reference before running mapped taps.';
          return;
        }
        if (live && !stationary && (Math.abs(plan.yStep - reference.yStep) > .001
            || (plan.columns > 1 && Math.abs(plan.xStep - reference.xStep) > .001))) {
          status.textContent = 'Use the spacing saved with this wall reference.';
          return;
        }
        const zone = el('brickTapZone').value;
        let saved = 0;
        let skipped = 0;
        let duplicates = 0;
        let warnings = 0;
        let unscored = 0;
        for (const step of routeStops(plan)) {
          check(signal);
          const label = `Column ${step.column + 1}, brick ${step.row + 1}`;
          status.textContent = label + ' · moving';
          const position = await visit(step, plan, origin, controlled, signal);
          if (!position) {
            skipped++;
            continue;
          }
          if (position.yMoveNote) warnings++;
          const target = {xInches: origin.xInches + plan.xStep * step.column,
            yInches: origin.yInches + plan.yStep * step.row};
          const brick = isPreview ? window.brickCamera.previewIdentity(target) : reference ? measuredBrick(target, reference) : null;
          if (reference && storage.brickKey(measuredBrick(position, reference)) !== storage.brickKey(brick)) {
            throw new Error('Route paused before tapping: the measured position differs from the planned brick.');
          }
          const captured = await captureStop(position, {...options, label, brick, zone});
          if (!captured) {
            duplicates++;
            continue;
          }
          if (pending.length) throw new Error('Route paused because a capture could not be saved. Use Retry saving.');
          saved++;
          if (controlled && !lastSaved.record.tap_analysis) unscored++;
        }
        const notes = [
          skipped ? `${skipped} past planned positions skipped` : '',
          duplicates ? `${duplicates} ${zone} ${duplicates === 1 ? 'location' : 'locations'} already recorded or held` : '',
          warnings ? `${warnings} captures with movement notes` : '',
          unscored ? `${unscored} recordings could not be scored` : '',
          controlled && saved > unscored && !scoring.baseline() ? 'Record a baseline to show scores' : ''
        ].filter(Boolean);
        status.textContent = `Route complete · ${saved} captures saved` + (notes.length ? ' · ' + notes.join(' · ') : '.');
      } else {
        const inches = el('yDistance').valueAsNumber;
        status.textContent = live ? `Lowering ${inches} in…` : `Simulating a ${inches} in descent…`;
        let position;
        if (controlled || roof.yAxis?.mode === 'simulation') {
          if (!controlled) await pause(950);
          position = await ui.moveY('down', inches, {signal});
        } else {
          await pause(950);
          sampleY += inches;
          position = samplePosition(2.5, sampleY);
        }
        await captureStop(position, {...options, label: 'Stopped'});
        status.textContent = pending.length
          ? 'Step complete. You can run the next step or retry saving.'
          : position.yMoveNote ? `Cell saved. ${position.yMoveNote}` : 'Cell saved on this computer. Ready for the next step.';
      }
    } catch (error) {
      status.textContent = signal.aborted ? 'Inspection canceled.' : error.message;
      if (controlled && !stationary) await stopMotion();
    } finally {
      controller = null;
      tapRequestId = null;
      el('inspectionCancel').hidden = true;
      ui.busy(false);
      for (const id of inputs) el(id).disabled = false;
      routeSummary();
    }
  }

  function openReview() {
    if (!lastSaved || editing) return;
    reviewed = lastSaved;
    const {record} = reviewed;
    el('reviewPreview').src = record.url;
    el('reviewPosition').textContent = `X ${record.x_inches.toFixed(2)} in · Y ${record.y_inches.toFixed(2)} in`;
    storage.showTapRecordings(el('reviewAudio'), record);
    el('reviewRecording').hidden = !record.tap_audio.length;
    const review = reviewed;
    scoring.show(el('reviewTapAnalysis'), record, async (analysis, options) => {
      review.record = await storage.updateTapAnalysis(record.id, analysis, options);
      window.dispatchEvent(new Event('brickcaptured'));
    });
    el('reviewPrompt').textContent = record.tap_burst && record.simulated
      ? 'This cell uses a simulated tap recording.'
      : '';
    el('reviewSimilar').hidden = el('reviewDifferent').hidden = !record.tap_burst;
    el('reviewVisual').hidden = record.tap_burst;
    el('reviewNotes').value = record.notes || '';
    el('reviewFlag').checked = !!record.flagged;
    updateReview();
    el('inspectionReview').hidden = false;
    el('inspectionReview').scrollIntoView({block: 'nearest'});
  }

  function updateReview() {
    const {record} = reviewed;
    for (const [button, status] of reviewButtons) {
      if (status) el(button).setAttribute('aria-pressed', String(record.status === status));
    }
    el('reviewStatus').textContent = '';
  }

  function savePending() {
    if (saving) return saving;
    const retry = el('inspectionRetrySave');
    const feedback = el('inspectionSaveStatus');
    retry.disabled = true;
    saving = (async () => {
      while (pending.length) {
        const step = pending[0];
        const {blob, width, height} = step.frame;
        const status = step.burst ? 'unknown' : 'visual';
        const {record} = await storage.save(blob, width, height, {...step.position, status}, {
          ...storage.positionMetadata(step.position),
          tap_audio: step.recordings,
          tap_burst: step.burst,
          ...(Number.isInteger(step.peakPwm) ? {tap_pwm: step.peakPwm} : {}),
          ...(Number.isInteger(step.peakMs) ? {tap_peak_ms: step.peakMs} : {}),
          tap_analysis: step.recordings.length ? scoring.calculate(step.recordings[0]) : null,
          classification_source: step.burst ? 'pending' : 'tap-review',
          simulated: step.simulated,
          ...(step.brick ? {brick: step.brick, tap_zone: step.zone, tap_point: step.frame.tapPoint,
            brick_crop: step.frame.brickCrop} : {})
        });
        pending.shift();
        lastSaved = {record};
        if (step.stationary && step.recordings.length) {
          await ui.addTapRecording(step.recordings[0], record.id, true, {peakPwm: step.peakPwm, peakMs: step.peakMs});
        }
        el('inspectionReviewLast').hidden = false;
        window.dispatchEvent(new Event('brickcaptured'));
      }
      feedback.textContent = '';
    })().catch(error => {
      feedback.textContent = `Unsaved steps in this tab: ${pending.length}. ${error.message}`;
    }).finally(() => {
      saving = null;
      retry.hidden = !pending.length;
      retry.disabled = false;
    });
    return saving;
  }

  async function saveReview(result) {
    if (!reviewed || editing) return;
    editing = true;
    for (const [button] of reviewButtons) el(button).disabled = true;
    el('reviewClose').disabled = true;
    const {record} = reviewed;
    try {
      reviewed.record = await storage.label(record.id, result ?? record.status, {
        classification_source: result
          ? (result === 'unknown' ? 'pending' : 'tap-review')
          : record.classification_source,
        notes: el('reviewNotes').value.trim(),
        flagged: el('reviewFlag').checked || result === 'review'
      });
      el('reviewFlag').checked = reviewed.record.flagged;
      updateReview();
      el('reviewStatus').textContent = 'Review saved.';
      window.dispatchEvent(new Event('brickcaptured'));
    } catch (error) {
      el('reviewStatus').textContent = 'Could not save review. ' + error.message;
    } finally {
      editing = false;
      for (const [button] of reviewButtons) el(button).disabled = false;
      el('reviewClose').disabled = false;
    }
  }

  function closeReview() {
    if (editing) return;
    reviewed = null;
    el('inspectionReview').hidden = true;
    el('reviewPreview').removeAttribute('src');
    storage.clearAudio(el('reviewAudio'));
    scoring.show(el('reviewTapAnalysis'), {});
  }

  function stopBurst() {
    if (tapRequestId) ui.tap('stop', tapRequestId, true).catch(() => {});
  }

  function stopMotion() {
    stopBurst();
    return Promise.allSettled([ui.stopX(), ui.stopY(), ui.winch('stop')]);
  }

  function cancel() {
    if (!controller) return;
    controller.abort();
    if (isPreview || el('inspectionMode').value === 'hardware') stopMotion();
  }

  el('inspectionRun').onclick = () => run();
  el('routeRun').onclick = () => run(true);
  el('brickTapHere').onclick = () => run(true, true);
  el('routeReference').onclick = async () => {
    const roof = ui.state()?.roof.data;
    const feedback = el('brickReferenceStatus');
    if (!roof || !ui.positionMeasured() || !roof.position.yZeroed || ui.movementBusy() || controller || yZero(roof) === null) {
      feedback.textContent = 'Set roof zero and stop at the first brick before saving the reference.';
      return;
    }
    const x = el('routeXSpacing'), y = el('routeYSpacing');
    x.required = true;
    if (!x.reportValidity() || !y.reportValidity()) return;
    try {
      const previous = await storage.reference();
      if (previous && (Math.abs(previous.xStep - x.valueAsNumber) > .001 || Math.abs(previous.yStep - y.valueAsNumber) > .001)) {
        feedback.textContent = 'Use the original brick spacing when aligning to this job’s first brick.';
        return;
      }
      await storage.saveReference({x: roof.position.xInches, y: roof.position.yInches, xStep: x.valueAsNumber,
        yStep: y.valueAsNumber, bootId: roof.bootId, yZero: yZero(roof), wallId: previous?.wallId ?? storage.id()});
      feedback.textContent = roof.yAxis?.estimated
        ? 'First brick saved with estimated Y. Confirm brick alignment in the camera before each tap.'
        : 'First brick saved. Return to this same brick if the roof reference changes.';
    } catch (error) {
      feedback.textContent = error.message;
    }
  };
  if (isPreview) {
    el('routeXSpacing').value = '7';
    el('routeYSpacing').value = '2.7';
    el('routeReference').hidden = true;
    el('brickReferenceStatus').textContent = 'The sample wall supplies its own brick reference.';
  } else {
    el('brickReferenceStatus').textContent = 'Align the tapper with your first brick, then set the wall reference. Position the tapper before choosing Center or Edge.';
  }
  for (const id of routeInputs) el(id).addEventListener('input', routeSummary);
  el('routeUseSample').onclick = () => {
    const span = el('routeSampleSpan');
    const intervals = el('routeSampleIntervals');
    if (!span.reportValidity() || !intervals.reportValidity()) return;
    const spacing = el('routeYSpacing');
    spacing.valueAsNumber = span.valueAsNumber / intervals.valueAsNumber;
    routeSummary();
    spacing.reportValidity();
  };
  window.addEventListener('inspectionposition', routeSummary);
  routeSummary();
  el('inspectionCancel').onclick = cancel;
  el('inspectionReviewLast').onclick = openReview;
  el('inspectionRetrySave').onclick = savePending;
  el('reviewClose').onclick = closeReview;
  el('inspectionMode').onchange = () => {
    routeSummary();
    el('hardwareDistanceNotice').hidden = el('inspectionMode').value !== 'hardware';
    el('inspectionStatus').textContent = el('inspectionMode').value === 'hardware'
      ? (ui.state()?.roof.data?.relays.autoArm
        ? 'Hardware mode · each step sends Start before lowering.'
        : 'Hardware mode · arm the winch before running a step.')
      : 'Ready to simulate the next step.';
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancel(); });
  window.addEventListener('pagehide', cancel);
  window.addEventListener('inspectionstop', () => {
    if (!controller) return;
    controller.abort();
    ui.stopX().catch(() => {});
    stopBurst();
  });
  for (const [button, status] of reviewButtons) {
    el(button).onclick = () => saveReview(status);
  }
})();
