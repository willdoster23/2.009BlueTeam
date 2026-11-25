'use strict';
(() => {
  let database;
  const urls = new Map();
  const jobKey = 'brick-mapper-job';
  const job = localStorage.getItem(jobKey);

  function assertCurrentJob() {
    if (localStorage.getItem(jobKey) !== job) {
      throw new Error('A new job was started. Reload Brick Mapper.');
    }
  }

  function openDatabase() {
    if (!database) {
      let opening;
      opening = new Promise((resolve, reject) => {
        const request = indexedDB.open('blue-team-brick-mapper', 3);
        let settled = false;
        const fail = error => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          if (database === opening) database = null;
          reject(error);
        };
        const timer = setTimeout(() => fail(new Error('Browser storage did not respond. Reload this page or download the recording.')), 8000);
        request.onupgradeneeded = event => {
          if (!event.oldVersion) request.result.createObjectStore('photos', {keyPath: 'id'});
          if (event.oldVersion < 2) request.result.createObjectStore('mapping');
          request.result.createObjectStore('recordings', {keyPath: 'id'});
        };
        request.onsuccess = () => {
          const db = request.result;
          if (settled) { db.close(); return; }
          settled = true;
          clearTimeout(timer);
          db.onversionchange = () => { db.close(); if (database === opening) database = null; };
          resolve(db);
        };
        request.onblocked = () => fail(new Error('Close older Brick Mapper tabs and reload to update the saved map.'));
        request.onerror = () => fail(request.error);
      });
      database = opening;
    }
    return database;
  }

  async function transact(mode, operation, storeName = 'photos', otherStores = []) {
    const db = await openDatabase();
    if (mode === 'readwrite') assertCurrentJob();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction([storeName, ...otherStores], mode);
      let result, settled = false;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (error) reject(error); else resolve(result);
      };
      const timer = setTimeout(() => {
        try { transaction.abort(); } catch {}
        finish(new Error('Saving on this computer timed out. Retry saving or download the recording before closing this page.'));
      }, 8000);
      transaction.oncomplete = () => finish();
      transaction.onabort = () => finish(transaction.error || new Error('Browser storage operation cancelled.'));
      try {
        operation(transaction.objectStore(storeName), value => { result = value; });
      } catch (error) {
        try { transaction.abort(); } catch {}
        finish(error);
      }
    });
  }

  async function clearJob(resetBaseline) {
    await transact('readwrite', store => {
      store.clear();
      store.transaction.objectStore('mapping').clear();
      store.transaction.objectStore('recordings').clear();
    }, 'photos', ['mapping', 'recordings']);
    for (const url of urls.values()) URL.revokeObjectURL(url);
    urls.clear();
    resetBaseline();
    localStorage.setItem(jobKey, id());
  }

  function id() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('');
  }

  function view({blob, ...record}) {
    if (!urls.has(record.id)) urls.set(record.id, URL.createObjectURL(blob));
    return {...record, url: urls.get(record.id)};
  }

  function all() {
    return transact('readonly', (store, done) => {
      const request = store.getAll();
      request.onsuccess = () => done(request.result);
    });
  }

  function validRecording(record) {
    return record && typeof record.id === 'string' && /^[a-f0-9]{32}(?:-[1-8])?$/.test(record.id)
      && typeof record.captured_at === 'string' && Number.isFinite(Date.parse(record.captured_at))
      && typeof record.burst === 'boolean' && typeof record.wav === 'string'
      && record.wav.startsWith('UklGR') && record.wav.length <= 512 * 1024;
  }

  function saveRecording(record) {
    if (!validRecording(record)) throw new Error('The audio recording is invalid.');
    return transact('readwrite', store => store.put(record), 'recordings');
  }

  async function listRecordings() {
    const saved = await transact('readonly', (store, done) => {
      const request = store.getAll();
      request.onsuccess = () => done(request.result);
    }, 'recordings');
    const recordings = new Map(saved.map(record => [record.id, record]));
    for (const photo of await all()) {
      for (const [index, wav] of (photo.tap_audio || []).entries()) {
        const id = photo.id + (index ? '-' + index : '');
        if (!recordings.has(id)) recordings.set(id, {id, wav, burst: !!photo.tap_burst,
          captured_at: photo.captured_at, peakPwm: photo.tap_pwm, peakMs: photo.tap_peak_ms});
      }
    }
    return [...recordings.values()].sort((a, b) => a.captured_at.localeCompare(b.captured_at));
  }

  function brickKey({course, column, source}) {
    return `${source}:${course}:${column}`;
  }

  function reference() {
    return transact('readonly', (store, done) => {
      const request = store.get('reference');
      request.onsuccess = () => done(request.result);
    }, 'mapping');
  }

  function saveReference(value) {
    return transact('readwrite', store => store.put(value, 'reference'), 'mapping');
  }

  function route() {
    return transact('readonly', (store, done) => {
      const request = store.get('route');
      request.onsuccess = () => done(request.result);
    }, 'mapping');
  }

  function saveRoute(value) {
    if (!validRoute(value)) throw new Error('Use valid route coverage and spacing.');
    return transact('readwrite', store => store.put(value, 'route'), 'mapping');
  }

  function validRoute(value) {
    const fields = value?.inputs;
    if (value?.version !== 1 || typeof value.hardware !== 'boolean'
        || !['center', 'edge'].includes(value.zone) || !fields
        || !['count', 'limit'].includes(fields.routeCoverage)
        || (fields.routeDirection !== undefined && !['left', 'right'].includes(fields.routeDirection))) return false;
    const rows = Number(fields.routeRows), columns = Number(fields.routeColumns);
    const spacing = text => text !== '' && Number.isFinite(Number(text)) && Number(text) >= .1 && Number(text) <= 24;
    const limit = fields.routeYLimit;
    return (fields.routeCoverage === 'limit' || (Number.isSafeInteger(rows) && rows >= 1))
      && Number.isInteger(columns) && columns >= 1 && columns <= 12
      && spacing(fields.routeYSpacing) && (columns === 1 || spacing(fields.routeXSpacing))
      && ['routeRows', 'routeColumns', 'routeXSpacing', 'routeYSpacing', 'routeYLimit'].every(key => typeof fields[key] === 'string')
      && (limit === '' ? !value.hardware && fields.routeCoverage === 'count'
        : Number.isFinite(Number(limit)) && Number(limit) >= 0);
  }

  function reserveTap(brick, zone) {
    return transact('readwrite', (store, done) => {
      const key = `tap:${brickKey(brick)}:${zone}`;
      const request = store.getKey(key);
      request.onsuccess = () => {
        const available = request.result === undefined;
        if (available) store.add(true, key);
        done(available);
      };
    }, 'mapping');
  }

  function releaseTap(brick, zone) {
    return transact('readwrite', store => store.delete(`tap:${brickKey(brick)}:${zone}`), 'mapping');
  }

  const statuses = ['good', 'bad', 'visual', 'review', 'unknown'];

  async function save(blob, width, height, position, inspection = {}) {
    const captured = new Date();
    const photoId = id();
    const name = 'bridge-photo_' + captured.toISOString().replace(/[:.]/g, '-') + '_' + photoId.slice(0, 8) + '.png';
    if (!position) return {name};
    const {x, y, status} = position;
    const record = {id: photoId, name, captured_at: captured.toISOString(), width, height, blob,
      x, y, status, position_source: 'simulated-rooftop', classification_source: 'manual', ...inspection};
    await transact('readwrite', store => store.add(record));
    return {name, record: view(record)};
  }

  function positionMetadata({xInches, yInches, xZeroInches, source, yZeroed, yMoveNote, xEstimated}) {
    if (![xInches, yInches].every(Number.isFinite)) return {};
    return {x_inches: xInches, y_inches: yInches, y_zeroed: yZeroed === true,
      x_estimated: xEstimated === true || source === 'outrigger-stepper-x-pulley-y',
      ...(Number.isFinite(xZeroInches) ? {x_zero_inches: xZeroInches} : {}),
      ...(typeof yMoveNote === 'string' && yMoveNote ? {notes: yMoveNote} : {}),
      position_source: ['x-encoder-rooftop', 'outrigger-encoders', 'outrigger-timed', 'outrigger-stepper-x-pulley-y'].includes(source) ? source : 'simulated-rooftop'};
  }

  async function updatePhoto(photoId, changes, ifNoAnalysis = false) {
    const record = await transact('readwrite', (store, done) => {
      const request = store.get(photoId);
      request.onsuccess = () => {
        if (!request.result) return;
        if (ifNoAnalysis && request.result.tap_analysis &&
            request.result.tap_analysis.algorithm === changes.tap_analysis?.algorithm) {
          done(request.result);
          return;
        }
        const updated = {...request.result, ...changes};
        store.put(updated);
        done(updated);
      };
    });
    if (!record) throw new Error('Photo was not found');
    return view(record);
  }

  function label(photoId, status, review = {}) {
    if (!statuses.includes(status)) throw new Error('Choose an inspection result');
    return updatePhoto(photoId, {status, ...review});
  }

  function updateTapAnalysis(photoId, analysis, {ifMissing = false} = {}) {
    return updatePhoto(photoId, {tap_analysis: analysis}, ifMissing);
  }

  const unitNumber = value => typeof value === 'number' && value >= 0 && value <= 1;
  const validPoint = value => Array.isArray(value) && value.length === 2 && value.every(unitNumber);
  const validCrop = value => Array.isArray(value) && value.length === 4 && value.every(unitNumber)
    && value[2] > 0 && value[3] > 0 && value[0] + value[2] <= 1 && value[1] + value[3] <= 1;
  const validBrick = brick => brick && ['preview', 'measured'].includes(brick.source)
    && [brick.course, brick.column].every(value => Number.isSafeInteger(value) && Math.abs(value) < 1e9);
  const validZone = zone => ['center', 'edge'].includes(zone);

  function updatePhotoGuide(photoId, {point, crop}) {
    if (!validPoint(point) || (crop && !validCrop(crop))) throw new Error('Choose a tap point and a valid brick outline.');
    return updatePhoto(photoId, {tap_point: point, brick_crop: crop});
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  async function exportPhotos() {
    const photos = await all();
    const local = [], directory = [];
    const encoder = new TextEncoder();
    const crcTable = Uint32Array.from({length: 256}, (_, value) => {
      for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
      return value;
    });
    let offset = 0, directorySize = 0;
    if (photos.length > 65535) throw new Error('There are too many photos for one ZIP.');
    // PNGs are already compressed, so store the original bytes in a ZIP.
    for (const {blob, name} of photos) {
      const filename = encoder.encode(name);
      if (offset + blob.size + 30 + filename.length > 0xffffffff) {
        throw new Error('The photos exceed the 4 GB ZIP limit.');
      }
      let crc = 0xffffffff;
      for (const byte of new Uint8Array(await blob.arrayBuffer())) {
        crc = (crc >>> 8) ^ crcTable[(crc ^ byte) & 255];
      }
      const header = new DataView(new ArrayBuffer(30));
      header.setUint32(0, 0x04034b50, true);
      header.setUint16(4, 20, true);
      header.setUint16(12, 33, true); // ZIP's minimum valid date, 1980-01-01.
      header.setUint32(14, crc ^ 0xffffffff, true);
      header.setUint32(18, blob.size, true);
      header.setUint32(22, blob.size, true);
      header.setUint16(26, filename.length, true);
      const entry = new DataView(new ArrayBuffer(46));
      entry.setUint32(0, 0x02014b50, true);
      entry.setUint16(4, 20, true);
      new Uint8Array(entry.buffer, 6, 26).set(new Uint8Array(header.buffer, 4, 26));
      entry.setUint32(42, offset, true);
      local.push(header.buffer, filename, blob);
      directory.push(entry.buffer, filename);
      offset += 30 + filename.length + blob.size;
      directorySize += 46 + filename.length;
    }
    if (offset + directorySize + 22 > 0xffffffff) throw new Error('The photos exceed the 4 GB ZIP limit.');
    const end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true);
    end.setUint16(8, photos.length, true);
    end.setUint16(10, photos.length, true);
    end.setUint32(12, directorySize, true);
    end.setUint32(16, offset, true);
    download(new Blob([...local, ...directory, end.buffer], {type: 'application/zip'}),
      'brick-photos-' + new Date().toISOString().replace(/[:.]/g, '-') + '.zip');
  }

  function audioPlayer(wav, label) {
    const player = document.createElement('audio');
    const blob = new Blob([Uint8Array.from(atob(wav), char => char.charCodeAt(0))], {type: 'audio/wav'});
    const url = URL.createObjectURL(blob);
    player.src = url;
    player.controls = true;
    player.preload = 'none';
    player.setAttribute('aria-label', label);
    player.addEventListener('play', () => {
      for (const other of document.querySelectorAll('audio')) {
        if (other !== player) other.pause();
      }
    });
    return player;
  }

  function clearAudio(container) {
    for (const player of container.querySelectorAll('audio')) {
      player.pause();
      URL.revokeObjectURL(player.src);
    }
    container.replaceChildren();
  }

  function showTapRecordings(container, record) {
    clearAudio(container);
    for (const [index, wav] of (record.tap_audio || []).entries()) {
      const label = record.tap_burst ? 'Three taps at 2 Hz' : 'Tap ' + (index + 1);
      const player = audioPlayer(wav, label);
      const download = document.createElement('a');
      download.href = player.src;
      const suffix = record.tap_burst ? 'three-taps' : 'tap-' + (index + 1);
      download.download = record.name.replace(/\.png$/, '-' + suffix + '.wav');
      download.textContent = 'Download ' + (record.tap_burst ? 'three-tap WAV' : label + ' WAV');
      container.append(player, download);
    }
  }

  function encode(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  async function backup() {
    const photos = [];
    for (const {blob, ...record} of await all()) {
      photos.push({...record, png_base64: await encode(blob)});
    }
    const keys = await transact('readonly', (store, done) => {
      const request = store.getAllKeys();
      request.onsuccess = () => done(request.result);
    }, 'mapping');
    return {format: 'blue-team-brick-map', version: 2, coordinate_units: 'encoder counts', photos,
      recordings: await listRecordings(),
      wall_reference: await reference(), inspection_route: await route(), tap_reservations: keys.filter(key => key.startsWith('tap:')),
      tap_baseline: window.brickTapAnalysis?.baseline()};
  }

  async function restore(data) {
    if (data?.format !== 'blue-team-brick-map' || data.version !== 2 || !Array.isArray(data.photos)) {
      throw new Error('Choose a Brick Mapper backup file');
    }
    const currentReference = await reference();
    if (currentReference?.wallId && data.wall_reference?.wallId && currentReference.wallId !== data.wall_reference.wallId) {
      throw new Error('This backup belongs to a different wall. Open it in a new job to keep brick addresses separate.');
    }
    const records = data.photos.map(photo => {
      const {id: photoId, name, captured_at, x, y, status, width, height, png_base64} = photo;
      if (typeof photoId !== 'string' || !/^[a-f0-9]{32}$/.test(photoId)
          || typeof name !== 'string' || !/^bridge-photo_[\w.-]+\.png$/.test(name)
          || typeof captured_at !== 'string' || !Number.isFinite(Date.parse(captured_at))
          || ![x, y].every(value => Number.isFinite(value) && Math.abs(value) <= 1e12)
          || !statuses.includes(status)
          || ![width, height].every(value => Number.isInteger(value) && value >= 1 && value <= 8192)
          || typeof png_base64 !== 'string' || !png_base64.startsWith('iVBORw0KGgo')
          || png_base64.length > 16 * 1024 * 1024) {
        throw new Error('The backup contains an invalid photo');
      }
      const bytes = Uint8Array.from(atob(png_base64), byte => byte.charCodeAt(0));
      const tapAudio = photo.tap_audio ?? [];
      const burst = photo.tap_burst === true;
      if (!Array.isArray(tapAudio) || tapAudio.length > (burst ? 1 : 9) || tapAudio.some(clip =>
        typeof clip !== 'string' || !clip.startsWith('UklGR') || clip.length > (burst ? 512 * 1024 : 262144))) {
        throw new Error('The backup contains invalid tap audio');
      }
      return {id: photoId, name, captured_at, x, y, status, width, height, tap_audio: tapAudio, tap_burst: burst,
        ...(Number.isInteger(photo.tap_pwm) && photo.tap_pwm >= 0 && photo.tap_pwm <= 255 ? {tap_pwm: photo.tap_pwm} : {}),
        ...(Number.isInteger(photo.tap_peak_ms) && photo.tap_peak_ms >= 10 && photo.tap_peak_ms <= 150 ? {tap_peak_ms: photo.tap_peak_ms} : {}),
        ...(validBrick(photo.brick) ? {brick: photo.brick} : {}),
        ...(validZone(photo.tap_zone) ? {tap_zone: photo.tap_zone} : {}),
        ...(validPoint(photo.tap_point) ? {tap_point: photo.tap_point} : {}),
        ...(validCrop(photo.brick_crop) ? {brick_crop: photo.brick_crop} : {}),
        ...(burst && window.brickTapAnalysis?.validAnalysis(photo.tap_analysis, true) ? {tap_analysis: photo.tap_analysis} : {}),
        ...positionMetadata({xInches: photo.x_inches, yInches: photo.y_inches,
          xZeroInches: photo.x_zero_inches, xEstimated: photo.x_estimated,
          source: photo.position_source, yZeroed: photo.y_zeroed}),
        notes: typeof photo.notes === 'string' ? photo.notes.slice(0, 2000) : '',
        flagged: photo.flagged === true, simulated: photo.simulated === true,
        position_source: ['simulated-rooftop', 'x-encoder-rooftop', 'outrigger-encoders', 'outrigger-timed', 'outrigger-stepper-x-pulley-y'].includes(photo.position_source)
          ? photo.position_source : 'manual',
        classification_source: ['tap-review', 'pending'].includes(photo.classification_source)
          ? photo.classification_source : 'manual',
        blob: new Blob([bytes], {type: 'image/png'})};
    });
    const recordings = data.recordings ?? [];
    if (!Array.isArray(recordings) || !recordings.every(validRecording)) {
      throw new Error('The backup contains invalid audio recordings.');
    }
    return transact('readwrite', (store, done) => {
      let added = 0;
      done(added);
      const mapping = store.transaction.objectStore('mapping');
      const audio = store.transaction.objectStore('recordings');
      for (const recording of recordings) {
        const request = audio.getKey(recording.id);
        request.onsuccess = () => { if (request.result === undefined) audio.put(recording); };
      }
      for (const key of Array.isArray(data.tap_reservations) ? data.tap_reservations : []) {
        if (typeof key === 'string' && /^tap:(preview|measured):-?\d{1,9}:-?\d{1,9}:(center|edge)$/.test(key)) mapping.put(true, key);
      }
      const ref = data.wall_reference;
      if (ref && [ref.x, ref.y, ref.xStep, ref.yStep, ref.yZero].every(Number.isFinite)
          && [ref.xStep, ref.yStep].every(step => step >= 0.1 && step <= 24) && typeof ref.bootId === 'string'
          && (ref.xZero === undefined || Number.isFinite(ref.xZero))
          && (ref.xEpoch === undefined || (typeof ref.xEpoch === 'string' && ref.xEpoch.length <= 64))) {
        const request = mapping.getKey('reference');
        request.onsuccess = () => { if (request.result === undefined) mapping.put(ref, 'reference'); };
      }
      if (validRoute(data.inspection_route)) {
        const request = mapping.getKey('route');
        request.onsuccess = () => { if (request.result === undefined) mapping.put(data.inspection_route, 'route'); };
      }
      for (const record of records) {
        if (record.brick && record.tap_zone) mapping.put(true, `tap:${brickKey(record.brick)}:${record.tap_zone}`);
        const request = store.getKey(record.id);
        request.onsuccess = () => {
          if (request.result !== undefined) return;
          store.put(record);
          done(++added);
        };
      }
    }, 'photos', ['mapping', 'recordings']);
  }

  window.brickStorage = {id, jobKey, assertCurrentJob, clearJob, save, positionMetadata, label, updateTapAnalysis,
    saveRecording, listRecordings,
    brickKey, reference, saveReference, route, saveRoute, validRoute, reserveTap, releaseTap, updatePhotoGuide, download, exportPhotos, backup, restore, audioPlayer, clearAudio, showTapRecordings,
    list: async () => (await all()).sort((a, b) => a.captured_at.localeCompare(b.captured_at)).map(view)};
})();
