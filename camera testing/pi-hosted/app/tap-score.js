'use strict';
(() => {
  const rate = 48000;
  const algorithm = 'fft21600-pre50ms-v2';
  const fftSize = 21600;
  const bands = 64;
  const beforeImpact = 0.05;
  const afterImpact = 0.45;

  function decodeWav(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const word = offset => view.getUint32(offset, false);
    if (view.byteLength < 12 || word(0) !== 0x52494646 || word(8) !== 0x57415645) {
      throw new Error('Choose a WAV recording.');
    }
    let format, data;
    for (let chunk = 12; chunk + 8 <= view.byteLength;) {
      const tag = word(chunk), length = view.getUint32(chunk + 4, true), start = chunk + 8;
      if (start + length > view.byteLength) throw new Error('The WAV recording is incomplete.');
      if (tag === 0x666d7420 && length >= 16) {
        let encoding = view.getUint16(start, true);
        if (encoding === 0xfffe && length >= 40) encoding = view.getUint16(start + 24, true);
        format = {
          encoding, channels: view.getUint16(start + 2, true), rate: view.getUint32(start + 4, true),
          bytesPerFrame: view.getUint16(start + 12, true), bits: view.getUint16(start + 14, true)
        };
      }
      if (tag === 0x64617461) data = {start, length};
      chunk = start + length + (length % 2);
    }
    if (!format || !data || format.rate !== rate || format.channels < 1) {
      throw new Error('Use a WAV recorded at 48 kHz.');
    }
    const {bits, encoding, bytesPerFrame} = format;
    const pcm = {
      8: offset => (view.getUint8(offset) - 128) / 128,
      16: offset => view.getInt16(offset, true) / 32768,
      24: offset => ((view.getUint16(offset, true) | view.getInt8(offset + 2) << 16)) / 8388608,
      32: offset => view.getInt32(offset, true) / 2147483648
    };
    const floats = {32: offset => view.getFloat32(offset, true), 64: offset => view.getFloat64(offset, true)};
    const read = encoding === 1 ? pcm[bits] : encoding === 3 ? floats[bits] : null;
    const bytesPerSample = bits / 8;
    if (!read || bytesPerFrame !== bytesPerSample * format.channels || !data.length || data.length % bytesPerFrame) {
      throw new Error('Use an uncompressed PCM or floating-point WAV.');
    }
    return Float64Array.from({length: data.length / bytesPerFrame}, (_, frame) => {
      let sum = 0;
      for (let channel = 0; channel < format.channels; channel++) {
        const sample = read(data.start + frame * bytesPerFrame + channel * bytesPerSample);
        if (!Number.isFinite(sample)) throw new Error('The WAV contains invalid samples.');
        sum += sample;
      }
      return sum / format.channels;
    });
  }

  function samplesFromBase64(wav) {
    return decodeWav(Uint8Array.from(atob(wav), char => char.charCodeAt(0)));
  }

  function validTimes(times, sampleCount = Infinity) {
    return Array.isArray(times) && times.length === 3 && times.every((time, i) =>
      Number.isFinite(time) && time >= 0 && Math.floor(time * rate) < sampleCount &&
      (i === 0 || time > times[i - 1]));
  }

  function readTimes(csv, sampleCount) {
    const fields = line => line.split(',').map(value => value.trim().replace(/^"|"$/g, ''));
    const [header, ...rows] = csv.replace(/^\uFEFF/, '').trim().split(/\r?\n/).filter(line => line.trim());
    const labels = fields(header || '');
    const impact = labels.indexOf('Impact');
    const timestamp = labels.indexOf('Timestamp (s)');
    const times = [];
    for (const row of rows) {
      const values = fields(row);
      const number = Number(values[impact]);
      if (![1, 2, 3].includes(number) || !values[timestamp] || times[number - 1] !== undefined) {
        throw new Error('The CSV needs one Timestamp (s) for each Impact numbered 1 to 3.');
      }
      times[number - 1] = Number(values[timestamp]);
    }
    if (impact < 0 || timestamp < 0 || rows.length !== 3 || !validTimes(times, sampleCount)) {
      throw new Error('The CSV needs three increasing impact times within the WAV.');
    }
    return times;
  }

  function impactCsv(times) {
    return 'Impact,Timestamp (s)\r\n' + times.map((time, i) => `${i + 1},${time}\r\n`).join('');
  }

  function detectTimes(samples) {
    const frame = 96; // 2 ms. Use the change in the waveform to emphasize impact onsets.
    const energy = Float64Array.from({length: Math.floor(samples.length / frame)}, (_, index) => {
      const start = index * frame;
      let sum = 0;
      for (let i = Math.max(1, start); i < start + frame; i++) {
        sum += (samples[i] - samples[i - 1]) ** 2;
      }
      return Math.sqrt(sum / frame);
    });
    const quiet = energy.slice(0, 65).sort();
    if (!quiet.length) return null;
    const floor = Math.max(quiet[Math.floor(quiet.length / 2)] * 7, 0.00012);
    const candidates = [];
    let last = -Infinity;
    for (let i = 4; i < energy.length - 2; i++) {
      const before = Math.max(floor / 3, ...energy.subarray(i - 4, i));
      if (i - last >= 9 && energy[i] >= floor && energy[i] >= before * 2.5) {
        candidates.push({i, height: Math.log1p(energy[i] / floor)});
        last = i;
      }
    }
    let best = null, bestStrength = -Infinity;
    for (const first of candidates) {
      if (first.i < 75 || first.i > 425) continue;
      const remaining = candidates.filter(candidate => Math.abs(candidate.i - first.i - 250) <= 25);
      const third = candidates.filter(candidate => Math.abs(candidate.i - first.i - 500) <= 25);
      for (const second of remaining) {
        for (const last of third) {
          if (Math.abs(last.i - second.i - 250) > 25 || last.i * frame + afterImpact * rate > samples.length) continue;
          const spacingError = Math.abs(second.i - first.i - 250) + Math.abs(last.i - second.i - 250);
          const strength = first.height + second.height + last.height - spacingError * 0.1;
          if (strength > bestStrength) {
            bestStrength = strength;
            best = [first.i, second.i, last.i].map(i => i * frame / rate);
          }
        }
      }
    }
    return best;
  }

  function burstTimes(samples) {
    const detected = detectTimes(samples);
    if (detected) return {times: detected, source: 'detected'};
    // The recorder reads 0.25 s before triggering the 2 Hz burst.
    const times = [0.25, 0.75, 1.25];
    if ((times.at(-1) + afterImpact) * rate > samples.length || !samples.some(sample => sample !== 0)) return null;
    return {times, source: 'scheduled'};
  }

  // Bluestein computes the exact 21,600-point DFT using a power-of-two
  // convolution. Its internal padding does not change the supplied FFT length.
  const convolutionSize = 2 ** Math.ceil(Math.log2(2 * fftSize - 1));
  const cosines = Float64Array.from({length: convolutionSize / 2}, (_, i) => Math.cos(2 * Math.PI * i / convolutionSize));
  const sines = Float64Array.from({length: convolutionSize / 2}, (_, i) => Math.sin(2 * Math.PI * i / convolutionSize));

  function transform(real, imag, inverse = false) {
    const size = real.length;
    for (let i = 1, reversed = 0; i < size; i++) {
      let bit = size / 2;
      while (reversed & bit) { reversed ^= bit; bit >>= 1; }
      reversed ^= bit;
      if (i < reversed) {
        [real[i], real[reversed]] = [real[reversed], real[i]];
        [imag[i], imag[reversed]] = [imag[reversed], imag[i]];
      }
    }
    for (let width = 2; width <= size; width *= 2) {
      const stride = size / width;
      for (let i = 0; i < width / 2; i++) {
        const cos = cosines[i * stride], sin = (inverse ? 1 : -1) * sines[i * stride];
        for (let left = i; left < size; left += width) {
          const right = left + width / 2;
          const r = real[right] * cos - imag[right] * sin;
          const j = real[right] * sin + imag[right] * cos;
          real[right] = real[left] - r;
          imag[right] = imag[left] - j;
          real[left] += r;
          imag[left] += j;
        }
      }
    }
    if (inverse) {
      for (let i = 0; i < size; i++) { real[i] /= size; imag[i] /= size; }
    }
  }

  const chirpCos = new Float64Array(fftSize), chirpSin = new Float64Array(fftSize);
  const kernelReal = new Float64Array(convolutionSize), kernelImag = new Float64Array(convolutionSize);
  for (let i = 0; i < fftSize; i++) {
    const angle = Math.PI * ((i * i) % (2 * fftSize)) / fftSize;
    chirpCos[i] = kernelReal[i] = Math.cos(angle);
    chirpSin[i] = kernelImag[i] = Math.sin(angle);
    if (i) {
      kernelReal[convolutionSize - i] = chirpCos[i];
      kernelImag[convolutionSize - i] = chirpSin[i];
    }
  }
  transform(kernelReal, kernelImag);

  function fftMagnitude(samples, start, end) {
    const n = end - start;
    let mean = 0;
    for (let i = start; i < end; i++) mean += samples[i];
    mean /= n;
    const real = new Float64Array(convolutionSize), imag = new Float64Array(convolutionSize);
    // Match SciPy's full-slice Hann before NumPy truncates or pads to n=21600.
    for (let i = 0; i < Math.min(n, fftSize); i++) {
      const hann = n === 1 ? 1 : (1 - Math.cos(2 * Math.PI * i / (n - 1))) / 2;
      const sample = (samples[start + i] - mean) * hann;
      real[i] = sample * chirpCos[i];
      imag[i] = -sample * chirpSin[i];
    }
    transform(real, imag);
    for (let i = 0; i < convolutionSize; i++) {
      const r = real[i] * kernelReal[i] - imag[i] * kernelImag[i];
      imag[i] = real[i] * kernelImag[i] + imag[i] * kernelReal[i];
      real[i] = r;
    }
    transform(real, imag, true);
    return Float64Array.from({length: fftSize / 2 + 1}, (_, i) => Math.hypot(
      real[i] * chirpCos[i] + imag[i] * chirpSin[i],
      imag[i] * chirpCos[i] - real[i] * chirpSin[i]));
  }

  function melFilters() {
    const step = 200 / 3, logStep = Math.log(6.4) / 27;
    const first = 20 / step, last = 15 + Math.log((rate / 2) / 1000) / logStep;
    const edges = Array.from({length: bands + 2}, (_, i) => {
      const mel = first + (last - first) * i / (bands + 1);
      return mel < 15 ? mel * step : 1000 * Math.exp(logStep * (mel - 15));
    });
    return Array.from({length: bands}, (_, i) => {
      const lower = edges[i], middle = edges[i + 1], upper = edges[i + 2];
      return Float32Array.from({length: fftSize / 2 + 1}, (_, bin) => {
        const hz = bin * rate / fftSize;
        const triangle = Math.max(0, Math.min((hz - lower) / (middle - lower), (upper - hz) / (upper - middle)));
        return Math.fround(triangle) * 2 / (upper - lower);
      });
    });
  }

  const filters = melFilters();
  function spectrum(samples, times) {
    if (!validTimes(times, samples.length)) throw new Error('Enter three increasing impact times within the WAV.');
    const average = new Float64Array(fftSize / 2 + 1);
    for (const time of times) {
      const start = Math.max(0, Math.trunc((time - beforeImpact) * rate));
      const end = Math.min(Math.trunc((time + afterImpact) * rate), samples.length);
      const fft = fftMagnitude(samples, start, end);
      for (let bin = 0; bin < fft.length; bin++) average[bin] += fft[bin];
    }
    const power = average.map(bin => (bin / times.length) ** 2);
    const db = filters.map(weights => {
      const energy = weights.reduce((sum, weight, bin) => sum + weight * power[bin], 0);
      return 10 * Math.log10(Math.max(1e-10, energy));
    });
    const floor = Math.max(...db) - 80;
    return db.map(value => Math.max(value, floor));
  }

  function compare(baseline, current) {
    return baseline.reduce((sum, value, i) => sum + Math.abs(value - current[i]), 0) / bands;
  }

  window.brickTapScore = {algorithm, decodeWav, samplesFromBase64, validTimes, readTimes, impactCsv, detectTimes, burstTimes, spectrum, compare};
})();
