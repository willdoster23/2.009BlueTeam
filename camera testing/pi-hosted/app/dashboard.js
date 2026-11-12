'use strict';
(() => {
  const el = id => document.getElementById(id);
  const token = '__PHOTO_TOKEN__';
  let latest = null, receivedAt = 0, polling = false, sending = false;
  let tapId = null, tapBusy = false, renewing = false;
  const positionReady = () => !!latest?.roof.reachable && performance.now() - receivedAt < 1800;
  window.blueInspection = {positionReady};

  function indicator(id, text, online) {
    el(id).textContent = text;
    el(id).className = online ? 'online' : 'offline';
  }

  function paint() {
    const fresh = performance.now() - receivedAt < 1800;
    const roof = fresh && latest?.roof.reachable ? latest.roof.data : null;
    const robot = fresh && latest?.robot.reachable ? latest.robot.data : null;
    const linked = !!roof?.exchange.connected;
    indicator('roofState', 'Winch controller · ' + (roof ? 'connected' : 'offline'), !!roof);
    indicator('robotState', 'Camera unit · ' + (robot ? 'connected' : 'offline'), !!robot);
    indicator('exchangeState', 'Data link · ' + (linked ? 'connected' : 'waiting'), linked);
    const tap = robot?.tap;
    const idle = tap?.available && tap.mode === 'idle';
    el('tapOnce').disabled = tapBusy || !idle;
    el('tapAuto').disabled = tapBusy || !idle;
    el('tapHz').disabled = tap?.mode === 'auto';
    el('tapStop').disabled = !tap?.available || (idle && !tapBusy);
    if (!tapBusy && !el('tapStatus').dataset.message) {
      el('tapStatus').textContent = tap?.available
        ? (tap.mode === 'auto' ? 'Tapping at ' + tap.hz + ' Hz' : tap.mode === 'single' ? 'Tapping once…' : 'Tap ready')
        : 'Robot tap controller unavailable';
    }
    el('encoderX').value = roof?.position.x ?? '';
    el('encoderY').value = roof?.position.y ?? '';
    el('positionStatus').textContent = roof
      ? 'Sample counts from the controller · updated twice per second'
      : 'Waiting for controller. Sample position is unavailable.';
    el('winchStart').disabled = sending || !roof || !linked || roof.relays.enabled;
    for (const id of ['winchUp', 'winchDown']) el(id).disabled = sending || !roof || !linked || !roof.relays.enabled;
    el('winchStop').disabled = !roof;
    for (let n = 1; n <= 4; n++) {
      const active = roof?.relays.channels.find(channel => channel.channel === n)?.closed;
      el('relay' + n).classList.toggle('on', !!active);
    }
    if (!roof || (!sending && !el('winchFeedback').dataset.command)) {
      el('winchFeedback').textContent = roof
        ? (roof.relays.enabled ? 'Simulation enabled. Up and Down change the sample Y count.' : 'Press Start to enable the simulation.')
        : 'Waiting for controller…';
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
    } catch {
      latest = null;
    } finally {
      polling = false;
      paint();
    }
  }

  async function command(action) {
    if (sending && action !== 'stop') return;
    sending = true;
    el('winchFeedback').dataset.command = 'true';
    el('winchFeedback').textContent = 'Sending ' + action + '…';
    paint();
    try {
      const response = await fetch('/api/winch', {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Photo-Token': token},
        body: JSON.stringify({action, id: window.brickStorage.id()}), signal: AbortSignal.timeout(3500)
      });
      const result = await response.json();
      el('winchFeedback').textContent = response.ok ? result.message : result.error;
      const channel = {up: 1, down: 2, start: 3, stop: 4}[action];
      if (response.ok) {
        el('relay' + channel).classList.add('on');
        setTimeout(() => el('relay' + channel).classList.remove('on'), 300);
      }
    } catch {
      el('winchFeedback').textContent = 'Acknowledgement unavailable. Check controller status before retrying.';
    } finally {
      sending = false;
      poll();
      setTimeout(() => { delete el('winchFeedback').dataset.command; paint(); }, 3000);
    }
  }
  for (const action of ['start', 'up', 'down', 'stop']) {
    el('winch' + action[0].toUpperCase() + action.slice(1)).onclick = () => command(action);
  }
  async function tapRequest(action, id, hz, keepalive = false) {
    const response = await fetch('/api/tap', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-Photo-Token': token},
      body: JSON.stringify({action, id, hz}), keepalive, signal: AbortSignal.timeout(1500)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    return result;
  }

  async function tapCommand(action) {
    if (tapBusy && action !== 'stop') return;
    if (action === 'auto' && !el('tapHz').reportValidity()) return;
    const id = window.brickStorage.id();
    if (action === 'stop') tapId = null;
    if (action === 'auto') tapId = id;
    tapBusy = true;
    delete el('tapStatus').dataset.message;
    paint();
    try {
      await tapRequest(action, id, el('tapHz').valueAsNumber);
      el('tapStatus').textContent = action === 'single' ? 'Single tap accepted' :
        action === 'stop' ? 'Tapping stopped' : 'Automatic tapping started';
    } catch (error) {
      if (tapId === id) tapId = null;
      el('tapStatus').textContent = error.message || 'Tap acknowledgement unavailable';
    } finally {
      el('tapStatus').dataset.message = 'true';
      setTimeout(() => { delete el('tapStatus').dataset.message; paint(); }, 1300);
      tapBusy = false;
      poll();
    }
  }

  function stopLeaving() {
    if (!tapId) return;
    const id = tapId;
    tapId = null;
    tapRequest('stop', id, undefined, true).catch(() => {});
  }

  el('tapOnce').onclick = () => tapCommand('single');
  el('tapAuto').onclick = () => tapCommand('auto');
  el('tapStop').onclick = () => tapCommand('stop');
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopLeaving(); });
  window.addEventListener('pagehide', stopLeaving);
  setInterval(async () => {
    if (!tapId || tapBusy || renewing || document.hidden) return;
    const id = tapId;
    renewing = true;
    try {
      await tapRequest('renew', id);
    } catch {
      if (tapId === id) tapId = null;
    } finally {
      renewing = false;
    }
  }, 400);

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
