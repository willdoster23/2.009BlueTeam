'use strict';
(() => {
  const el = id => document.getElementById(id);
  const token = '__PHOTO_TOKEN__';
  const isPreview = document.body.dataset.preview === 'true';
  let latest = null, receivedAt = 0, polling = false, pendingWinchCommands = 0;
  let tapId = null, tapBusy = false, tapStopping = false, pendingTapSaves = 0;
  let inspectionBusy = false;
  let xRequestId = null, yRequestId = null, ySending = false;
  const distanceControllers = {};
  let initialModeSet = false;
  let renewing = false;
  let xMessage = '', yMessage = '';
  let tuningDirty = false, tuningSaving = false, tuningSaved = '';
  let poweroffBusy = false, poweroffScheduled = false, poweroffMessage = '';
  const scheduledMessage = 'Both shutdowns are scheduled. Wait for both Pis to finish shutting down before unplugging.';
  const positionReady = () => !!latest?.roof.reachable && performance.now() - receivedAt < 1800;
  const xBusy = () => !!distanceControllers.x || !!xRequestId || !!(positionReady() && latest.roof.data.xAxis?.moving);
  window.blueInspection = {
    positionReady,
    positionMeasured: () => positionReady() && ['x', 'y', 'xInches', 'yInches']
      .every(key => Number.isFinite(latest.roof.data.position?.[key])),
    state: () => positionReady() ? latest : null,
    movementBusy: () => xBusy() || !!distanceControllers.y || ySending || !!(positionReady() &&
      (latest.roof.data.yAxis?.moving || latest.roof.data.relays?.busy ||
        ['up', 'down'].includes(latest.roof.data.relays?.action))),
    tapping: () => tapBusy || tapStopping,
    savingRecordings: () => pendingTapSaves > 0,
    moveX,
    stopX,
    moveY,
    stopY,
    inspectionActive: () => inspectionBusy,
    busy(value) { inspectionBusy = value; paint(); window.dispatchEvent(new Event('inspectionposition')); },
    winch: command,
    tap: tapRequest,
    addTapRecording: addTapClip
  };

  function indicator(id, text, online) {
    el(id).textContent = text;
    el(id).className = online ? 'online' : 'offline';
  }

  function paint() {
    const fresh = performance.now() - receivedAt < 1800;
    const roof = fresh && latest?.roof.reachable ? latest.roof.data : null;
    const robot = fresh && latest?.robot.reachable ? latest.robot.data : null;
    const linked = !!roof?.exchange.connected;
    const robotStopping = !!roof?.poweroff?.robotScheduled || !!robot?.poweroff?.scheduled;
    const stopping = poweroffBusy || poweroffScheduled || !!roof?.poweroff?.scheduled || robotStopping;
    el('poweroff').disabled = poweroffBusy || poweroffScheduled || !roof?.poweroff?.available ||
      !!roof.poweroff.scheduled || (!roof.poweroff.robotScheduled && !robot?.poweroff?.available);
    el('poweroffStatus').textContent = poweroffMessage || (roof?.poweroff?.scheduled ? scheduledMessage
      : robotStopping ? 'Robot shutdown is scheduled. The rooftop Pi is still online.'
      : roof && !robot?.poweroff?.available ? 'Both Pis must be connected with shutdown support.' : '');
    indicator('roofState', 'Winch controller · ' + (roof ? 'connected' : 'offline'), !!roof);
    indicator('robotState', 'Camera unit · ' + (robot ? 'connected' : 'offline'), !!robot);
    indicator('exchangeState', 'Data link · ' + (linked ? 'connected' : 'waiting'), linked);
    const tap = robot?.tap;
    const idle = tap?.available && tap.mode === 'idle';
    el('tapOnce').disabled = el('tapBurst').disabled = stopping || tapBusy || tapStopping || inspectionBusy || !idle;
    const strengthBusy = stopping || tapBusy || tapStopping || inspectionBusy || !idle || xBusy() || ySending || !!roof?.relays.busy;
    const strengthAvailable = tap?.strengthControl === true;
    el('strengthTap').disabled = strengthBusy || !strengthAvailable;
    el('tapStrength').disabled = el('strengthDefault').disabled = strengthBusy || !strengthAvailable;
    el('tapDuration').disabled = strengthBusy || tap?.durationControl !== true;
    el('recordBaseline').disabled = el('tapBurst').disabled || xBusy() || ySending || !!roof?.relays.action;
    el('tapStop').disabled = tapStopping || !tap?.available || (idle && !tapBusy && !inspectionBusy);
    el('tapStop').hidden = !tapBusy && !tapStopping && (!tap?.available || idle);
    if (!tapBusy && !tapStopping && !el('tapStatus').dataset.message) {
      el('tapStatus').textContent = tap?.available
        ? ({burst: 'Recording three taps…', single: 'Tapping once…', auto: 'Tapping at ' + tap.hz + ' Hz'}[tap.mode] || 'Tap ready')
        : 'Robot tap controller unavailable';
    }
    el('encoderX').value = roof?.position.xInches?.toFixed(2) ?? '';
    el('encoderY').value = roof?.position.yInches?.toFixed(2) ?? '';
    el('xReadout').value = el('encoderX').value ? el('encoderX').value + ' in' : '—';
    el('yReadout').value = el('encoderY').value ? el('encoderY').value + (roof?.yAxis?.estimated ? ' in · estimated' : ' in') : '—';
    const pulleyCount = roof?.yAxis?.rawCounts;
    const yCount = roof?.position.y;
    const yCountText = yCount == null && pulleyCount != null ? `${pulleyCount} raw` : yCount ?? '—';
    el('positionStatus').textContent = roof
      ? (roof.yAxis?.estimated ? `X ${roof.position.x ?? '—'} counts · Y estimated from winch time` : `X ${roof.position.x ?? '—'} counts · Y ${yCountText} counts`)
      : 'Waiting for position from the controller.';
    const relays = roof?.relays;
    const upOnly = relays?.mode === 'live-up';
    const fourChannel = relays?.mode === 'live-four';
    const live = upOnly || fourChannel;
    const sending = pendingWinchCommands > 0;
    const ready = !!relays && relays.ready !== false;
    const canControlWinch = ready && (fourChannel || linked) && !stopping && !sending &&
      !inspectionBusy && !xBusy() && !distanceControllers.y && !ySending && !roof?.yAxis?.moving;
    const horizontal = roof?.xAxis;
    const vertical = roof?.yAxis;
    const measuredY = vertical?.mode === 'live';
    const movingY = !!distanceControllers.y || !!vertical?.moving || ySending;
    const winchBusy = relays?.busy ?? !!relays?.action;
    const canArm = relays?.autoArm || relays?.enabled;
    const available = !inspectionBusy && !stopping && !sending && !winchBusy;
    const canMove = available && !xBusy() && !movingY;
    el('moveLeft').disabled = el('moveRight').disabled = !canMove || !horizontal?.ready;
    el('stopX').disabled = !(xBusy() || horizontal?.moving);
    el('moveYUp').disabled = !canMove || !vertical ||
      (measuredY && !(canArm && (vertical.upReady ?? vertical.ready)));
    el('moveYDown').disabled = !canMove || !vertical ||
      (measuredY && !(canArm && (vertical.downReady ?? vertical.ready)));
    el('stopY').hidden = !measuredY;
    el('stopY').disabled = !movingY;
    el('zeroY').disabled = !canMove || !vertical ||
      (measuredY && (!vertical.connected || !vertical.direction));
    el('manualZeroY').disabled = el('zeroY').disabled;
    el('pulleySetup').hidden = !measuredY;
    el('pulleySave').disabled = !canMove || !measuredY;
    paintWinchTuning(vertical, canMove && measuredY && !stopping);
    if (measuredY && el('pulleySetup').dataset.boot !== roof.bootId) {
      el('pulleySetup').dataset.boot = roof.bootId;
      el('pulleyTravel').value = Number(vertical.inchesPerTurn.toFixed(6));
      el('pulleyHoldDown').checked = vertical.holdToRunDown ?? vertical.holdToRun;
      el('pulleyHoldUp').checked = vertical.holdToRunUp ?? vertical.holdToRun;
      el('timedY').checked = !!vertical.estimated;
      el('timedSpeed').value = vertical.timedSpeed ?? 1;
    }
    el('pulleyCount').textContent = vertical?.rawCounts ?? '—';
    el('yMode').textContent = vertical?.estimated ? 'Timed estimate · no encoder' : isPreview && vertical?.connected ? 'Simulated pulley' : measuredY ? (vertical.connected ? 'Pico 2 connected' : 'Pico 2 unavailable')
      : vertical ? 'Simulated' : 'Unavailable';
    el('xMode').textContent = isPreview && horizontal?.connected ? 'Simulated carriage' : horizontal?.connected === true ? 'Nano Every connected'
      : horizontal?.connected === false ? 'Nano Every unavailable'
      : horizontal?.mode === 'live' ? 'Live motor' : horizontal?.ready ? 'Simulated' : 'Unavailable';
    el('xMovementStatus').textContent = xMessage || horizontal?.message || 'Waiting for the X controller.';
    el('yMovementStatus').textContent = yMessage || vertical?.message || (vertical?.zeroed
      ? 'Y increases downward from the roof.' : 'Set zero when the robot is at the top.');
    el('winchMode').textContent = live && !isPreview ? 'Live winch' : 'Simulated winch';
    el('winchHint').textContent = relays?.autoArm ? 'Each jog sends Start, the pulse chosen in Setup, then Stop.'
      : upOnly ? 'Start arms Up. Stop disarms it.'
      : live ? 'Start enables motion. Up and Down request 0.3 second jogs.'
      : 'Up and Down simulate 0.3 second jogs.';
    el('winchStart').disabled = !canControlWinch || winchBusy || (!relays?.autoArm && relays?.enabled);
    const canJog = canControlWinch && canArm && !winchBusy;
    el('winchUp').disabled = !canJog;
    el('winchDown').hidden = upOnly;
    el('winchDown').disabled = upOnly || !canJog;
    el('winchStop').disabled = !ready || !!roof?.poweroff?.scheduled;
    for (const channel of relays?.channels ?? []) {
      const indicator = el('relay' + channel.channel);
      indicator.textContent = `CH${channel.channel} · ${channel.label}`;
      indicator.classList.toggle('on', channel.closed);
    }
    if (!relays) {
      for (let n = 1; n <= 4; n++) el('relay' + n).classList.remove('on');
    }
    if (!roof || (!sending && !el('winchFeedback').dataset.command)) {
      el('winchFeedback').textContent = !roof ? 'Waiting for controller…' : live
        ? (!ready ? 'Winch relay unavailable' : relays.autoArm ? (winchBusy ? 'Sending winch command…' : 'Up or Down will send Start first.')
          : relays.enabled ? 'Winch armed' : 'Press Start to arm the winch.')
        : relays.enabled ? 'Simulation enabled. Up and Down change the sample Y count.' : 'Press Start to enable the simulation.';
    }
    el('deviceAddresses').textContent = latest
      ? `Roof ${latest.addresses.roof} · Robot ${latest.addresses.robot}` : '';
    el('packetStatus').textContent = roof
      ? `Roof → robot → roof · sent ${roof.exchange.sent} · acknowledged ${roof.exchange.acknowledged}`
        + (linked ? ` · round trip ${roof.exchange.roundTripMs} ms` : ' · waiting for robot')
      : 'Controller packet exchange is unavailable.';
  }

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch('/api/network', {cache: 'no-store', signal: AbortSignal.timeout(1800)});
      if (!response.ok) throw new Error('Controller status unavailable');
      latest = await response.json();
      receivedAt = performance.now();
      if (!initialModeSet && latest.roof.reachable) {
        initialModeSet = true;
        if (!isPreview && latest.roof.data.yAxis?.mode === 'live' && !inspectionBusy) {
          el('inspectionMode').value = 'hardware';
          el('inspectionMode').dispatchEvent(new Event('change'));
        }
      }
    } catch {
      latest = null;
    } finally {
      polling = false;
      paint();
      window.dispatchEvent(new Event('inspectionposition'));
    }
  }

  async function command(action) {
    if (pendingWinchCommands && action !== 'stop') return;
    pendingWinchCommands++;
    el('winchFeedback').dataset.command = 'true';
    el('winchFeedback').textContent = 'Sending ' + action + '…';
    paint();
    try {
      const response = await fetch('/api/winch', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Photo-Token': token},
        body: JSON.stringify({action, id: window.brickStorage.id(), mode: latest?.roof.data?.relays.mode,
          motionEpoch: latest?.roof.data?.relays.motionEpoch}),
        signal: AbortSignal.timeout(3500)
      });
      const result = await response.json();
      el('winchFeedback').textContent = response.ok ? result.message : result.error;
      const channel = result.channel ?? latest?.roof.data?.relays.channels.find(item =>
        item.label.toLowerCase() === action)?.channel;
      if (response.ok && result.pulseMs > 0 && channel &&
          !(latest?.roof.data?.relays.autoArm && ['up', 'down'].includes(action))) {
        el('relay' + channel).classList.add('on');
        setTimeout(() => el('relay' + channel).classList.remove('on'), result.pulseMs);
      }
      return response.ok ? result : {error: result.error};
    } catch {
      el('winchFeedback').textContent = 'Acknowledgement unavailable. Check controller status before retrying.';
      return {error: el('winchFeedback').textContent};
    } finally {
      pendingWinchCommands--;
      poll();
      setTimeout(() => { delete el('winchFeedback').dataset.command; paint(); }, 3000);
    }
  }
  for (const action of ['start', 'up', 'down', 'stop']) {
    el('winch' + action[0].toUpperCase() + action.slice(1)).onclick = () => {
      if (action === 'stop') {
        window.dispatchEvent(new Event('inspectionstop'));
        stopY().catch(() => {});
      }
      return command(action);
    };
  }

  async function sendAxis(axis, data) {
    const response = await fetch('/api/' + axis + '-axis', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Photo-Token': token},
      body: JSON.stringify(data), keepalive: data.action === 'stop', signal: AbortSignal.timeout(3000)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }

  async function waitForMove(axis, id, bootId, signal) {
    const deadline = performance.now() + (axis === 'y' ? 94000 : 15000);
    while (performance.now() < deadline) {
      signal?.throwIfAborted();
      const roof = window.blueInspection.state()?.roof.data;
      if (!roof || roof.bootId !== bootId) throw new Error('Rooftop connection changed during movement.');
      const motion = roof[axis + 'Axis'];
      if (motion?.moveId === id && !motion.moving) {
        if (motion.outcome !== 'done') throw new Error(motion.message || 'Movement stopped.');
        return roof.position;
      }
      await new Promise(resolve => setTimeout(resolve, 160));
    }
    throw new Error('Movement could not be confirmed.');
  }

  async function moveDistance(axis, action, inches, options = {}) {
    if (!Number.isFinite(inches) || inches < .1) throw new Error('Enter a positive distance of at least 0.1 inches.');
    if (window.blueInspection.movementBusy()) throw new Error('Wait for movement to finish.');
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.throwIfAborted();
    options.signal?.addEventListener('abort', abort, {once: true});
    distanceControllers[axis] = controller;
    paint();
    try {
      const roof = window.blueInspection.state()?.roof.data;
      const maximum = roof?.[axis + 'Axis']?.maxJogInches ?? (axis === 'x' ? 6 : 12);
      const chunkSize = axis === 'y' && roof?.yAxis?.estimated
        ? Math.min(maximum, roof.yAxis.timedSpeed * 3) : maximum;
      let remaining = inches;
      let position;
      while (remaining >= .000001) {
        controller.signal.throwIfAborted();
        // Keep the final command at least 0.1 in, including distances such as 6.05 in.
        const chunk = Math.max(.1, remaining <= chunkSize ? remaining : Math.min(chunkSize, remaining - .1));
        if (remaining - chunk === remaining) throw new Error('That distance exceeds supported numeric precision.');
        const requestOptions = {...options, signal: controller.signal};
        position = axis === 'x'
          ? await moveXJog(action, chunk, requestOptions)
          : await moveYJog(action, chunk, requestOptions);
        remaining -= chunk;
        controller.signal.throwIfAborted();
      }
      return position;
    } finally {
      options.signal?.removeEventListener('abort', abort);
      delete distanceControllers[axis];
      paint();
    }
  }

  function moveX(action, inches, options) {
    if (action === 'stop') {
      distanceControllers.x?.abort();
      return moveXJog(action, inches, options);
    }
    return moveDistance('x', action, inches, options);
  }

  function moveY(action, inches, options) {
    return action === 'up' || action === 'down'
      ? moveDistance('y', action, inches, options) : moveYJog(action, inches, options);
  }

  async function moveXJog(action, inches, {signal} = {}) {
    const stopping = action === 'stop';
    const roof = window.blueInspection.state()?.roof.data;
    if (!stopping && (xRequestId || roof?.xAxis?.moving)) throw new Error('Wait for X movement to finish.');
    signal?.throwIfAborted();
    const id = window.brickStorage.id();
    const cancelId = xRequestId;
    if (!stopping) xRequestId = id;
    xMessage = stopping ? 'Stopping X…' : 'Sending X move…';
    paint();
    try {
      const result = await sendAxis('x', {id, action, inches, mode: roof?.xAxis?.mode, cancelId});
      if (!stopping && result.moveId === id) {
        xMessage = '';
        return await waitForMove('x', id, roof.bootId, signal);
      }
      if (!stopping && signal) throw new Error('The X move was not acknowledged.');
      xMessage = '';
    } catch (error) {
      if (!stopping && xRequestId === id && roof?.xAxis?.connected) {
        await sendAxis('x', {id: window.brickStorage.id(), action: 'stop', cancelId: id}).catch(() => {});
      }
      xMessage = error.message || 'Could not confirm the X move.';
      throw error;
    } finally {
      if (xRequestId === (stopping ? cancelId : id)) xRequestId = null;
      paint();
      poll();
    }
  }

  function stopX() {
    distanceControllers.x?.abort();
    return xRequestId ? moveXJog('stop') : Promise.resolve();
  }

  function stopY() {
    distanceControllers.y?.abort();
    if (!yRequestId) return Promise.resolve();
    return sendAxis('y', {id: window.brickStorage.id(), action: 'stop', cancelId: yRequestId});
  }

  async function moveYJog(action, inches, {signal, calibration, routeLimitInches} = {}) {
    if (ySending) throw new Error('Wait for the Y command to finish.');
    signal?.throwIfAborted();
    const roof = window.blueInspection.state()?.roof.data;
    const mode = roof?.yAxis?.mode;
    const id = window.brickStorage.id();
    const moving = mode === 'live' && (action === 'up' || action === 'down');
    ySending = true;
    if (moving) yRequestId = id;
    yMessage = '';
    paint();
    try {
      const result = await sendAxis('y', {id, action, inches, mode,
        motionEpoch: roof?.relays.motionEpoch, routeLimitInches, ...calibration});
      if (moving) {
        if (result.moveId !== id) throw new Error('The Y move was not acknowledged.');
        return await waitForMove('y', id, roof.bootId, signal);
      }
      return result.position;
    } catch (error) {
      if (moving) await stopY().catch(() => {});
      throw error;
    } finally {
      if (yRequestId === id) yRequestId = null;
      ySending = false;
      paint();
      poll();
    }
  }

  setInterval(async () => {
    if (renewing || document.hidden || !positionReady()) return;
    const motions = [];
    if (xRequestId && latest.roof.data.xAxis?.connected) motions.push(['x', xRequestId]);
    if (yRequestId) motions.push(['y', yRequestId]);
    if (!motions.length) return;
    renewing = true;
    try {
      await Promise.allSettled(motions.map(([axis, id]) => sendAxis(axis, {id, action: 'renew'})));
    } finally {
      renewing = false;
    }
  }, 500);
  function stopDistanceMoves() {
    if (distanceControllers.x) stopX().catch(() => {});
    if (distanceControllers.y) stopY().catch(() => {});
  }
  window.addEventListener('pagehide', stopDistanceMoves);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopDistanceMoves(); });
  el('stopY').onclick = () => {
    window.dispatchEvent(new Event('inspectionstop'));
    return Promise.allSettled([stopY(), command('stop')]);
  };
  function tuningLabels() {
    for (const direction of ['Up', 'Down']) {
      for (const field of ['Pulse', 'Min', 'Max', 'Settle']) {
        const id = 'winch' + direction + field;
        el(id + 'Value').textContent = el(id).value + ' ms';
      }
    }
  }

  function paintWinchTuning(axis, available) {
    const settings = axis?.winchTuning;
    const signature = JSON.stringify(settings);
    if (settings && signature !== tuningSaved && !tuningDirty && !tuningSaving) {
      for (const direction of ['Up', 'Down']) {
        const values = settings[direction.toLowerCase()];
        for (const [field, key] of [['Pulse','pulseMs'],['Min','minPulseMs'],['Max','maxPulseMs'],['Settle','settleMs']]) {
          el('winch' + direction + field).value = values[key];
        }
      }
      el('winchAdaptive').checked = settings.adaptive;
      tuningSaved = signature;
      tuningLabels();
      el('winchTuningStatus').textContent = isPreview ? 'Preview winch settings loaded.' : 'Saved winch settings loaded from the roof Pi.';
    }
    el('winchTuningSave').disabled = !available || tuningSaving || !settings;
  }

  function editWinchTuning(event) {
    for (const direction of ['Up', 'Down']) {
      const pulse = el('winch' + direction + 'Pulse');
      const minimum = el('winch' + direction + 'Min');
      const maximum = el('winch' + direction + 'Max');
      if (event.target === minimum) {
        pulse.value = Math.max(pulse.valueAsNumber, minimum.valueAsNumber);
        maximum.value = Math.max(maximum.valueAsNumber, pulse.valueAsNumber);
      } else if (event.target === maximum) {
        pulse.value = Math.min(pulse.valueAsNumber, maximum.valueAsNumber);
        minimum.value = Math.min(minimum.valueAsNumber, pulse.valueAsNumber);
      } else if (event.target === pulse) {
        minimum.value = Math.min(minimum.valueAsNumber, pulse.valueAsNumber);
        maximum.value = Math.max(maximum.valueAsNumber, pulse.valueAsNumber);
      }
    }
    tuningDirty = true;
    tuningLabels();
    el('winchTuningStatus').textContent = 'Not applied yet. Apply these settings for the next move.';
  }

  for (const direction of ['Up', 'Down']) {
    for (const field of ['Pulse', 'Min', 'Max', 'Settle']) el('winch' + direction + field).oninput = editWinchTuning;
  }
  el('winchAdaptive').onchange = editWinchTuning;
  el('winchTuningSave').onclick = async () => {
    if (tuningSaving) return;
    const settings = {adaptive: el('winchAdaptive').checked};
    for (const direction of ['Up', 'Down']) {
      settings[direction.toLowerCase()] = {};
      for (const [field, key] of [['Pulse','pulseMs'],['Min','minPulseMs'],['Max','maxPulseMs'],['Settle','settleMs']]) {
        settings[direction.toLowerCase()][key] = el('winch' + direction + field).valueAsNumber;
      }
    }
    tuningSaving = true;
    paint();
    el('winchTuningStatus').textContent = 'Saving winch settings…';
    try {
      const result = await sendAxis('y', {id: window.brickStorage.id(), action: 'tune', winchTuning: settings});
      tuningSaved = JSON.stringify(result.winchTuning);
      if (latest?.roof.data?.yAxis) latest.roof.data.yAxis.winchTuning = result.winchTuning;
      tuningDirty = false;
      el('winchTuningStatus').textContent = isPreview ? 'Applied to the preview.' : 'Saved on the roof Pi. Ready for the next move.';
    } catch (error) {
      el('winchTuningStatus').textContent = 'Settings were not applied. ' + error.message;
    } finally {
      tuningSaving = false;
      paint();
      poll();
    }
  };

  el('pulleySave').onclick = async () => {
    const travel = el('pulleyTravel');
    const speed = el('timedSpeed');
    if (!travel.reportValidity() || !speed.reportValidity()) return;
    try {
      await moveY('calibrate', undefined, {calibration: {
        inchesPerTurn: travel.valueAsNumber,
        timedY: el('timedY').checked, timedSpeed: speed.valueAsNumber,
        holdToRunDown: el('pulleyHoldDown').checked,
        holdToRunUp: el('pulleyHoldUp').checked
      }});
      yMessage = 'Pulley setup saved.';
    } catch (error) {
      yMessage = error.message;
    }
    paint();
  };

  for (const [id, action] of [['moveLeft', 'left'], ['moveRight', 'right'], ['stopX', 'stop']]) {
    el(id).onclick = () => {
      const input = el('xDistance');
      if (action === 'stop' || input.reportValidity()) return moveX(action, input.valueAsNumber).catch(() => {});
    };
  }
  for (const [id, action] of [['moveYUp', 'up'], ['moveYDown', 'down'], ['zeroY', 'zero']]) {
    el(id).onclick = async () => {
      const input = el('yDistance');
      if (action !== 'zero' && !input.reportValidity()) return;
      try {
        await moveY(action, input.valueAsNumber);
        if (action === 'zero') {
          yMessage = 'Y zero set at this position.';
          paint();
        }
      } catch (error) {
        yMessage = error.message || 'Could not confirm the Y command.';
        paint();
      }
    };
  }

  el('manualZeroY').onclick = () => el('zeroY').onclick();

  el('poweroff').onclick = () => {
    const dialog = el('poweroffDialog');
    dialog.returnValue = 'cancel';
    dialog.showModal();
  };
  el('poweroffDialog').addEventListener('close', async event => {
    if (event.currentTarget.returnValue !== 'poweroff' || el('poweroff').disabled) return;
    poweroffBusy = true;
    poweroffMessage = 'Requesting shutdown…';
    paint();
    try {
      const response = await fetch('/api/poweroff', {
        method: 'POST', headers: {'X-Photo-Token': token}, signal: AbortSignal.timeout(9000)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      poweroffScheduled = true;
      poweroffMessage = scheduledMessage;
    } catch (error) {
      poweroffMessage = error instanceof TypeError || error.name === 'TimeoutError'
        ? 'Shutdown could not be confirmed. Keep both Pis powered.' : error.message;
    } finally {
      poweroffBusy = false;
      paint();
      poll();
    }
  });
  async function tapRequest(action, id, keepalive = false) {
    const record = action === 'single' || action === 'burst';
    const response = await fetch('/api/tap', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Photo-Token': token},
      body: JSON.stringify({action, id, record, ...(record ? {peakPwm: el('tapStrength').valueAsNumber, peakMs: el('tapDuration').valueAsNumber} : {})}), keepalive,
      signal: AbortSignal.timeout(record ? 10000 : 1500)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }

  let clipNumber = 0;
  const displayedClips = new Set();
  function renderTapClip({wav, id, burst, captured_at}) {
    if (displayedClips.has(id)) return;
    displayedClips.add(id);
    const item = document.createElement('li');
    item.dataset.recordingId = id;
    const label = document.createElement('span');
    label.textContent = (burst ? 'Three taps at 2 Hz' : 'Single tap') + ' · Recording ' + ++clipNumber
      + ' · ' + new Date(captured_at).toLocaleString();
    const player = window.brickStorage.audioPlayer(wav, label.textContent);
    player.preload = 'metadata';
    player.addEventListener('play', () => {
      if (wanted) {
        el('audioToggle').textContent = 'Play microphone';
        el('audioStatus').textContent = 'Live microphone paused for playback.';
      }
    });
    const download = document.createElement('a');
    download.href = player.src;
    download.download = (burst ? 'brick-three-taps-' : 'brick-tap-') + id + '.wav';
    download.textContent = 'Download WAV';
    item.append(label, player, download);
    if (burst) {
      const analysis = document.createElement('div');
      analysis.className = 'tap-analysis';
      item.append(analysis);
      window.brickTapAnalysis.show(analysis, {name: download.download, tap_burst: true, tap_audio: [wav]});
    }
    el('tapClips').prepend(item);
    el('recordingsEmpty').hidden = true;
    el('tapRecordings').hidden = false;
    el('tapRecordings').open = true;
  }

  async function addTapClip(wav, id, burst, settings = {}) {
    const recording = {id, wav, burst, captured_at: new Date().toISOString(),
      peakPwm: settings.peakPwm, peakMs: settings.peakMs};
    renderTapClip(recording);
    pendingTapSaves++;
    el('audioSaveStatus').textContent = 'Recording ready. Saving audio on this computer…';
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.textContent = 'Retry saving audio';
    retry.hidden = true;
    const item = [...el('tapClips').children].find(item => item.dataset.recordingId === id);
    item?.append(retry);
    retry.onclick = async () => {
      retry.disabled = true;
      pendingTapSaves++;
      try {
        await window.brickStorage.saveRecording(recording);
        retry.remove();
        el('audioSaveStatus').textContent = 'Audio saved on this computer.';
      } catch (error) {
        el('audioSaveStatus').textContent = 'Audio is still available to play or download. ' + error.message;
      } finally { pendingTapSaves--; retry.disabled = false; }
    };
    try {
      await window.brickStorage.saveRecording(recording);
      retry.remove();
      el('audioSaveStatus').textContent = 'Audio saved on this computer. It will be available after a refresh.';
    } catch (error) {
      retry.hidden = false;
      el('audioSaveStatus').textContent = 'Audio could not be saved. Download the WAV before closing this page. ' + error.message;
      throw error;
    } finally { pendingTapSaves--; }
  }

  async function restoreTapClips() {
    try {
      for (const recording of await window.brickStorage.listRecordings()) renderTapClip(recording);
    } catch (error) {
      el('audioSaveStatus').textContent = 'Could not open saved audio. ' + error.message;
    }
  }
  restoreTapClips();
  window.addEventListener('focus', restoreTapClips);
  window.addEventListener('brickcaptured', restoreTapClips);

  async function tapCommand(action, {baseline = false} = {}) {
    const stop = action === 'stop';
    if (tapStopping || (tapBusy && !stop)) return;
    const id = (stop && tapId) || window.brickStorage.id();
    if (stop) {
      tapStopping = true;
      tapId = null;
    } else {
      tapBusy = true;
      tapId = id;
    }
    delete el('tapStatus').dataset.message;
    paint();
    el('tapStatus').textContent = stop ? 'Stopping taps…'
      : action === 'burst' ? 'Recording three taps at 2 Hz…' : 'Recording tap…';
    let hardwareReleased = false, canceled = false;
    const releaseHardware = () => {
      if (hardwareReleased) return;
      hardwareReleased = true;
      if (stop) { tapStopping = false; tapBusy = false; }
      else if (tapId === id || (tapId === null && !tapStopping)) { tapId = null; tapBusy = false; }
      paint();
    };
    const baselineFeedback = el('baselineFeedback');
    if (baseline) baselineFeedback.textContent = 'Recording the reference brick…';
    try {
      const result = await tapRequest(action, id);
      if (!stop && tapId !== id) { canceled = true; return; }
      releaseHardware();
      if (Number.isInteger(result.peakPwm)) {
        el('strengthStatus').textContent = `Last tap used peak PWM ${result.peakPwm} / 255 for ${result.peakMs ?? 50} ms.`;
      }
      if (result.audioWav) await addTapClip(result.audioWav, id, action === 'burst', result);
      if (baseline) {
        if (!result.audioWav) throw new Error(result.audioError || 'No baseline recording was received.');
        window.brickTapAnalysis.useRecordingBaseline(result.audioWav, isPreview ? 'Sample reference brick' : 'Reference brick');
        baselineFeedback.textContent = 'Baseline ready.';
      }
      if (!tapBusy && !tapStopping) el('tapStatus').textContent = stop ? 'Tapping stopped' : result.audioError ||
        (result.audioWav ? 'Recording ready' : 'Audio recording unavailable.');
    } catch (error) {
      if (baseline && !canceled) baselineFeedback.textContent = error.message;
      if (!hardwareReleased || (!tapBusy && !tapStopping)) el('tapStatus').textContent = error.message || 'Tap acknowledgement unavailable';
    } finally {
      releaseHardware();
      if (baseline && canceled) baselineFeedback.textContent = 'Baseline recording stopped.';
      if (!tapBusy && !tapStopping) {
        el('tapStatus').dataset.message = 'true';
        setTimeout(() => { delete el('tapStatus').dataset.message; paint(); }, 1300);
      }
      poll();
    }
  }

  function stopLeaving() {
    if (!tapId) return;
    const id = tapId;
    tapId = null;
    tapRequest('stop', id, true).catch(() => {});
  }

  el('tapOnce').onclick = () => tapCommand('single');
  el('strengthTap').onclick = () => tapCommand('single');
  el('tapStrength').oninput = () => {
    const value = el('tapStrength').valueAsNumber;
    el('tapStrengthValue').textContent = `${value} / 255 · ${Math.round(value / 255 * 100)}%`;
    el('strengthStatus').textContent = 'Selected strength applies to the next tap and inspection burst.';
  };
  el('tapDuration').oninput = () => {
    el('tapDurationValue').textContent = el('tapDuration').value+' ms';
    el('strengthStatus').textContent = 'Selected duration applies to the next tap and inspection burst.';
  };
  el('strengthDefault').onclick = () => {
    el('tapStrength').value = '210'; el('tapStrength').oninput();
    el('tapDuration').value = '50'; el('tapDuration').oninput();
  };
  el('tapBurst').onclick = () => tapCommand('burst');
  el('recordBaseline').onclick = () => tapCommand('burst', {baseline: true});
  el('tapStop').onclick = () => {
    if (inspectionBusy) el('inspectionCancel').click();
    else tapCommand('stop');
  };
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopLeaving(); });
  window.addEventListener('pagehide', stopLeaving);
  setInterval(poll, 500);
  setInterval(paint, 500);
  poll();

  const audio = el('microphone');
  let wanted = false, peer = null, session = null, generation = 0, retry = null, stats = null;
  let lastPacket = 0, packets = 0, connectedAt = 0, controller = null;
  function clearAudio() {
    if (controller) controller.abort();
    controller = null;
    if (stats) clearInterval(stats);
    stats = null;
    if (peer) peer.close();
    peer = null;
    audio.pause();
    audio.srcObject = null;
    if (session) fetch(session, {method: 'DELETE', headers: {'X-Photo-Token': token}, signal: AbortSignal.timeout(1200)}).catch(() => {});
    session = null;
  }
  function reconnectAudio(message) {
    if (!wanted || retry) return;
    generation++;
    clearAudio();
    el('audioStatus').textContent = message + ' · retrying';
    retry = setTimeout(() => { retry = null; connectAudio(); }, 1200);
  }
  async function connectAudio() {
    if (!wanted) return;
    const current = ++generation;
    clearAudio();
    const local = new RTCPeerConnection({iceServers: []});
    peer = local;
    controller = new AbortController();
    const abort = controller;
    const endpoint = new URL('/media/audio/whep', location.href);
    lastPacket = 0;
    packets = 0;
    connectedAt = performance.now();
    local.addTransceiver('audio', {direction: 'recvonly'});
    local.ontrack = event => {
      if (generation !== current) return;
      audio.srcObject = event.streams[0] || new MediaStream([event.track]);
      audio.play().catch(() => {
        el('audioToggle').textContent = 'Play microphone';
        el('audioStatus').textContent = 'Microphone connected. Click Play microphone to hear it.';
      });
    };
    local.onconnectionstatechange = () => {
      if (generation === current && local.connectionState === 'failed') reconnectAudio('Microphone connection lost');
    };
    const timeout = setTimeout(() => abort.abort(), 5000);
    el('audioStatus').textContent = 'Connecting to robot microphone…';
    try {
      await local.setLocalDescription(await local.createOffer());
      if (local.iceGatheringState !== 'complete') await new Promise(resolve => {
        const timer = setTimeout(resolve, 1800);
        local.addEventListener('icegatheringstatechange', () => {
          if (local.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); }
        });
      });
      if (generation !== current) return;
      const response = await fetch(endpoint, {method: 'POST', headers: {'Content-Type': 'application/sdp', 'X-Photo-Token': token},
        body: local.localDescription.sdp, signal: abort.signal});
      if (!response.ok) throw new Error('Microphone unavailable');
      const location = response.headers.get('Location');
      const created = location ? new URL(location, endpoint).href : null;
      if (generation !== current) {
        if (created) fetch(created, {method: 'DELETE', headers: {'X-Photo-Token': token}}).catch(() => {});
        return;
      }
      session = created;
      const lines = (await response.text()).split('\r\n');
      const candidates = lines.filter(line => line.startsWith('a=candidate:') && line.trim().split(/\s+/)[4] === '192.168.2.3');
      if (!candidates.length) throw new Error('Microphone Ethernet route unavailable');
      await local.setRemoteDescription({type: 'answer', sdp: lines.filter(line => !line.startsWith('a=candidate:') || candidates.includes(line)).join('\r\n')});
      stats = setInterval(async () => {
        if (generation !== current) return;
        try {
          const report = await local.getStats();
          if (generation !== current) return;
          let received = 0;
          report.forEach(row => { if (row.type === 'inbound-rtp' && row.kind === 'audio') received += row.packetsReceived || 0; });
          if (received > packets) lastPacket = performance.now();
          packets = received;
          if (lastPacket && !audio.paused) el('audioStatus').textContent = 'Live robot microphone · ' + packets + ' audio packets received';
          const stale = lastPacket ? performance.now() - lastPacket > 2500 : performance.now() - connectedAt > 8000;
          if (stale) reconnectAudio('No microphone audio arriving');
        } catch {
          if (generation === current) reconnectAudio('Microphone connection interrupted');
        }
      }, 1000);
    } catch (error) {
      if (generation === current) reconnectAudio(error.message);
    } finally {
      clearTimeout(timeout);
    }
  }
  el('audioToggle').onclick = async () => {
    for (const clip of el('tapClips').querySelectorAll('audio')) clip.pause();
    if (wanted && audio.paused && audio.srcObject) {
      try { await audio.play(); el('audioToggle').textContent = 'Stop listening'; } catch { el('audioStatus').textContent = 'Playback blocked. Try Play microphone again.'; }
      return;
    }
    wanted = !wanted;
    el('audioToggle').setAttribute('aria-pressed', String(wanted));
    el('audioToggle').textContent = wanted ? 'Stop listening' : 'Listen to microphone';
    if (wanted) connectAudio();
    else {
      generation++;
      if (retry) clearTimeout(retry);
      retry = null;
      clearAudio();
      el('audioStatus').textContent = 'Microphone playback is off.';
    }
  };
  window.addEventListener('beforeunload', () => { wanted = false; generation++; clearAudio(); });
})();
