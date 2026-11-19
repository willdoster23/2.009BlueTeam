"""Brick Mapper preview bound to this computer only, using simulated controllers."""
from array import array
import base64
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import signal
import sys
from tempfile import TemporaryDirectory
import threading
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'pi')]

from agent import AXIS_ROUTES, BenchState, stop_service
from camera import DEFAULT, ELP_SIZES
from tap import peak_pwm, peak_ms
from outrigger import MOTION_LEASE, PULLEY_COUNTS, NanoAxis, PulleyAxis, Reading
from server import Handler
from winch import FOUR_CHANNEL_PINS, WinchRelays
from x_axis import MM_PER_COUNT

ADDRESS = ('127.0.0.1', 8765)
HOST = f'{ADDRESS[0]}:{ADDRESS[1]}'
X_COUNTS_PER_SECOND = 3 * 25.4 / MM_PER_COUNT
Y_COUNTS_PER_SECOND = 3 * PULLEY_COUNTS / math.tau


class PreviewOutput:
    def __init__(self, change):
        self.change = change

    def on(self):
        self.change(True)

    def off(self):
        self.change(False)

    close = off


@dataclass
class PreviewXMove:
    start: int
    distance: int
    began: float
    expires: float


class PreviewEncoders:
    """Model position as the relay opens or closes and while the X motor moves."""

    def __init__(self):
        self.lock = threading.Lock()
        self.x_count, self.x_tag = 0, 0
        self.x_state, self.x_move = 'idle', None
        self.y_count, self.y_direction = 0.0, 0
        self.y_updated = time.monotonic()

    def output(self, pin, **_):
        direction = {FOUR_CHANNEL_PINS['up']: -1, FOUR_CHANNEL_PINS['down']: 1}.get(pin, 0)
        return PreviewOutput(lambda on: self.set_winch(direction if on else 0))

    def _advance_y(self, now):
        self.y_count += (now - self.y_updated) * self.y_direction * Y_COUNTS_PER_SECOND
        self.y_updated = now

    def set_winch(self, direction):
        with self.lock:
            self._advance_y(time.monotonic())
            self.y_direction = direction

    def _advance_x(self, now):
        move = self.x_move
        if move is None:
            return
        elapsed = min(now, move.expires) - move.began
        travelled = min(abs(move.distance), round(elapsed * X_COUNTS_PER_SECOND))
        self.x_count = move.start + (travelled if move.distance > 0 else -travelled)
        if travelled == abs(move.distance):
            self.x_move, self.x_state = None, 'done'
        elif now >= move.expires:
            self.x_move, self.x_state = None, 'link'

    def reading(self, axis):
        with self.lock:
            now = time.monotonic()
            if axis == 'X':
                self._advance_x(now)
                return Reading('X', 1, 0, self.x_count, self.x_state, self.x_tag, now)
            self._advance_y(now)
            return Reading('Y', 1, 0, round(self.y_count), 'idle', 1, now)

    def start_x(self, generation, token, counts):
        with self.lock:
            now = time.monotonic()
            self._advance_x(now)
            if self.x_move is not None or generation != 1:
                return False
            self.x_move = PreviewXMove(self.x_count, counts, now, now + MOTION_LEASE)
            self.x_tag, self.x_state = token, 'moving'
            return True

    def renew_x(self):
        with self.lock:
            now = time.monotonic()
            self._advance_x(now)
            if self.x_move is None:
                return False
            self.x_move.expires = now + MOTION_LEASE
            return True

    def stop_x(self):
        with self.lock:
            self._advance_x(time.monotonic())
            self.x_move, self.x_state = None, 'stopped'
            return True


class PreviewTap:
    """Play back deterministic sample recordings through the normal review and scoring UI."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recording = None

    def status(self):
        with self.lock:
            return {'available': True, 'mode': self.recording[1] if self.recording else 'idle', 'strengthControl': True, 'durationControl': True}

    @staticmethod
    def audio(burst, alternate, peak=210):
        rate = 48000
        samples = array('h', [0]) * int(rate * (2.5 if burst else 1.5))
        frequency = 3600 if alternate else 1150
        for impact in range(3 if burst else 1):
            start = int(rate * (.25 + impact * .5))
            for n in range(rate // 10):
                t = n / rate
                attack = .8 * math.sin(math.tau * frequency * t) + .2 * math.sin(math.tau * frequency * 2.1 * t)
                samples[start + n] = round(21000 * peak / 255 * attack * math.exp(-55 * t))
        if sys.byteorder != 'little':
            samples.byteswap()
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as output:
            output.setparams((1, 2, rate, len(samples), 'NONE', 'not compressed'))
            output.writeframes(samples.tobytes())
        return base64.b64encode(buffer.getvalue()).decode('ascii')

    def command(self, payload, alternate):
        action, request_id = payload.get('action'), payload.get('id')
        if not isinstance(request_id, str) or not request_id or action not in ('burst', 'single', 'stop'):
            return 400, {'error': 'Invalid preview tap request.'}
        with self.lock:
            if action == 'stop':
                if self.recording and self.recording[0] == request_id:
                    self.recording[2].set()
                return 200, {'mode': 'idle'}
            if self.recording:
                return 409, {'error': 'Wait for the preview tap to finish.'}
            try:
                peak = peak_pwm(payload)
                duration = peak_ms(payload)
            except ValueError as error:
                return 400, {'error': str(error)}
            stopped = threading.Event()
            self.recording = (request_id, action, stopped)
        try:
            burst = action == 'burst'
            if stopped.wait(2.5 if burst else 1.5):
                return 409, {'error': 'Preview recording stopped.'}
            return 200, {'audioWav': self.audio(burst, alternate, peak), 'peakPwm': peak, 'peakMs': duration}
        finally:
            with self.lock:
                self.recording = None

    def close(self):
        with self.lock:
            if self.recording:
                self.recording[2].set()


class PreviewControls:
    def __init__(self):
        self.state_dir = TemporaryDirectory(prefix='brick-mapper-preview-')
        pulley_config = Path(self.state_dir.name, 'pulley.json')
        pulley_config.write_text(json.dumps({'inchesPerTurn': math.tau, 'holdToRun': True}))
        self.roof = BenchState('roof', '192.168.2.4', '192.168.2.3', '', 'live-four')
        boards = PreviewEncoders()
        self.roof.axis = NanoAxis(boards)
        self.roof.winch = WinchRelays(FOUR_CHANNEL_PINS, output_factory=boards.output)
        self.roof.pulley = PulleyAxis(boards, self.roof.winch, self.roof.lock,
                                     self.roof.arm_winch, self.roof.disarm_winch, pulley_config)
        self.roof.pulley.command({'action': 'zero', 'id': 'preview-start'})
        self.roof.exchange['connected'] = True
        self.roof.poweroff_available = False
        self.preview_tap = PreviewTap()
        self.camera_state = {'modes': ELP_SIZES, 'active': DEFAULT, 'id': None}

    def status(self):
        roof = self.roof.snapshot()
        roof['position']['source'] = 'simulated-rooftop'
        if roof['xAxis']['moveId'] is None:
            roof['xAxis']['message'] = 'Ready for a simulated X move.'
        return {
            'roof': {'reachable': True, 'data': roof},
            'robot': {'reachable': True, 'data': {'tap': self.preview_tap.status(), 'camera': self.camera_state}},
            'addresses': {'roof': 'simulated', 'robot': 'simulated'},
        }

    def axis(self, path, payload):
        return self.roof.axis_command(AXIS_ROUTES[path], payload)

    def command(self, payload):
        action, command_id = payload.get('action'), payload.get('id')
        if action not in ('up', 'down', 'start', 'stop') or not isinstance(command_id, str):
            return 400, {'error': 'Invalid winch command'}
        return self.roof.command(action, command_id, payload.get('mode'), payload.get('motionEpoch'))

    def tap(self, payload):
        x = self.roof.snapshot()['position']['xInches']
        return self.preview_tap.command(payload, alternate=int(abs(x or 0) / 1.5) % 2 == 1)

    def camera(self, payload):
        mode = payload.get('mode')
        if mode not in ELP_SIZES:
            return 400, {'error': 'Choose a sample capture mode.'}
        request_id = uuid.uuid4().hex
        self.camera_state = {**self.camera_state, 'active': mode, 'id': request_id}
        return 202, {'id': request_id}

    def poweroff(self):
        return 503, {'error': 'Shutdown is unavailable in the preview.'}

    def close(self):
        self.preview_tap.close()
        with self.roof.lock:
            self.roof.pulley.stop()
            self.roof.axis.close()
            self.roof.winch.close()
            self.state_dir.cleanup()


class PreviewHandler(Handler):
    preview = True
    controls = PreviewControls()

    def allowed_host(self):
        return self.headers.get('Host') == HOST

    def media(self, _):
        return self.respond(503, {'error': 'The preview uses a sample camera.'})


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_service)
    try:
        with ThreadingHTTPServer(ADDRESS, PreviewHandler) as server:
            print(f'Brick Mapper preview http://{HOST}/', flush=True)
            server.serve_forever()
    finally:
        PreviewHandler.controls.close()
