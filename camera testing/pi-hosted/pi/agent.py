#!/usr/bin/env python3
"""Ethernet inspection controller."""
import argparse
import collections
import datetime
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
import uuid

POWEROFF_TIMER = 'brick-mapper-poweroff.timer'
WINCH_CHANNELS = ('stop', 'start', 'up', 'down')
LEGACY_CHANNELS = ('up', 'down', 'start', 'stop')
Y_MODEL_COUNTS_PER_INCH = 100
Y_MAX_JOG_INCHES = 12
AXIS_ROUTES = {'/api/x-axis': 'x', '/api/y-axis': 'y'}


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def stop_service(_signum, _frame):
    raise SystemExit(0)


class BenchState:
    def __init__(self, role, address, peer, token, winch_mode='simulation'):
        self.role, self.address, self.peer, self.token = role, address, peer, token
        self.boot_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.enabled = False
        self.motion_epoch = uuid.uuid4().hex
        self.action = None
        self.until = 0
        self.updated = self.started
        self.x, self.y = 1000.0, 0.0
        self.y_zero = None
        self.y_commands = collections.deque(maxlen=200)
        self.sequence = 0
        self.received = 0
        self.last_received = None
        self.exchange = {'connected': False, 'sent': 0, 'acknowledged': 0}
        self.commands = collections.OrderedDict()
        self.events = collections.deque(maxlen=30)
        self.axis = None
        self.pulley = None
        self.tap = None
        self.tap_recording = threading.Lock()
        self.recorded_tap_ids = collections.deque(maxlen=200)
        self.tap_stop_count = 0
        self.winch_mode = winch_mode
        self.channels = LEGACY_CHANNELS if winch_mode == 'live-up' else WINCH_CHANNELS
        self.winch = None
        self.poweroff_available = Path('/etc/systemd/system', POWEROFF_TIMER).is_file()
        self.poweroff_scheduled = False
        self.robot_poweroff_scheduled = False

    def update(self):
        now = time.monotonic()
        if self.winch_mode != 'simulation':
            self.action = self.winch.action() if self.winch is not None else None
            return
        if self.enabled and self.action in ('up', 'down'):
            moving_until = min(now, self.until)
            duration = max(0, moving_until - self.updated)
            self.y += duration * (100 if self.action == 'down' else -100)
        if now >= self.until:
            self.action = None
        self.updated = now

    def snapshot(self):
        with self.lock:
            self.update()
            axis = self.axis.snapshot() if self.axis else None
            pulley = self.pulley.snapshot() if self.pulley else None
            return {
                'role': self.role, 'hostname': socket.gethostname(),
                'address': self.address, 'bootId': self.boot_id,
                'time': utc_now(), 'uptimeSeconds': round(time.monotonic() - self.started, 1),
                'position': self.position(axis, pulley),
                'xAxis': axis,
                'yAxis': pulley or ({'mode': 'simulation', 'zeroed': self.y_zero is not None,
                          'maxJogInches': Y_MAX_JOG_INCHES} if self.role == 'roof' else None),
                'relays': {'mode': self.winch_mode,
                           'ready': self.winch_mode == 'simulation' or self.winch is not None,
                           'enabled': self.enabled,
                           'autoArm': self.winch_mode == 'live-four',
                           'motionEpoch': self.motion_epoch,
                           'busy': self.winch.busy() if self.winch is not None else bool(self.action),
                           'action': self.action, 'channels': [
                               {'channel': n, 'label': action.capitalize(), 'closed': self.action == action}
                               for n, action in enumerate(self.channels, 1)]},
                'exchange': dict(self.exchange), 'receivedPackets': self.received,
                'lastReceived': self.last_received, 'events': list(self.events),
                'tap': self.tap.status() if self.tap else {'available': False},
                'poweroff': {'available': self.poweroff_available,
                             'scheduled': self.poweroff_scheduled,
                             'robotScheduled': self.robot_poweroff_scheduled},
            }

    def position(self, axis, pulley=None):
        if pulley is not None:
            return {'x': axis['counts'], 'y': pulley['counts'], 'xInches': axis['inches'],
                    'yInches': pulley['inches'], 'yZeroed': pulley['zeroed'],
                    'source': 'outrigger-timed' if pulley.get('estimated') else 'outrigger-encoders', 'units': 'encoder counts',
                    'yMoveNote': pulley['warning']}
        y = round(self.y - (self.y_zero or 0))
        result = {'x': axis['counts'] if axis else round(self.x), 'y': y,
                  'source': 'x-encoder-rooftop' if axis and axis['mode'] == 'live'
                  else 'simulated-rooftop', 'units': 'encoder counts'}
        if axis:
            result.update(xInches=axis['inches'], yInches=y / Y_MODEL_COUNTS_PER_INCH,
                          yZeroed=self.y_zero is not None)
        return result

    def arm_winch(self):
        if not self.winch.pulse('start'):
            return False
        self.enabled = True
        return True

    def disarm_winch(self):
        self.enabled = False
        self.motion_epoch = uuid.uuid4().hex
        if self.winch is not None:
            self.winch.pulse('stop')

    def command(self, action, command_id, mode=None, motion_epoch=None):
        with self.lock:
            self.update()
            if self.poweroff_scheduled or self.robot_poweroff_scheduled:
                return 409, {'error': 'System shutdown is in progress'}
            if self.winch_mode != 'simulation' and mode != self.winch_mode and action != 'stop':
                return 409, {'error': 'Reload Brick Mapper to use live winch controls'}
            if self.winch_mode != 'live-four' and action != 'stop' and not self.exchange['connected']:
                return 409, {'error': 'Robot link is unavailable'}
            if command_id in self.commands:
                previous = self.commands[command_id]
                if previous['action'] != action:
                    return 409, {'error': 'Command ID was already used for another action'}
                return 200, dict(previous, duplicate=True)
            if action != 'stop' and self.pulley and self.pulley.active:
                return 409, {'error': 'Wait for the measured Y move to finish.'}
            if action != 'stop' and self.axis is not None and self.axis.snapshot()['moving']:
                return 409, {'error': 'Wait for X movement to finish.'}
            if self.winch_mode != 'simulation':
                if self.winch is None:
                    return 503, {'error': 'Winch relay is unavailable'}
                legacy = self.winch_mode == 'live-up'
                if legacy and action == 'down':
                    return 409, {'error': 'Only Up is wired for live movement'}
                auto_jog = not legacy and action in ('up', 'down')
                if auto_jog and motion_epoch != self.motion_epoch:
                    return 409, {'error': 'Winch state changed. Wait for status to refresh and try again.'}
                if legacy and action == 'up' and not self.enabled:
                    return 409, {'error': 'Press Start to arm the winch'}
                pulse_ms = 0
                try:
                    if action == 'stop':
                        if self.pulley:
                            self.pulley.stop('Y move stopped.')
                        if legacy:
                            self.enabled = False
                            self.winch.release()
                        else:
                            self.disarm_winch()
                            pulse_ms = self.winch.pulse_ms
                    elif auto_jog:
                        if not self.winch.jog(action):
                            return 409, {'error': 'A winch command is already in progress'}
                        self.enabled = True
                        pulse_ms = self.winch.jog_duration(action)
                    elif not legacy or action == 'up':
                        if not self.winch.pulse(action):
                            return 409, {'error': 'A winch command is already in progress'}
                        pulse_ms = self.winch.pulse_ms
                except (OSError, RuntimeError) as error:
                    self.enabled = False
                    print(f'Winch relay command failed: {error}', flush=True)
                    return 503, {'error': 'Winch relay command failed'}
                if action == 'start':
                    self.enabled = True
                message = {'start': 'Winch armed', 'stop': 'Winch disarmed',
                           'up': 'Up jog accepted', 'down': 'Down jog accepted'}[action]
                if auto_jog:
                    message = 'Start sent · ' + message
                if pulse_ms:
                    message += f' · channel {self.channels.index(action) + 1} for {pulse_ms} ms'
            else:
                if action in ('up', 'down') and not self.enabled:
                    return 409, {'error': 'Press Start before trying a simulated movement'}
                if action in ('up', 'down') and self.action in ('up', 'down'):
                    return 409, {'error': 'A simulated movement is already in progress'}
                if action in ('start', 'stop'):
                    self.enabled = action == 'start'
                self.action = action
                pulse_ms = 300
                message = 'Simulated ' + action + ' accepted by rooftop controller'
            self.until = time.monotonic() + pulse_ms / 1000
            result = {'id': command_id, 'action': action,
                      'mode': self.winch_mode, 'channel': self.channels.index(action) + 1,
                      'acceptedAt': utc_now(), 'pulseMs': pulse_ms, 'message': message}
            self.commands[command_id] = result
            while len(self.commands) > 200:
                self.commands.popitem(last=False)
            self.events.append(result)
            return 200, result

    def axis_command(self, axis, payload):
        with self.lock:
            if self.axis is None:
                return 503, {'error': 'Axis controller is unavailable'}
            action = payload.get('action')
            controller = self.axis if axis == 'x' else self.pulley
            if controller is not None and action in ('stop', 'renew'):
                if axis == 'x':
                    if action == 'stop' or self.pulley:
                        return self.axis.command(payload)
                else:
                    return self.pulley.command(payload)
            if self.poweroff_scheduled or self.robot_poweroff_scheduled:
                return 409, {'error': 'System shutdown is in progress'}
            self.update()
            if (self.winch.busy() if self.winch is not None else self.action) or (self.pulley and self.pulley.active):
                return 409, {'error': 'Wait for the winch to finish.'}
            if axis == 'x':
                return self.axis.command(payload)
            if self.axis.snapshot()['moving']:
                return 409, {'error': 'Wait for X movement to finish.'}
            if self.pulley:
                if action in ('up', 'down') and payload.get('motionEpoch') != self.motion_epoch:
                    return 409, {'error': 'Winch state changed. Wait for status to refresh and try again.'}
                return self.pulley.command(payload)
            action, command_id = payload.get('action'), payload.get('id')
            if action not in ('up', 'down', 'zero') or not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
                return 400, {'error': 'Invalid Y-axis command'}
            if command_id in self.y_commands:
                return 409, {'error': 'This Y-axis request was already handled.'}
            if action == 'zero':
                self.y_zero = self.y
            else:
                inches = payload.get('inches')
                if type(inches) not in (int, float) or not 0.1 <= inches <= Y_MAX_JOG_INCHES:
                    return 400, {'error': f'Choose a Y distance from 0.1 to {Y_MAX_JOG_INCHES} inches.'}
                change = round(inches * Y_MODEL_COUNTS_PER_INCH)
                target = self.y + (change if action == 'down' else -change)
                self.y = target
            self.y_commands.append(command_id)
            return 200, {'position': self.position(self.axis.snapshot())}

    def poweroff(self):
        with self.lock:
            if not self.poweroff_available:
                return 503, {'error': 'Shutdown is not installed on this Pi'}
            if self.poweroff_scheduled:
                return 200, {'scheduled': True}

            self.update()
            self.enabled, self.action = False, None
            try:
                if self.pulley:
                    self.pulley.stop('Y stopped for shutdown.')
                if self.winch is not None:
                    self.winch.release()
                if self.axis is not None and not self.axis.stop():
                    return 503, {'error': 'The X motor could not be stopped. Cut motor power.'}
                if self.tap is not None:
                    self.tap_stop_count += 1
                    status, _ = self.tap.command({'action': 'stop'})
                    if status != 200:
                        return 503, {'error': 'The robot could not stop tapping'}
            except (OSError, RuntimeError) as error:
                print(f'Could not release outputs for shutdown: {error}', flush=True)
                return 503, {'error': 'Could not release the outputs for shutdown'}

            if self.role == 'roof' and not self.robot_poweroff_scheduled:
                try:
                    conn = http.client.HTTPConnection(
                        self.peer, 8091, timeout=3, source_address=(self.address, 0))
                    try:
                        conn.request('POST', '/api/poweroff', '{}',
                                     {'X-Network-Key': self.token, 'Content-Type': 'application/json'})
                        response = conn.getresponse()
                        data = json.loads(response.read(4096))
                        if response.status != 200 or data.get('scheduled') is not True:
                            return 503, {'error': 'Robot did not schedule shutdown. Rooftop Pi is still on.'}
                    finally:
                        conn.close()
                except (OSError, ValueError, AttributeError, http.client.HTTPException):
                    return 503, {'error': 'Robot shutdown could not be confirmed. Rooftop Pi is still on.'}
                self.robot_poweroff_scheduled = True

            try:
                subprocess.run(['/usr/bin/systemctl', '--no-ask-password', 'start', POWEROFF_TIMER],
                               check=True, timeout=2, capture_output=True, text=True)
            except (OSError, subprocess.SubprocessError) as error:
                print(f'Could not schedule local shutdown: {error}', flush=True)
                message = ('Robot shutdown scheduled. Rooftop shutdown failed. Try again.'
                           if self.robot_poweroff_scheduled else 'Could not schedule shutdown on this Pi')
                return 503, {'error': message}
            self.poweroff_scheduled = True
            return 200, {'scheduled': True}

    def tap_command(self, payload):
        action = payload.get('action')
        command_id = payload.get('id')
        burst = action == 'burst'
        with self.lock:
            if self.poweroff_scheduled:
                return 409, {'error': 'Robot shutdown is in progress'}
            if self.tap is None:
                return 503, {'error': 'Hardware PWM is unavailable'}
            if action == 'stop':
                self.tap_stop_count += 1
                if isinstance(command_id, str) and 1 <= len(command_id) <= 100:
                    self.recorded_tap_ids.append(command_id)
            if action in ('single', 'burst', 'auto') and self.tap_recording.locked():
                return 409, {'error': 'A tap recording is already in progress'}
            if not burst and (action != 'single' or payload.get('record') is not True):
                return self.tap.command(payload)
            if not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
                return 400, {'error': 'Invalid tap request'}
            if command_id in self.recorded_tap_ids:
                return 409, {'error': 'This tap request has already been handled or canceled'}
            self.tap_recording.acquire()
            stop_count = self.tap_stop_count

        def trigger():
            with self.lock:
                if self.poweroff_scheduled or stop_count != self.tap_stop_count:
                    return 409, {'error': 'Tap canceled'}
                status, result = self.tap.command(payload)
                if status == 200:
                    self.recorded_tap_ids.append(command_id)
                return status, result

        try:
            from tap_audio import record_tap
            if burst:
                from tap import BURST_COUNT, BURST_HZ
                status, result = record_tap(trigger, burst_span=(BURST_COUNT - 1) / BURST_HZ)
            else:
                status, result = record_tap(trigger)
        finally:
            try:
                completed = self.tap.finish_burst(command_id) if burst else True
            finally:
                self.tap_recording.release()
        if not completed and result.get('audioWav'):
            return 409, {'error': 'The three-tap burst did not finish. No recording was saved.'}
        return status, result

    def exchange_loop(self):
        """Actual roof -> robot -> roof packets, with static Ethernet sources."""
        while True:
            with self.lock:
                self.sequence += 1
                sequence = self.sequence
            position = self.snapshot()['position']
            payload = {'from': self.role, 'bootId': self.boot_id, 'sequence': sequence,
                       'position': position, 'sentAt': utc_now()}
            start = time.monotonic()
            connection = http.client.HTTPConnection(
                self.peer, 8091, timeout=0.8, source_address=(self.address, 0))
            try:
                connection.request('POST', '/api/exchange', json.dumps(payload),
                                   {'Content-Type': 'application/json', 'X-Network-Key': self.token})
                response = connection.getresponse()
                data = json.loads(response.read(16384))
                if response.status != 200 or data.get('role') != 'robot':
                    raise ValueError('Unexpected peer response')
                if data.get('sequence') != sequence or data.get('senderBootId') != self.boot_id:
                    raise ValueError('Acknowledgement did not match sent packet')
                with self.lock:
                    self.exchange = {'connected': True, 'sent': sequence,
                                     'acknowledged': sequence, 'lastAckAt': utc_now(),
                                     'roundTripMs': round((time.monotonic() - start) * 1000, 2),
                                     'robotHostname': data['hostname'], 'robotBootId': data['bootId']}
            except (OSError, ValueError, KeyError, http.client.HTTPException):
                with self.lock:
                    self.exchange.update(connected=False, sent=sequence)
                    if self.winch_mode != 'live-four':
                        self.update()
                        self.action, self.enabled = None, False
                        if self.winch is not None:
                            try:
                                self.winch.release()
                            except (OSError, RuntimeError) as error:
                                print(f'Winch relay release failed: {error}', flush=True)
            finally:
                connection.close()
            time.sleep(max(0.05, 0.5 - (time.monotonic() - start)))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        state = self.server.state
        supplied = self.headers.get('X-Network-Key', '')
        return (self.client_address[0] in ('192.168.2.254', state.peer, '127.0.0.1')
                and secrets.compare_digest(supplied, state.token))

    def do_GET(self):
        if not self.authorized():
            return self.respond(403, {'error': 'Authentication required'})
        if self.path != '/api/status':
            return self.respond(404, {'error': 'Not found'})
        state = self.server.state
        result = state.snapshot()
        if state.role == 'robot':
            from camera import status as camera_status
            result['camera'] = camera_status()
        self.respond(200, result)

    def do_POST(self):
        if not self.authorized():
            return self.respond(403, {'error': 'Authentication required'})
        state = self.server.state
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < length <= 4096:
                return self.respond(400, {'error': 'Invalid request length'})
            self.connection.settimeout(2)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError('Expected an object')
            if self.path == '/api/poweroff':
                expected = '127.0.0.1' if state.role == 'roof' else state.peer
                if self.client_address[0] != expected:
                    return self.respond(403, {'error': 'Shutdown requires the rooftop dashboard'})
                return self.respond(*state.poweroff())
            if self.path == '/api/exchange' and state.role == 'robot':
                if payload.get('from') != 'roof' or self.client_address[0] != state.peer:
                    return self.respond(403, {'error': 'Expected rooftop controller'})
                with state.lock:
                    state.received += 1
                    state.last_received = dict(payload, receivedAt=utc_now())
                return self.respond(200, {'role': 'robot', 'hostname': socket.gethostname(),
                                         'bootId': state.boot_id, 'senderBootId': payload.get('bootId'),
                                         'sequence': payload.get('sequence'), 'receivedAt': utc_now()})
            if self.path == '/api/tap' and state.role == 'robot':
                if self.client_address[0] != state.peer:
                    return self.respond(403, {'error': 'Expected rooftop controller'})
                return self.respond(*state.tap_command(payload))
            if self.path == '/api/camera' and state.role == 'robot':
                if self.client_address[0] != state.peer:
                    return self.respond(403, {'error': 'Expected rooftop controller'})
                from camera import request_mode
                return self.respond(*request_mode(payload))
            if self.path in AXIS_ROUTES and state.role == 'roof':
                if self.client_address[0] != '127.0.0.1':
                    return self.respond(403, {'error': 'Expected rooftop dashboard'})
                return self.respond(*state.axis_command(AXIS_ROUTES[self.path], payload))
            if self.path == '/api/winch' and state.role == 'roof':
                action, command_id = payload.get('action'), payload.get('id')
                if action not in ('up', 'down', 'start', 'stop'):
                    raise ValueError('Unknown winch command')
                if not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
                    raise ValueError('Missing command ID')
                return self.respond(*state.command(action, command_id, payload.get('mode'), payload.get('motionEpoch')))
            self.respond(404, {'error': 'Not found'})
        except (ValueError, TypeError, OSError):
            self.respond(400, {'error': 'Invalid request'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('roof', 'robot'), required=True)
    parser.add_argument('--tap', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--live-winch-up', dest='winch_mode', action='store_const', const='live-up')
    modes.add_argument('--live-winch', dest='winch_mode', action='store_const', const='live-four')
    parser.set_defaults(winch_mode='simulation')
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    address, peer = ('192.168.2.4', '192.168.2.3') if args.role == 'roof' else ('192.168.2.3', '192.168.2.4')
    token = args.token_file.read_text().strip()
    if len(token) < 32:
        raise SystemExit('Network key is missing or too short')
    if args.winch_mode != 'simulation' and args.role != 'roof':
        parser.error('The live winch belongs on the rooftop Pi')
    state = BenchState(args.role, address, peer, token, args.winch_mode)
    serial_out = args.role == 'roof' and args.winch_mode == 'live-four' and Path('/etc/brick-mapper/outrigger').is_file()
    if args.role == 'roof' and not serial_out:
        from winch import FOUR_CHANNEL_PINS
        from x_axis import CONFIG, XAxis
        state.axis = XAxis.from_file(Path(CONFIG), {2, 3, 14, 15, *FOUR_CHANNEL_PINS.values()})
    if args.tap and args.role == 'robot':
        from tap import TapController
        try:
            state.tap = TapController()
        except OSError as error:
            print(f'Tap unavailable: {error}', flush=True)
    if args.winch_mode != 'simulation':
        try:
            from winch import FOUR_CHANNEL_PINS, WinchRelays
            pins = {'up': 26} if args.winch_mode == 'live-up' else FOUR_CHANNEL_PINS
            state.winch = WinchRelays(pins)
        except Exception as error:
            print(f'Winch relay unavailable: {error}', flush=True)
    boards = None
    if serial_out:
        from outrigger import NanoAxis, PulleyAxis, SerialBoards
        boards = SerialBoards()
        state.axis = NanoAxis(boards)
        state.pulley = PulleyAxis(boards, state.winch, state.lock, state.arm_winch, state.disarm_winch)
    if args.role == 'roof':
        signal.signal(signal.SIGTERM, stop_service)
    try:
        with ThreadingHTTPServer((address, 8091), Handler) as server:
            server.state = state
            server.daemon_threads = True
            if args.role == 'roof':
                threading.Thread(target=state.exchange_loop, daemon=True).start()
            print(f'{args.role} inspection controller on {address}:8091', flush=True)
            server.serve_forever()
    finally:
        try:
            with state.lock:
                if state.pulley:
                    state.pulley.stop('Y stopped for service shutdown.')
                if state.axis is not None:
                    state.axis.close()
        finally:
            try:
                if boards:
                    boards.close()
            finally:
                if state.winch is not None:
                    state.winch.close()


if __name__ == '__main__':
    main()
