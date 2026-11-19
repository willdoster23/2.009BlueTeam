"""Capture a short WAV from the same microphone used for live listening."""
from array import array
import base64
import io
import os
import select
import subprocess
import sys
import time
import wave

RATE = 48000
PRE_TAP_BYTES = int(RATE * 0.25) * 2
POST_TAP_BYTES = int(RATE * 1.25) * 2
MICROPHONE_URL = 'rtsp://127.0.0.1:8554/usbaudio'


def read_pcm(stream, size, timeout):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise TimeoutError('Microphone capture timed out')
        chunk = os.read(stream.fileno(), size - len(data))
        if not chunk:
            raise OSError('Microphone stream ended')
        data.extend(chunk)
    return data


def wav_bytes(pcm, *, normalize=True):
    # Lift quiet taps while capping gain and leaving headroom.
    if normalize:
        samples = array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        peak = max(map(abs, samples), default=0)
        if peak:
            gain = min(16, 26000 / peak)
            if gain > 1:
                samples = array('h', (round(sample * gain) for sample in samples))
        if sys.byteorder != 'little':
            samples.byteswap()
        pcm = samples.tobytes()
    out = io.BytesIO()
    with wave.open(out, 'wb') as clip:
        clip.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        clip.writeframes(pcm)
    return out.getvalue()


def record_tap(trigger, *, burst_span=0):
    command = ['/usr/bin/ffmpeg', '-nostdin', '-loglevel', 'error',
               '-rtsp_transport', 'tcp', '-timeout', '2000000',
               '-analyzeduration', '0', '-probesize', '32', '-i', MICROPHONE_URL,
               '-map', '0:a:0', '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le',
               '-flush_packets', '1', '-f', 's16le', 'pipe:1']
    try:
        recorder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return 503, {'error': 'Microphone recorder unavailable. No tap was sent.'}
    try:
        try:
            pcm = read_pcm(recorder.stdout, PRE_TAP_BYTES, 3)
        except OSError:
            return 503, {'error': 'Microphone unavailable. No tap was sent.'}
        status, result = trigger()
        if status != 200:
            return status, result
        try:
            post_bytes = POST_TAP_BYTES + int(RATE * burst_span) * 2
            pcm.extend(read_pcm(recorder.stdout, post_bytes, 3))
        except OSError:
            return status, {**result, 'audioError': 'Tap sent, but microphone capture was interrupted.'}
        return status, {**result, 'audioWav': base64.b64encode(wav_bytes(pcm, normalize=not burst_span)).decode('ascii')}
    finally:
        recorder.kill()
        recorder.wait()
        recorder.stdout.close()
