"""Manage the USB camera publisher and its selectable raw capture modes."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
import uuid


RUNTIME = Path('/run/brick-camera')
DEFAULT = '1280x720'
ELP_SIZES = ('1920x1080', '1280x1024', DEFAULT, '1024x768', '800x600', '640x480', '320x240')
STREAM = 'rtsp://127.0.0.1:8554/usbtest'
request_lock = threading.Lock()


def read_json(name):
    try:
        return json.loads((RUNTIME / name).read_text())
    except (ValueError, OSError):
        return {}


def write_json(name, value):
    target = RUNTIME / name
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.replace(target)


def status():
    state = read_json('status.json')
    pending = read_json('request.json').get('id')
    return {**state, 'switching': bool(pending and pending != state.get('id'))}


def request_mode(payload):
    with request_lock:
        state = status()
        if not state.get('modes'):
            return 503, {'error': 'Camera capture modes are unavailable.'}
        last = read_json('request.json')
        if last.get('id') and last['id'] != state.get('id'):
            return 409, {'error': 'Wait for the current camera switch to finish.'}
        mode = payload.get('mode')
        if not isinstance(mode, str) or mode not in state['modes']:
            return 400, {'error': 'Choose a capture mode supported by the connected camera.'}
        if mode == state.get('active'):
            return 200, {'active': mode}
        request = {'mode': mode, 'id': uuid.uuid4().hex}
        write_json('request.json', request)
        return 202, request


def mjpeg_modes(descriptions):
    modes = {}
    mjpeg = False
    size = None
    for line in descriptions.splitlines():
        if re.match(r"\s*\[\d+\]:", line):
            mjpeg = "'MJPG'" in line
            size = None
        elif mjpeg and (match := re.search(r'Size: Discrete (\d+x\d+)', line)):
            size = match[1]
            modes.setdefault(size, [])
        elif size and (match := re.search(r'\(([\d.]+) fps\)', line)):
            modes[size].append(float(match[1]))
    return {size: max((rate for rate in rates if rate <= 30), default=min(rates, default=30))
            for size, rates in modes.items()}


def camera_modes(device):
    try:
        result = subprocess.run(['/usr/bin/v4l2-ctl', '-d', device, '--list-formats-ext'],
                                capture_output=True, text=True, check=True, timeout=3)
        return mjpeg_modes(result.stdout) or {DEFAULT: 30}
    except (OSError, subprocess.SubprocessError):
        return {DEFAULT: 30}


def publish(device, mode, fps):
    output_rate = ['-r', '30'] if fps > 30 else []
    return subprocess.Popen(['/usr/bin/ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'warning',
                             '-f', 'v4l2', '-input_format', 'mjpeg', '-framerate', f'{fps:g}',
                             '-video_size', mode, '-i', device, '-an', *output_rate, '-c:v', 'libx264',
                             '-pix_fmt', 'yuv420p', '-preset', 'ultrafast', '-tune', 'zerolatency',
                             '-profile:v', 'baseline', '-b:v', '2M', '-g', '30', '-bf', '0',
                             '-rtsp_transport', 'tcp', '-f', 'rtsp', STREAM])


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def confirms_mode(process, mode):
    deadline = time.monotonic() + 7
    while process.poll() is None and time.monotonic() < deadline:
        try:
            result = subprocess.run(['/usr/bin/ffprobe', '-v', 'error', '-rtsp_transport', 'tcp',
                                     '-analyzeduration', '200000', '-probesize', '32768',
                                     '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                                     '-of', 'csv=p=0:s=x', STREAM], capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip() == mode
        except subprocess.TimeoutExpired:
            pass
        time.sleep(.25)
    return False


def run(device):
    modes = camera_modes(device)
    active = DEFAULT if DEFAULT in modes else next(iter(modes))
    state = {'modes': list(modes), 'active': active, 'id': read_json('request.json').get('id')}
    process = publish(device, active, modes[active])
    try:
        write_json('status.json', state)
        while process.poll() is None:
            time.sleep(.2)
            request = read_json('request.json')
            if request.get('id') == state['id']:
                continue
            mode = request.get('mode')
            if mode not in modes:
                continue
            stop(process)
            process = publish(device, mode, modes[mode])
            error = None
            if confirms_mode(process, mode):
                active = mode
            else:
                stop(process)
                process = publish(device, active, modes[active])
                error = f'The camera could not stream {mode}. Restoring {active}.'
            state.update(active=active, id=request['id'], error=error)
            write_json('status.json', state)
    finally:
        stop(process)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('device')
    options = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: exit(0))
    run(options.device)
