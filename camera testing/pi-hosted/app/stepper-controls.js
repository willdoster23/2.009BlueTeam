'use strict';
(() => {
  const el = id => document.getElementById(id);
  const dirty = {x:false,y:false}, speedDirty = {x:false,y:false}, busy = {x:false,y:false};
  const saved = {}, savedSpeed = {};
  const fields = {Ppr:'pulsesPerRev', Accel:'accel', Travel:'travelPerRev', Sign:'positiveDirection'};
  function readiness(axis, state) {
    const name = axis.toUpperCase();
    if (!state?.motorConnected) return `Connect the ${name} motor Nano.`;
    if (state.settingsError) return state.settingsError;
    if (!state.stepperSettings?.calibrated) return `Confirm the ${name} distance scale in Setup → Distance calibration.`;
    if (axis === 'y' && !state.encoderSignalValid) return 'Y needs a valid pulley encoder signal for inch moves.';
    if (!state.zeroed) return `Set ${name} zero here before moving by inches.`;
    if (!state.motorArmed) return `Press Arm ${name} motor, then choose ${axis === 'x' ? 'Left or Right' : 'Up or Down'}.`;
    return `Ready to move in inches${axis === 'x' ? ' · X position is estimated from commanded steps' : ' · Y travel is measured by the pulley encoder'}.`;
  }
  window.blueStepperControls = {readiness, busy: () => busy.x || busy.y};
  function refresh() { window.blueInspection.refresh?.(); paint(); }
  function adopt(axis, result) {
    const roof = window.blueInspection.state()?.roof.data;
    if (roof && result?.drive === 'stepper') roof[axis+'Axis'] = result;
  }
  function paint() {
    const ui = window.blueInspection, roof = ui.state()?.roof.data;
    const enabled = ['x','y'].some(axis => roof?.[axis+'Axis']?.drive === 'stepper');
    el('stepperSetup').hidden = !enabled;
    el('stepperStopHint').hidden = !enabled;
    for (const axis of ['x','y']) {
      const state = roof?.[axis+'Axis'], active = state?.drive === 'stepper';
      el(axis+'StepperManual').hidden = el(axis+'StepperDiagnostics').hidden = !active;
      if (!active) continue;
      const profile = state.stepperSettings, signature = JSON.stringify(profile);
      if (profile && signature !== saved[axis] && !dirty[axis] && !busy[axis]) {
        for (const [suffix,key] of Object.entries(fields)) el(axis+'Motor'+suffix).value = profile[key];
        el(axis+'MotorCalibrated').checked = profile.calibrated;
        if (axis === 'y') el('yMotorAlarm').value = profile.alarmMode;
        saved[axis] = signature;
      }
      if (profile && profile.rpm !== savedSpeed[axis] && !speedDirty[axis] && !busy[axis]) {
        el(axis+'MotorRpm').value = profile.rpm;
        savedSpeed[axis] = profile.rpm;
      }
      const inputPpr = Number(el(axis+'MotorPpr').value);
      if (Number.isFinite(inputPpr) && inputPpr >= 200) {
        el(axis+'MotorRpm').max = Math.min(state.motorRPMCap ?? Infinity,
          state.maxPulsesPerSecond * 60 / inputPpr);
      }
      const unavailable = ui.movementBusy() || ui.inspectionActive() || !!roof.poweroff?.scheduled || busy.x || busy.y;
      el(axis+'MotorSave').disabled = unavailable || state.motorArmed;
      el(axis+'MotorSpeedSave').disabled = el(axis+'MotorRpm').disabled = unavailable || !state.motorConnected;
      el(axis+'MotorArm').disabled = unavailable || !state.motorConnected;
      el(axis+'MotorArm').textContent = `${state.motorArmed ? 'Disarm' : 'Arm'} ${axis.toUpperCase()} motor`;
      el(axis+'MotorPositive').disabled = el(axis+'MotorNegative').disabled = unavailable || !state.motorConnected || !state.motorArmed;
      const motor = state.motor;
      el(axis+'MotorStatus').textContent = !motor ? 'Motor Nano not reporting.'
        : `${state.motorArmed ? 'Armed' : 'Disarmed'} · speed setting ${profile.rpm} RPM`
          + (state.moving ? ` · commanded ${motor.commandedRPM.toFixed(1)} RPM` : '');
      if (axis === 'y' && state.cableSpeedInchesPerSecond != null) {
        el(axis+'MotorStatus').textContent += ` · measured cable speed ${state.cableSpeedInchesPerSecond.toFixed(2)} in/s`;
      }
      if (!speedDirty[axis] && !busy[axis]) el(axis+'MotorSpeedStatus').textContent =
        `Up to ${state.maxMotorRPM.toFixed(1)} RPM at the current driver setting.`;
      if (!dirty[axis] && !busy[axis]) el(axis+'MotorSettingsStatus').textContent = state.settingsError ||
        `${profile?.calibrated ? 'Distance scale confirmed.' : 'Confirm this distance scale to enable inch moves.'}`
          + (axis === 'y' ? ' Y is capped at 200 RPM.' : '');
      if (axis === 'y') {
        for (const id of ['timedY','timedSpeed','pulleyHoldDown','pulleyHoldUp']) el(id).closest('label').hidden = true;
        for (const p of el('pulleySetup').querySelectorAll('p.hint')) {
          if (!p.contains(el('pulleyCount'))) p.hidden = true;
        }
      }
    }
  }
  for (const axis of ['x','y']) {
    for (const suffix of [...Object.keys(fields), 'Calibrated', ...(axis === 'y' ? ['Alarm'] : [])]) {
      el(axis+'Motor'+suffix).addEventListener('input', () => {
        dirty[axis] = true; paint();
        el(axis+'MotorSettingsStatus').textContent = 'Not applied yet.';
      });
    }
    el(axis+'MotorRpm').addEventListener('input', () => {
      speedDirty[axis] = true;
      el(axis+'MotorSpeedStatus').textContent = 'Apply speed before moving. Applying speed disarms this motor.';
    });
    el(axis+'MotorSpeedSave').onclick = async () => {
      const input = el(axis+'MotorRpm'), ui = window.blueInspection;
      if (busy.x || busy.y || !input.reportValidity()) return;
      const state = ui.state()?.roof.data?.[axis+'Axis'];
      if (!state || state.moving || ui.movementBusy() || ui.inspectionActive()) return;
      busy[axis] = true; refresh();
      try {
        if (state.motorArmed) adopt(axis, await ui.sendAxis(axis, {id:window.brickStorage.id(), action:'stop'}));
        const result = await ui.sendAxis(axis, {id:window.brickStorage.id(), action:'stepper-settings',
          settings:{...state.stepperSettings, rpm:input.valueAsNumber}});
        adopt(axis, result); speedDirty[axis] = false; savedSpeed[axis] = undefined;
        el(axis+'MotorFeedback').textContent = 'Speed saved. Arm the motor when ready to move.';
      } catch (error) { el(axis+'MotorFeedback').textContent = error.message; }
      finally { busy[axis] = false; refresh(); }
    };
    el(axis+'MotorSave').onclick = async () => {
      if (busy.x || busy.y) return;
      const profile = {};
      for (const [suffix,key] of Object.entries({...fields,Rpm:'rpm'})) {
        const field = el(axis+'Motor'+suffix);
        if (!field.reportValidity()) return;
        profile[key] = Number(field.value);
      }
      profile.calibrated = el(axis+'MotorCalibrated').checked;
      profile.alarmMode = axis === 'y' ? Number(el('yMotorAlarm').value) : 0;
      busy[axis] = true; refresh();
      try {
        adopt(axis, await window.blueInspection.sendAxis(axis, {id:window.brickStorage.id(), action:'stepper-settings', settings:profile}));
        dirty[axis] = speedDirty[axis] = false;
        el(axis+'MotorFeedback').textContent = 'Calibration saved. Set zero again if the distance scale or direction changed.';
      } catch (error) {el(axis+'MotorSettingsStatus').textContent = error.message;}
      finally {busy[axis] = false; refresh();}
    };
    el(axis+'MotorArm').onclick = async () => {
      if (busy.x || busy.y) return;
      const state = window.blueInspection.state()?.roof.data?.[axis+'Axis'];
      if (!state || state.moving || window.blueInspection.movementBusy() || window.blueInspection.inspectionActive()) return;
      busy[axis] = true; refresh();
      try {
        adopt(axis, await window.blueInspection.sendAxis(axis, {id:window.brickStorage.id(), action:state.motorArmed ? 'stop' : 'arm'}));
        el(axis+'MotorFeedback').textContent = state.motorArmed ? 'Motor disarmed.' : 'Motor armed.';
      } catch (error) {el(axis+'MotorFeedback').textContent = error.message;}
      finally {busy[axis] = false; refresh();}
    };
    el(axis+'MotorEstop').onclick = () => el('stopAll').click();
    for (const [suffix,direction] of [['Positive',1],['Negative',-1]]) {
      el(axis+'Motor'+suffix).onclick = async () => {
        const input = el(axis+'MotorTurns');
        if (!input.reportValidity()) return;
        try {
          el(axis+'MotorFeedback').textContent = 'Moving…';
          await window.blueInspection.motorTurns(axis, direction, input.valueAsNumber);
          el(axis+'MotorFeedback').textContent = 'Commanded motor turn completed.';
        } catch (error) {el(axis+'MotorFeedback').textContent = error.message;}
      };
    }
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') {event.preventDefault();el('stopAll').click();}
  });
  window.addEventListener('inspectionposition', paint);
})();
