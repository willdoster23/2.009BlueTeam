'use strict';
(() => {
  let database;
  const urls = new Map();

  function openDatabase() {
    if (!database) database = new Promise((resolve, reject) => {
      const request = indexedDB.open('blue-team-brick-mapper', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('photos', {keyPath: 'id'});
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => { database = null; reject(request.error); };
    });
    return database;
  }

  async function transact(mode, operation) {
    const db = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = db.transaction('photos', mode);
      let result;
      transaction.oncomplete = () => resolve(result);
      transaction.onabort = () => reject(transaction.error || new Error('Photo storage operation cancelled'));
      operation(transaction.objectStore('photos'), value => { result = value; });
    });
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

  async function save(blob, width, height, position) {
    const captured = new Date();
    const photoId = id();
    const name = 'bridge-photo_' + captured.toISOString().replace(/[:.]/g, '-') + '_' + photoId.slice(0, 8) + '.png';
    if (!position) return {name};
    const record = {id: photoId, name, captured_at: captured.toISOString(), width, height, blob,
      ...position, position_source: 'simulated-rooftop', classification_source: 'manual'};
    await transact('readwrite', store => store.add(record));
    return {name, record: view(record)};
  }

  async function label(photoId, status) {
    if (!['good', 'bad'].includes(status)) throw new Error('Choose Good or Bad');
    const record = await transact('readwrite', (store, done) => {
      const request = store.get(photoId);
      request.onsuccess = () => {
        if (!request.result) return;
        const updated = {...request.result, status};
        store.put(updated);
        done(updated);
      };
    });
    if (!record) throw new Error('Photo was not found');
    return view(record);
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
    return {format: 'blue-team-brick-map', version: 2, coordinate_units: 'encoder counts', photos};
  }

  async function restore(data) {
    if (data?.format !== 'blue-team-brick-map' || data.version !== 2 || !Array.isArray(data.photos)) {
      throw new Error('Choose a Brick Mapper backup file');
    }
    const records = data.photos.map(photo => {
      const {id: photoId, name, captured_at, x, y, status, width, height, png_base64} = photo;
      if (typeof photoId !== 'string' || !/^[a-f0-9]{32}$/.test(photoId)
          || typeof name !== 'string' || !/^bridge-photo_[\w.-]+\.png$/.test(name)
          || typeof captured_at !== 'string' || !Number.isFinite(Date.parse(captured_at))
          || ![x, y].every(value => Number.isFinite(value) && Math.abs(value) <= 1e12)
          || !['good', 'bad'].includes(status)
          || ![width, height].every(value => Number.isInteger(value) && value >= 1 && value <= 8192)
          || typeof png_base64 !== 'string' || !png_base64.startsWith('iVBORw0KGgo')
          || png_base64.length > 16 * 1024 * 1024) {
        throw new Error('The backup contains an invalid photo');
      }
      const bytes = Uint8Array.from(atob(png_base64), byte => byte.charCodeAt(0));
      return {id: photoId, name, captured_at, x, y, status, width, height,
        position_source: photo.position_source === 'simulated-rooftop' ? 'simulated-rooftop' : 'manual',
        classification_source: 'manual', blob: new Blob([bytes], {type: 'image/png'})};
    });
    return transact('readwrite', (store, done) => {
      let added = 0;
      done(added);
      for (const record of records) {
        const request = store.getKey(record.id);
        request.onsuccess = () => {
          if (request.result !== undefined) return;
          store.put(record);
          done(++added);
        };
      }
    });
  }

  window.brickStorage = {id, save, label, download, backup, restore,
    list: async () => (await all()).sort((a, b) => a.captured_at.localeCompare(b.captured_at)).map(view)};
})();
