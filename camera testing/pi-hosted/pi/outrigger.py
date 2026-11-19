"""USB encoder and motion links for the rooftop outrigger."""
from collections import deque
from dataclasses import dataclass, field, replace
import json
import math
import os
from pathlib import Path
import secrets
import termios
import threading
import time
import tty

from x_axis import MAX_JOG_INCHES, MM_PER_COUNT

# Moving the installed X carriage to the right decreases the Nano's raw count.
X_RIGHT_ENCODER_SIGN = -1
PULLEY_COUNTS = 4096
# Ignore small reverse readings; stop at a quarter turn opposite the move start.
Y_REVERSE_LIMIT_COUNTS = PULLEY_COUNTS // 4
Y_TARGET_TOLERANCE_INCHES = 0.5
Y_MAX_JOG_INCHES = 12
MOTION_LEASE = 2.0
FRESH_SECONDS = 0.35
SERIAL_READ_SECONDS = 0.005
WINCH_TIMEOUT_SECONDS = 90
Y_CONFIG = Path('/var/lib/brick-mapper-outrigger/pulley.json')
MOTOR_STATES = {'idle', 'moving', 'done', 'stopped', 'stall', 'reverse', 'timeout', 'link'}


@dataclass(frozen=True)
class PulseProfile:
    probe_ms: int
    minimum_ms: int
    maximum_ms: int
    fallback_ms_per_inch: int
    settle_seconds: float
    fraction: float
    growth: float


WINCH_PROFILES = {
    'up': PulseProfile(400, 80, 600, 120, 0.25, 0.90, 1.75),
    'down': PulseProfile(250, 40, 250, 35, 0.70, 0.45, 1.15),
}


def winch_tuning(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or type(value.get('adaptive', True)) is not bool:
        raise ValueError('Use valid winch settings.')
    profiles = {}
    for direction, default in WINCH_PROFILES.items():
        settings = value.get(direction, {})
        if not isinstance(settings, dict):
            raise ValueError('Use valid Up and Down settings.')
        pulse = settings.get('pulseMs', default.probe_ms)
        minimum = settings.get('minPulseMs', default.minimum_ms)
        maximum = settings.get('maxPulseMs', default.maximum_ms)
        settle = settings.get('settleMs', round(default.settle_seconds * 1000))
        if not all(type(number) is int for number in (pulse, minimum, maximum, settle)):
            raise ValueError('Enter whole milliseconds for winch timing.')
        if not 10 <= minimum <= pulse <= maximum <= 1500 or not 50 <= settle <= 2000:
            raise ValueError('Use pulses from 10 to 1500 ms and settling from 50 to 2000 ms.')
        profiles[direction] = replace(default, probe_ms=pulse, minimum_ms=minimum,
                                      maximum_ms=maximum, settle_seconds=settle / 1000)
    return value.get('adaptive', True), profiles


class PulseResponse:
    """Estimate settled travel per relay-on millisecond within one measured move."""
    def __init__(self, profile):
        self.profile = profile
        self.rate = None
        self.last_ms = None
        self.progress = False

    def duration(self, inches):
        p = self.profile
        if self.last_ms is None:
            duration = p.probe_ms
        elif self.rate is None:
            duration = inches * p.fallback_ms_per_inch
        else:
            duration = inches * p.fraction / self.rate
        if self.last_ms is not None:
            # No feedback must never turn into a larger blind retry.
            duration = min(duration, self.last_ms * (p.growth if self.progress else 1))
        duration = min(p.maximum_ms, max(p.minimum_ms, round(duration)))
        self.last_ms = duration
        self.progress = False
        return duration

    def observe(self, inches, on_ms):
        self.progress = inches > 0 and on_ms >= 5
        if not self.progress:
            return
        measured = inches / on_ms
        # Respond immediately to faster travel. A slow or slipping sample only
        # lowers the estimate gradually, so it cannot provoke a large pulse.
        self.rate = measured if self.rate is None else max(measured, self.rate * 0.85)


@dataclass(frozen=True)
class Reading:
    role: str
    generation: int
    sequence: int
    counts: int
    state: str
    tag: int
    received: float


@dataclass
class Port:
    fd: int
    generation: int
    buffer: bytearray = field(default_factory=bytearray)
    discard: bool = False
    reading: Reading | None = None


class SerialBoards:
    """Read identified devices, with a short motor lease independent of web polling."""
    def __init__(self, paths=None):
        self.paths = paths or self._paths
        self.lock = threading.RLock()
        self.ports = {}
        self.generation = 0
        self.x_move = None
        self.next_ping = 0
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()

    @staticmethod
    def _paths():
        return sorted({p.resolve() for p in Path('/dev').glob('ttyACM*')}.union(
            p.resolve() for p in Path('/dev/serial/by-id').glob('*')))

    def _open(self, path):
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd, termios.TCSANOW)
            settings = termios.tcgetattr(fd)
            settings[2] |= termios.CLOCAL | termios.CREAD
            settings[4] = settings[5] = termios.B115200
            termios.tcsetattr(fd, termios.TCSANOW, settings)
            termios.tcflush(fd, termios.TCIOFLUSH)
        except BaseException:
            os.close(fd)
            raise
        self.generation += 1
        return Port(fd, self.generation)

    def _receive(self, port, raw, now):
        try:
            version, role, sequence, count, state, tag = raw.decode('ascii').split()
            if version != 'BM1' or role not in ('X', 'Y'):
                return
            sequence, count, tag = int(sequence), int(count), int(tag)
            if not (0 <= sequence < 2**32 and -(2**47) <= count < 2**47 and 0 <= tag < 2**32):
                return
            if state not in (MOTOR_STATES if role == 'X' else {'idle', 'signal'}):
                return
        except (ValueError, UnicodeError):
            return
        old = port.reading
        if old and (old.role != role or sequence < old.sequence):
            self.generation += 1
            port.generation = self.generation
        if old and old.role == role and sequence == old.sequence:
            return
        port.reading = Reading(role, port.generation, sequence, count, state, tag, now)

    def _read(self, port, now):
        for _ in range(4):
            try:
                data = os.read(port.fd, 1024)
            except BlockingIOError:
                return
            if not data:
                return
            for byte in data:
                if byte == 10:
                    if not port.discard:
                        self._receive(port, port.buffer, now)
                    port.buffer.clear()
                    port.discard = False
                elif not port.discard:
                    if len(port.buffer) < 96:
                        port.buffer.append(byte)
                    else:
                        port.discard = True

    def _port(self, role):
        found = [port for port in self.ports.values() if port.reading and port.reading.role == role
                 and time.monotonic() - port.reading.received < FRESH_SECONDS]
        return found[0] if len(found) == 1 else None

    def reading(self, role):
        with self.lock:
            port = self._port(role)
            return port.reading if port else None

    @staticmethod
    def _write(port, packet):
        try:
            return os.write(port.fd, packet) == len(packet)
        except OSError:
            return False

    def start_x(self, generation, token, counts):
        with self.lock:
            port = self._port('X')
            if not port or port.generation != generation or port.reading.state == 'moving':
                return False
            if not self._write(port, f'PING\nMOVE {token} {counts}\n'.encode('ascii')):
                return False
            self.x_move = (port, generation, time.monotonic() + MOTION_LEASE)
            return True

    def renew_x(self):
        with self.lock:
            if not self.x_move:
                return False
            port, generation, until = self.x_move
            if port is not self._port('X') or generation != port.generation or time.monotonic() >= until:
                return False
            self.x_move = (port, generation, time.monotonic() + MOTION_LEASE)
            return True

    def stop_x(self):
        with self.lock:
            move, self.x_move = self.x_move, None
            port = move[0] if move else self._port('X')
            return bool(port and any(item is port for item in self.ports.values())
                        and self._write(port, b'STOP\n'))

    def _work(self):
        next_scan = 0
        while not self.finished.wait(SERIAL_READ_SECONDS):
            now = time.monotonic()
            with self.lock:
                if now >= next_scan:
                    paths = set(self.paths())
                    for path in self.ports.keys() - paths:
                        os.close(self.ports.pop(path).fd)
                    for path in paths - self.ports.keys():
                        try:
                            self.ports[path] = self._open(path)
                        except (OSError, termios.error):
                            pass
                    next_scan = now + 0.5
                for path, port in list(self.ports.items()):
                    try:
                        self._read(port, now)
                    except OSError:
                        os.close(self.ports.pop(path).fd)
                if self.x_move:
                    port, generation, until = self.x_move
                    if now >= until or port is not self._port('X') or generation != port.generation:
                        # A removed descriptor may already have been reused by a different device.
                        if any(item is port for item in self.ports.values()) and generation == port.generation:
                            self._write(port, b'STOP\n')
                        self.x_move = None
                    elif now >= self.next_ping:
                        self._write(port, b'PING\n')
                        self.next_ping = now + 0.1

    def close(self):
        self.stop_x()
        self.finished.set()
        self.worker.join()
        for port in self.ports.values():
            os.close(port.fd)


def valid_id(command_id):
    return isinstance(command_id, str) and 1 <= len(command_id) <= 100


def valid_distance(value, maximum):
    return type(value) in (float, int) and 0.1 <= value <= maximum


class NanoAxis:
    def __init__(self, boards):
        self.boards = boards
        self.handled = deque(maxlen=200)
        self.active = None
        self.last_id = None
        self.outcome = None
        self.message = 'Waiting for the Nano Every.'

    def snapshot(self):
        reading = self.boards.reading('X')
        if self.active:
            token, generation, sent = self.active
            if not reading or reading.generation != generation:
                self._finish('error', 'Nano disconnected during the move.')
            elif reading.tag == token and reading.state != 'moving':
                message = {'done': 'X target reached.', 'stall': 'X stopped without encoder progress.',
                           'reverse': 'X encoder counted in the opposite direction.', 'timeout': 'X move timed out.',
                           'link': 'X stopped after losing its motion link.'}
                self._finish('done' if reading.state == 'done' else 'error',
                             message.get(reading.state, 'X move stopped.'))
            elif reading.tag != token and time.monotonic() - sent > 0.5:
                self._finish('error', 'Nano did not acknowledge the X move.')
        counts = reading.counts * X_RIGHT_ENCODER_SIGN if reading else None
        return {'mode': 'live', 'ready': reading is not None, 'connected': reading is not None,
                'moving': self.active is not None or bool(reading and reading.state == 'moving'),
                'counts': counts, 'inches': counts * MM_PER_COUNT / 25.4 if counts is not None else None,
                'moveId': self.last_id, 'outcome': self.outcome, 'maxJogInches': MAX_JOG_INCHES,
                'message': (self.message if self.last_id else 'Nano Every connected.')
                if reading else 'Connect the Nano Every over USB.'}

    def _finish(self, outcome, message):
        self.boards.stop_x()
        self.active, self.outcome, self.message = None, outcome, message

    def stop(self, message='X stopped.'):
        sent = self.boards.stop_x()
        if self.active:
            self.active, self.outcome, self.message = None, 'error', message
        return sent or self.boards.reading('X') is None

    def command(self, payload):
        action, command_id = payload.get('action'), payload.get('id')
        if not valid_id(command_id):
            return 400, {'error': 'Invalid X command ID.'}
        if action == 'stop':
            self.handled.append(command_id)
            if valid_id(payload.get('cancelId')):
                self.handled.append(payload['cancelId'])
            self.stop()
            return 200, self.snapshot()
        state = self.snapshot()
        if action == 'renew':
            if self.active and command_id == self.last_id:
                self.boards.renew_x()
            return 200, state
        if action not in ('left', 'right') or not valid_distance(payload.get('inches'), MAX_JOG_INCHES):
            return 400, {'error': 'Choose an X move from 0.1 to 6 inches.'}
        if payload.get('mode') != 'live' or command_id in self.handled or state['moving']:
            return 409, {'error': 'X is busy, or this move is no longer current.'}
        reading = self.boards.reading('X')
        if not reading or reading.state == 'moving':
            return 503, {'error': 'Connect the Nano Every before moving X.'}
        counts = round(payload['inches'] * 25.4 / MM_PER_COUNT)
        direction = X_RIGHT_ENCODER_SIGN if action == 'right' else -X_RIGHT_ENCODER_SIGN
        counts *= direction
        token = secrets.randbelow(2**31 - 1) + 1
        self.handled.append(command_id)
        if not self.boards.start_x(reading.generation, token, counts):
            return 503, {'error': 'Could not start the Nano motor controller.'}
        self.active = (token, reading.generation, time.monotonic())
        self.last_id, self.outcome, self.message = command_id, 'moving', f'Moving X {action}.'
        return 202, self.snapshot()

    def close(self):
        self.stop()


@dataclass
class WinchMove:
    id: str
    target: int
    direction: int
    expires: float
    route_limit_inches: float | None = None


class PulleyAxis:
    def __init__(self, boards, winch, lock, arm, disarm, config=Y_CONFIG):
        self.boards, self.winch, self.lock = boards, winch, lock
        self.arm, self.disarm = arm, disarm
        self.config = config
        self.travel, self.sign = math.tau, 1
        self.hold_down = self.hold_up = False
        self.timed = False
        self.timed_speed = 1.0
        self.timed_counts = self.timed_seconds = 0.0
        self.timed_tag = secrets.randbits(32)
        self.adaptive, self.profiles = winch_tuning()
        try:
            data = json.loads(config.read_text())
            self.travel, self.sign, self.hold_down, self.hold_up = self._calibration(data)
            self.timed, self.timed_speed = self._timing(data)
            self.adaptive, self.profiles = winch_tuning(data.get('winchTuning'))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as error:
            print(f'Pulley setup unavailable: {error}', flush=True)
        self._sync_jog_pulses()
        self.origin = None
        self.origin_file = config.with_name('pulley-origin.json')
        self.saved_origin = self._read_origin()
        self.active = None
        self.handled = deque(maxlen=200)
        self.last_id = self.outcome = None
        self.message = ''
        self.warning = None
        self.pulse_control = None
        if self.timed:
            self.saved_origin = None
            self.origin = (1, self.timed_tag, 0)

    @staticmethod
    def _timing(data):
        enabled, speed = data.get('timedY', False), data.get('timedSpeed', 1.0)
        if type(enabled) is not bool or type(speed) not in (int, float) or not .1 <= speed <= 20:
            raise ValueError('Enter an estimated winch speed from 0.1 to 20 inches per second.')
        return enabled, speed

    def _sync_jog_pulses(self):
        if self.winch is not None:
            self.winch.jog_pulses = {direction: profile.probe_ms for direction, profile in self.profiles.items()}

    def tuning(self):
        return {'adaptive': self.adaptive, **{direction: {
            'pulseMs': profile.probe_ms, 'minPulseMs': profile.minimum_ms,
            'maxPulseMs': profile.maximum_ms, 'settleMs': round(profile.settle_seconds * 1000)
        } for direction, profile in self.profiles.items()}}

    def _calibration(self, data):
        if not isinstance(data, dict):
            raise ValueError('Use a valid pulley calibration.')
        travel, sign = data.get('inchesPerTurn'), data.get('direction', self.sign)
        legacy_hold = data.get('holdToRun', False)
        down = data.get('holdToRunDown', legacy_hold)
        up = data.get('holdToRunUp', legacy_hold)
        if type(travel) not in (int, float) or not 0.1 <= travel <= 500:
            raise ValueError('Enter cable travel in inches per pulley revolution.')
        if type(sign) is not int or sign not in (-1, 1) or type(down) is not bool or type(up) is not bool:
            raise ValueError('Check the pulley settings.')
        return travel, sign, down, up

    def _read_origin(self):
        try:
            tag, counts = json.loads(self.origin_file.read_text())
            if type(tag) is int and 0 <= tag < 2**32 and type(counts) is int and -(2**47) <= counts < 2**47:
                return tag, counts
        except FileNotFoundError:
            pass
        except (OSError, TypeError, ValueError) as error:
            print(f'Pulley zero could not be restored: {error}', flush=True)
        return None

    def _forget_origin(self):
        self.origin = self.saved_origin = None
        try:
            self.origin_file.unlink(missing_ok=True)
        except OSError as error:
            print(f'Pulley zero could not be removed: {error}', flush=True)

    def _sample(self):
        if self.timed and self.winch is not None:
            elapsed = self.winch.motion_seconds()
            self.timed_counts += (elapsed - self.timed_seconds) * self.timed_speed * PULLEY_COUNTS / self.travel
            self.timed_seconds = elapsed
            return Reading('Y', 1, 0, round(self.timed_counts) * self.sign, 'idle', self.timed_tag, time.monotonic())
        reading = self.boards.reading('Y')
        valid = reading is not None and reading.state == 'idle'
        if self.origin and (not valid or self.origin[:2] != (reading.generation, reading.tag)):
            self._forget_origin()
        if valid and self.saved_origin is not None:
            tag, counts = self.saved_origin
            self.saved_origin = None
            if reading.tag == tag:
                self.origin = (reading.generation, tag, counts)
            else:
                self._forget_origin()
        return reading if valid else None

    def _counts(self, reading):
        return (reading.counts - self.origin[2]) * self.sign if self.origin and self.sign and reading else None

    def snapshot(self):
        reading = self._sample()
        counts = self._counts(reading)
        if not reading:
            hint = 'Connect the Pico 2 and check for a valid pulley PWM signal.'
        elif self.origin is None:
            hint = 'Set Y zero at the roof before moving by distance.'
        else:
            hint = self.message or 'Y increases as cable pays out.'
        measured = counts is not None and self.winch is not None
        if self.timed:
            hint = f'Estimated Y from relay time at {self.timed_speed:g} in/s. Check the camera before tapping.'
        return {'mode': 'live', 'connected': reading is not None,
                'estimated': self.timed, 'timedSpeed': self.timed_speed,
                'ready': measured, 'downReady': measured, 'upReady': measured,
                'zeroed': self.origin is not None, 'moving': self.active is not None,
                'rawCounts': reading.counts if reading else None, 'counts': counts,
                'encoderSequence': reading.sequence if reading else None,
                'encoderEpoch': reading.tag if reading else None,
                'encoderAgeMs': round((time.monotonic() - reading.received) * 1000, 1) if reading else None,
                'inches': counts * self.travel / PULLEY_COUNTS if counts is not None else None,
                'moveId': self.last_id, 'outcome': self.outcome, 'message': hint, 'warning': self.warning,
                'pulseControl': dict(self.pulse_control) if self.pulse_control else None,
                'winchTuning': self.tuning(),
                'inchesPerTurn': self.travel, 'direction': self.sign,
                'holdToRun': self.hold_down and self.hold_up,
                'holdToRunDown': self.hold_down, 'holdToRunUp': self.hold_up,
                'maxJogInches': Y_MAX_JOG_INCHES, 'routeLimitSupported': True}

    def _save_calibration(self, payload):
        travel, sign, down, up = self._calibration(payload)
        timed, speed = self._timing(payload)
        if self.timed:
            self._sample()
        changed = timed != self.timed
        scratch = self.config.with_suffix('.tmp')
        scratch.write_text(json.dumps({'inchesPerTurn': travel, 'direction': sign,
                                       'holdToRun': down and up, 'holdToRunDown': down, 'holdToRunUp': up,
                                       'timedY': timed, 'timedSpeed': speed,
                                       'winchTuning': self.tuning()}) + '\n')
        scratch.replace(self.config)
        self.travel, self.sign, self.hold_down, self.hold_up = travel, sign, down, up
        self.timed, self.timed_speed = timed, speed
        if changed:
            self._forget_origin()
            self.timed_counts = 0.0
            self.timed_seconds = self.winch.motion_seconds() if self.winch else 0.0
            self.timed_tag = secrets.randbits(32)
            if timed:
                self.origin = (1, self.timed_tag, 0)
        self.message = ''

    def _save_tuning(self, value):
        adaptive, profiles = winch_tuning(value)
        settings = {'inchesPerTurn': self.travel, 'direction': self.sign,
                    'holdToRunDown': self.hold_down, 'holdToRunUp': self.hold_up,
                    'timedY': self.timed, 'timedSpeed': self.timed_speed, 'winchTuning': value}
        scratch = self.config.with_suffix('.tmp')
        scratch.write_text(json.dumps(settings) + '\n')
        scratch.replace(self.config)
        self.adaptive, self.profiles = adaptive, profiles
        self._sync_jog_pulses()

    def command(self, payload):
        action, command_id = payload.get('action'), payload.get('id')
        if not valid_id(command_id):
            return 400, {'error': 'Invalid Y command ID.'}
        if action == 'renew':
            if self.active and command_id == self.active.id and time.monotonic() < self.active.expires:
                self.active.expires = time.monotonic() + MOTION_LEASE
            return 200, self.snapshot()
        if action == 'stop':
            if valid_id(payload.get('cancelId')):
                self.handled.append(payload['cancelId'])
            if self.active:
                self.stop('Y move canceled.', disarm=True)
            return 200, self.snapshot()
        if self.active:
            return 409, {'error': 'Wait for the Y move to finish.'}
        if command_id in self.handled:
            return 409, {'error': 'This Y move was already handled or canceled.'}
        if action == 'tune':
            try:
                value = payload.get('winchTuning')
                if not isinstance(value, dict):
                    raise ValueError('Use valid winch settings.')
                self._save_tuning(value)
            except (ValueError, OSError) as error:
                return 400, {'error': str(error)}
            return 200, self.snapshot()
        if action == 'calibrate':
            try:
                self._save_calibration(payload)
            except (ValueError, OSError) as error:
                return 400, {'error': str(error)}
            return 200, self.snapshot()
        reading = self._sample()
        if not reading:
            return 503, {'error': 'Connect the Pico 2 and check its pulley signal.'}
        if action == 'zero':
            try:
                scratch = self.origin_file.with_suffix('.tmp')
                scratch.write_text(json.dumps([reading.tag, reading.counts]) + '\n')
                scratch.replace(self.origin_file)
            except OSError as error:
                print(f'Pulley zero could not be saved: {error}', flush=True)
                return 503, {'error': 'Could not save roof zero.'}
            self.origin = (reading.generation, reading.tag, reading.counts)
            self.warning, self.message = None, 'Y zero set at this position.'
            self.handled.append(command_id)
            return 200, self.snapshot()
        if action not in ('up', 'down') or not valid_distance(payload.get('inches'), Y_MAX_JOG_INCHES):
            return 400, {'error': 'Choose a Y move from 0.1 to 12 inches.'}
        counts = self._counts(reading)
        if payload.get('mode') != 'live' or counts is None or self.winch is None:
            return 409, {'error': 'Complete pulley setup and set roof zero.'}
        # Down always gets an explicit Stop, even if the handset was configured
        # as hold-to-run. Releasing a relay alone must not leave descent latched.
        stop_after_jog = action == 'down' or self.timed or not self.hold_up
        direction = 1 if action == 'down' else -1
        distance = max(1, round(payload['inches'] * PULLEY_COUNTS / self.travel))
        target = counts + direction * distance
        route_limit = payload.get('routeLimitInches')
        if route_limit is not None:
            if type(route_limit) not in (int, float) or not 0 <= route_limit < math.inf:
                return 400, {'error': 'Set a valid lower Y limit.'}
            current = counts * self.travel / PULLEY_COUNTS
            if current > route_limit or (direction > 0 and current + payload['inches'] > route_limit + 1e-8):
                return 409, {'error': 'That route move would exceed the lower Y limit.'}
            if direction > 0 and target * self.travel / PULLEY_COUNTS > route_limit:
                # Round toward the roof if the requested stop lands between counts at the limit.
                target -= 1
                distance = target - counts
        if payload['inches'] <= Y_TARGET_TOLERANCE_INCHES:
            if self.winch.busy():
                return 409, {'error': 'The winch is busy. Wait for the current command to finish.'}
            self.handled.append(command_id)
            self.last_id = command_id
            self.warning = self.pulse_control = None
            self._complete(distance, counts)
            return 200, self.snapshot()
        try:
            if not self.arm():
                return 409, {'error': 'The winch is busy. Wait for the current command to finish.'}
        except (OSError, RuntimeError) as error:
            print(f'Winch Start failed: {error}', flush=True)
            return 503, {'error': 'Could not send Start to the winch.'}
        job = WinchMove(command_id, target, direction, time.monotonic() + MOTION_LEASE, route_limit)
        self.handled.append(command_id)
        self.active = job
        self.last_id, self.outcome, self.message = command_id, 'moving', f'Moving Y {action}.'
        self.warning = None
        self.pulse_control = {'strategy': 'timed' if self.timed else 'adaptive' if self.adaptive else 'fixed', 'direction': action,
                              'pulses': 0, 'lastPulseMs': None, 'responseInchesPerSecond': None}
        threading.Thread(target=self._move, args=(job, counts, distance, stop_after_jog), daemon=True).start()
        return 202, self.snapshot()

    def stop(self, message='Y stopped.', disarm=False):
        if not self.active:
            return
        self.active = None
        self.outcome, self.message = 'error', message
        try:
            self.winch.release()
        finally:
            if disarm:
                self.disarm()

    def _move(self, job, start, distance, stop_after_jog):
        began = time.monotonic()
        pulse_from = feedback_after = None
        pulse_on_at = None
        misses = 0
        rearm = False
        action = 'down' if job.direction > 0 else 'up'
        profile = self.profiles[action]
        adaptive = self.adaptive
        response = PulseResponse(profile)
        if self.timed:
            response.rate = self.timed_speed / 1000
        tolerance = Y_TARGET_TOLERANCE_INCHES * PULLEY_COUNTS / self.travel
        try:
            while True:
                with self.lock:
                    if self.active is not job:
                        return
                    now = time.monotonic()
                    reading = self._sample()
                    count = self._counts(reading)
                    if count is None:
                        self.stop('Y lost pulley feedback. Set Y zero again.', disarm=True)
                        return
                    if job.route_limit_inches is not None and count * self.travel / PULLEY_COUNTS > job.route_limit_inches:
                        self.stop('Lower Y limit exceeded. The winch was stopped.', disarm=True)
                        return
                    if now >= job.expires or now - began > WINCH_TIMEOUT_SECONDS:
                        self.stop('Y motion timed out.', disarm=True)
                        return
                    remaining = (job.target - count) * job.direction
                    if (count - start) * job.direction <= -Y_REVERSE_LIMIT_COUNTS:
                        self.stop('Y encoder moved opposite the requested motion.', disarm=True)
                        return

                    if pulse_from is not None and feedback_after is None:
                        if remaining <= tolerance and self.winch.action() == action:
                            self.winch.release()
                        if not self.winch.busy():
                            if stop_after_jog and not rearm:
                                if not self.winch.pulse('stop'):
                                    raise RuntimeError('Could not send Stop after the winch jog.')
                                rearm = True
                            else:
                                feedback_after = now + profile.settle_seconds
                    elif feedback_after is not None and reading.received >= feedback_after:
                        advanced = (count - pulse_from) * job.direction
                        on_ms = abs(self.winch.motion_seconds() - pulse_on_at) * 1000
                        if not self.timed and adaptive:
                            response.observe(advanced * self.travel / PULLEY_COUNTS if advanced >= 3 else 0, on_ms)
                        else:
                            response.progress = advanced >= 3
                        self.pulse_control['responseInchesPerSecond'] = round(response.rate * 1000, 3) if response.rate else None
                        if remaining <= tolerance:
                            self._complete(remaining, count)
                            return
                        misses = misses + 1 if advanced < 3 else 0
                        if misses >= 2:
                            self.stop('Y stopped without pulley progress.', disarm=True)
                            return
                        if rearm and not self.arm():
                            raise RuntimeError('Could not send Start for the next winch jog.')
                        pulse_from = feedback_after = None
                        rearm = False
                    elif pulse_from is None and not self.winch.busy():
                        if remaining <= tolerance:
                            self._complete(remaining, count)
                            return
                        inches = remaining * self.travel / PULLEY_COUNTS
                        pulse_limit = response.duration(inches) if adaptive else profile.probe_ms
                        pulse_on_at = self.winch.motion_seconds()
                        if not self.winch.pulse(action, pulse_limit):
                            raise RuntimeError('Winch relay is busy.')
                        pulse_from = count
                        self.pulse_control.update(pulses=self.pulse_control['pulses'] + 1, lastPulseMs=pulse_limit)
                        self.message = f'Moving Y {action} · adjusting pulse {self.pulse_control["pulses"]} to measured travel.'
                time.sleep(0.02)
        except (OSError, RuntimeError) as error:
            with self.lock:
                if self.active is job:
                    self.stop(str(error), disarm=True)

    def _complete(self, remaining, count):
        if remaining < -Y_TARGET_TOLERANCE_INCHES * PULLEY_COUNTS / self.travel:
            overshoot = -remaining * self.travel / PULLEY_COUNTS
            position = count * self.travel / PULLEY_COUNTS
            self.warning = f'Y went {overshoot:.2f} in past the target. Measured Y {position:.2f} in.'
        self.active, self.outcome, self.message = None, 'done', self.warning or ('Estimated Y target reached.' if self.timed else 'Y target reached.')
