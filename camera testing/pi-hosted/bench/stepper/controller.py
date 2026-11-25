#!/usr/bin/env python3
"""Local-only HBS86H bench controller. Opening the page never arms or moves hardware."""
from __future__ import annotations

import argparse
from collections import deque
import csv
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import math
from pathlib import Path
import secrets
import signal
import threading
import time
import webbrowser

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
PROTECTED = {
    'D92DB2CC515452344D202020FF094441': 'Existing X controller',
    '73F97AA75154524C4A202020FF0C3720': 'Existing Y encoder',
}
IDENTITY = 'SBH1 WINCH_NANO_EVERY_BENCH 1 '
MAX_MOVE_STEPS = (1 << 31) - 1  # Signed command/position representation on the Nano.


def ports():
    try:
        motor_serial = json.loads((ROOT / 'runtime/installed.json').read_text())['usbSerial']
    except (OSError, ValueError, KeyError):
        motor_serial = None
    return [dict(port=p.device, description=p.description, serial=p.serial_number or '',
                 role=PROTECTED.get(p.serial_number, ''), benchMotor=p.serial_number == motor_serial,
                 nano=(p.vid, p.pid) == (0x2341, 0x0058))
            for p in list_ports.comports() if p.vid is not None]


def finite(value, name, low, high=None):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a number')
    result = float(value)
    if not math.isfinite(result) or result < low or (high is not None and result > high):
        bounds = f'between {low:g} and {high:g}' if high is not None else f'at least {low:g}'
        raise ValueError(f'{name} must be {bounds}')
    return result


def plan_move(data):
    ppr = int(finite(data.get('ppr'), 'Pulses per revolution', 200, 51200))
    diameter = finite(data.get('diameter'), 'Effective drum diameter', .1, 24)
    distance = finite(data.get('distance'), 'Move amount', .0001)
    unit = data.get('unit')
    if unit not in ('turns', 'inches'):
        raise ValueError('Choose turns or inches')
    direction = data.get('direction')
    if type(direction) is not int or direction not in (-1, 1):
        raise ValueError('Choose positive or negative direction')
    rpm = finite(data.get('rpm'), 'Speed in RPM', .1, 60)
    accel = finite(data.get('accel'), 'Acceleration in RPM/second', .1, 120)
    turns = distance if unit == 'turns' else distance / (math.pi * diameter)
    pulses = turns * ppr
    if not math.isfinite(pulses) or pulses > MAX_MOVE_STEPS:
        raise ValueError('Move exceeds the Nano command range of 2,147,483,647 pulses')
    steps = int(math.floor(pulses + .5))
    if steps < 1:
        raise ValueError('This move must contain at least one pulse')
    rate = int(round(rpm * ppr / 60 * 1000))
    acceleration = int(round(accel * ppr / 60 * 1000))
    if not 1000 <= rate <= 5_000_000:
        raise ValueError('This speed/subdivision combination exceeds the 5,000 pulse/second bench limit')
    if not 1000 <= acceleration <= 10_000_000:
        raise ValueError('This acceleration/subdivision combination is outside the bench limit')
    return dict(steps=direction * steps, rate=rate, acceleration=acceleration,
                ppr=ppr, diameter=diameter, turns=direction * steps / ppr,
                inches=direction * steps / ppr * math.pi * diameter)


def parse_status(line):
    fields = line.split()
    if len(fields) != 12 or fields[0] != 'SB1':
        return None
    try:
        numeric = [int(fields[n]) for n in (1, 2, 3, 4, 5, 6, 8, 9, 10, 11)]
        seq, armed, moving, position, remaining, rate, alarm, pend, mode, move_id = numeric
        if any(n not in (0, 1) for n in (armed, moving, alarm, pend)) or mode not in (0, 1, 2):
            return None
        return dict(sequence=seq, armed=bool(armed), moving=bool(moving), steps=position,
                    remaining=remaining, rate=rate / 1000, reason=fields[7],
                    alarmRaw=alarm, pendRaw=pend, alarmMode=mode, moveId=move_id)
    except ValueError:
        return None


class BenchController:
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.tx_lock = threading.RLock()
        self.operation_lock = threading.Lock()
        self.link = None
        self.port = None
        self.board_serial = ''
        self.last_rx = 0.
        self.lease = 0.
        self.generation = 0
        self.closed = False
        self.state = dict(armed=False, moving=False, steps=0, reason='disconnected')
        self.error = ''
        self.acks = deque(maxlen=40)
        self.logs = deque(maxlen=36000)
        self.events = deque(maxlen=80)
        self.next_id = 1
        self.calibration = None
        self.encoder_link = None
        self.encoder_state = None
        self.encoder_rx = 0.
        self.encoder_zero = None
        self.encoder_scale = None
        self.encoder_sign = 1
        self.encoder_required = False
        self.encoder_watch = None
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def event(self, message):
        with self.lock:
            self.events.append(dict(time=datetime.now().strftime('%H:%M:%S'), message=message))

    def write(self, message):
        with self.tx_lock:
            with self.lock:
                link = self.link
            if link is None:
                raise ValueError('Connect the motor Nano first')
            link.write((message + '\n').encode('ascii'))

    def request(self, message, kind, move_id=0, generation=None):
        began = time.monotonic()
        # Serialize the final generation check with the write. If Stop races
        # with an ARM/MOVE request, it either rejects it or is sent after it.
        with self.tx_lock:
            if generation is not None:
                with self.lock:
                    self._ready(generation)
            self.write(message)
        with self.condition:
            while time.monotonic() - began < .6:
                for at, line in self.acks:
                    if at < began:
                        continue
                    if line.startswith('SBE '):
                        raise ValueError('Nano rejected command: ' + line[4:])
                    if line == f'SBA {kind} {move_id}':
                        return
                self.condition.wait(.03)
        self.stop('Command acknowledgement was lost; movement stopped')
        raise ValueError('No command acknowledgement from Nano')

    def connect(self, port):
        known = next((p for p in ports() if p['port'] == port and p['nano']), None)
        if not known:
            raise ValueError('Select an attached Nano Every')
        if known['serial'] in PROTECTED:
            raise ValueError('This is an existing X/Y board; keep it in its current role')
        self.disconnect()
        link = serial.Serial(port, 115200, timeout=.04, write_timeout=.2, exclusive=True)
        try:
            deadline = time.monotonic() + 3
            identified = False
            next_hello = 0.
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_hello:
                    link.write(b'HELLO\n')
                    next_hello = now + .3
                line = link.readline(200).decode('ascii', errors='replace').strip()
                if line.startswith(IDENTITY):
                    identified = True
                    break
                if line.startswith(('BM1 X ', 'BM1 Y ')):
                    raise ValueError('That board contains existing X/Y firmware; it has not been changed')
            if not identified:
                raise ValueError('The selected Nano does not report the bench firmware identity')
            # Connection never resumes an old move or arms the board.
            link.write(b'STOP\n')
            with self.lock:
                self.link = link
                self.port = port
                self.board_serial = known['serial']
                self.last_rx = time.monotonic()
                self.error = ''
                self.acks.clear()
                self.state = dict(armed=False, moving=False, steps=0, reason='connecting')
                self.calibration = None
            self.event('Motor Nano connected; disarmed. No movement requested.')
        except Exception:
            link.close()
            raise

    def heartbeat(self):
        with self.lock:
            self.lease = time.monotonic()

    def stop(self, message='Stopped by operator'):
        with self.lock:
            self.generation += 1
            self.lease = 0
            self.encoder_watch = None
            self.state['armed'] = False
        try:
            if self.link:
                self.write('STOP')
        except (serial.SerialException, OSError, ValueError):
            pass  # Nano independently times out if the serial link has failed.
        self.event(message)

    def disconnect(self):
        self.stop('Motor controller disconnected')
        with self.tx_lock:
            with self.lock:
                link, self.link = self.link, None
                self.port = None
                self.state = dict(armed=False, moving=False, steps=0, reason='disconnected')
            if link:
                link.close()

    def _ready(self, generation):
        if generation != self.generation:
            raise ValueError('A Stop interrupted this request; arm again when ready')
        if not self.link or 'sequence' not in self.state or time.monotonic() - self.last_rx > .4:
            raise ValueError('Fresh motor telemetry is required')
        if time.monotonic() - self.lease > .5:
            raise ValueError('The control page is not active; refresh it before arming')

    def arm(self):
        with self.lock:
            generation = self.generation
        with self.operation_lock:
            with self.lock:
                self._ready(generation)
                if self.state.get('moving'):
                    raise ValueError('The motor is already moving')
            self.write('PING')
            self.request('ARM', 'ARM', generation=generation)
            self.event('Armed for commanded moves')

    def move(self, data):
        plan = plan_move(data)
        required = data.get('requireEncoder') is True
        with self.lock:
            generation = self.generation
        with self.operation_lock:
            with self.lock:
                self._ready(generation)
                if not self.state.get('armed') or self.state.get('moving'):
                    raise ValueError('Arm the motor and wait until the previous move finishes')
                target = self.state['steps'] + plan['steps']
                if not -(1 << 31) <= target <= MAX_MOVE_STEPS:
                    raise ValueError('Commanded position exceeds the Nano counter range; zero the commanded position first')
                if self.calibration and any(self.calibration[k] != plan[k] for k in ('ppr', 'diameter')) and self.state['steps']:
                    raise ValueError('Zero the commanded position before changing pulses/rev or drum diameter')
                encoder = self._encoder_snapshot()
                if required and not encoder.get('valid'):
                    raise ValueError('A fresh external encoder signal is required for this move')
                if required and not encoder.get('zeroed'):
                    raise ValueError('Zero the external encoder first')
                self.encoder_required = required
                self.calibration = {k: plan[k] for k in ('ppr', 'diameter')}
                self.next_id = max(self.next_id, self.state.get('moveId', 0) + 1)
                move_id = self.next_id
                self.next_id += 1
                self.encoder_watch = dict(epoch=encoder.get('epoch'), moveId=move_id) if required else None
            self.request(f"MOVE {move_id} {plan['steps']} {plan['rate']} {plan['acceleration']}", 'MOVE', move_id, generation=generation)
            self.event(f"Move {move_id}: {plan['steps']} pulses; {plan['turns']:.4f} commanded turns")
        return plan

    def zero(self):
        with self.operation_lock:
            self.request('ZERO', 'ZERO')
            with self.lock:
                self.calibration = None
            self.event('Commanded pulse position zeroed (this is not an encoder measurement)')

    def alarm_mode(self, mode):
        if type(mode) is not int or mode not in (0, 1, 2):
            raise ValueError('Invalid alarm mode')
        with self.operation_lock:
            self.request(f'ALARM {mode}', 'ALARM')
        self.event('Driver alarm mode changed; only use a polarity verified on the actual driver')

    def connect_encoder(self, port):
        with self.lock:
            if self.state.get('moving'):
                raise ValueError('Stop before changing encoder connections')
            if port == self.port:
                raise ValueError('The external encoder must be a separate USB board')
        self.disconnect_encoder()
        device = next((p for p in ports() if p['port'] == port), None)
        if not device:
            raise ValueError('Select a connected external Y encoder board')
        if device['serial'] == 'D92DB2CC515452344D202020FF094441':
            raise ValueError('Preserve the existing X controller; select the Y encoder')
        link = serial.Serial(port, 115200, timeout=.01, write_timeout=.2, exclusive=True)
        with self.lock:
            self.encoder_link = link
            self.encoder_state = None
            self.encoder_zero = None
        self.event('Listening for BM1 Y reports; no firmware is written to this encoder board')

    def disconnect_encoder(self):
        with self.lock:
            if self.state.get('moving') and self.encoder_required:
                self.stop('External encoder disconnected during a monitored move')
            link, self.encoder_link = self.encoder_link, None
            self.encoder_state = self.encoder_zero = None
        if link:
            link.close()

    def zero_encoder(self, data):
        travel = data.get('travelPerRev')
        scale = None if travel in ('', None, 0, '0') else finite(travel, 'Encoder travel/revolution in inches', .001, 1000)
        sign = data.get('sign', 1)
        if type(sign) is not int or sign not in (-1, 1):
            raise ValueError('Invalid encoder direction')
        with self.lock:
            if self.state.get('moving'):
                raise ValueError('Stop before zeroing the encoder')
            state = self._encoder_snapshot()
            if not state.get('valid'):
                raise ValueError('A valid external Y encoder signal is required')
            self.encoder_zero = (state['epoch'], state['counts'])
            self.encoder_scale = scale
            self.encoder_sign = sign
        self.event('External encoder zeroed')

    def _encoder_snapshot(self):
        state = dict(self.encoder_state or {})
        state['connected'] = self.encoder_link is not None
        age = time.monotonic() - self.encoder_rx
        state['ageMs'] = round(age * 1000) if self.encoder_rx else None
        state['valid'] = bool(state.get('signal') and age < .3)
        state['zeroed'] = bool(state['valid'] and self.encoder_zero and self.encoder_zero[0] == state.get('epoch'))
        if state['zeroed']:
            state['turns'] = (state['counts'] - self.encoder_zero[1]) / 4096 * self.encoder_sign
            state['inches'] = state['turns'] * self.encoder_scale if self.encoder_scale else None
        return state

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
            age = time.monotonic() - self.last_rx
            state.update(connected=self.link is not None, port=self.port, boardSerial=self.board_serial,
                         fresh=bool(self.link and age < .4), ageMs=round(age * 1000) if self.link else None,
                         error=self.error, encoder=self._encoder_snapshot(),
                         calibration=self.calibration, events=list(self.events)[-15:])
            if self.calibration:
                state['commandedTurns'] = state['steps'] / self.calibration['ppr']
                state['estimatedInches'] = state['commandedTurns'] * math.pi * self.calibration['diameter']
            return state

    def _receive(self, line):
        state = parse_status(line)
        with self.condition:
            if state:
                self.state = state
                self.last_rx = time.monotonic()
                row = dict(utc=datetime.now(timezone.utc).isoformat(), **state)
                encoder = self._encoder_snapshot()
                row.update(encoderValid=encoder['valid'], encoderCounts=encoder.get('counts'),
                           encoderTurns=encoder.get('turns'), encoderInches=encoder.get('inches'))
                cal = self.calibration
                row.update(pulsesPerRev=cal['ppr'] if cal else None,
                           drumDiameterIn=cal['diameter'] if cal else None,
                           commandedTurns=state['steps'] / cal['ppr'] if cal else None,
                           estimatedInches=state['steps'] / cal['ppr'] * math.pi * cal['diameter'] if cal else None)
                self.logs.append(row)
                if (self.encoder_watch and not state['moving'] and
                        state['moveId'] == self.encoder_watch['moveId']):
                    self.encoder_watch = None
            elif line.startswith(('SBA ', 'SBE ')):
                self.acks.append((time.monotonic(), line))
                self.condition.notify_all()

    def _receive_encoder(self, line):
        fields = line.split()
        if len(fields) != 6 or fields[:2] != ['BM1', 'Y']:
            return
        try:
            _, _, sequence, counts, status, epoch = fields
            state = dict(sequence=int(sequence), counts=int(counts), signal=status == 'idle', epoch=int(epoch))
        except ValueError:
            return
        with self.lock:
            self.encoder_state = state
            self.encoder_rx = time.monotonic()

    def _loop(self):
        last_ping = 0.
        while not self.closed:
            with self.lock:
                link, encoder_link = self.link, self.encoder_link
                lease, last_rx = self.lease, self.last_rx
                moving = self.state.get('moving')
                watch = self.encoder_watch
            now = time.monotonic()
            if link:
                try:
                    if lease and now - lease > .55:
                        self.stop('Control page heartbeat lost; stopped and disarmed')
                    elif lease and now - last_ping > .15:
                        self.write('PING')
                        last_ping = now
                    if now - last_rx > .6 and self.state.get('armed'):
                        self.stop('Motor telemetry lost; stopped and disarmed')
                    line = link.readline(200).decode('ascii', errors='replace').strip()
                    if line:
                        self._receive(line)
                except (serial.SerialException, OSError, TypeError) as exc:
                    with self.lock:
                        self.error = f'USB motor connection lost: {exc}'
                    self.disconnect()
            if encoder_link:
                try:
                    for _ in range(12):
                        line = encoder_link.readline(200).decode('ascii', errors='replace').strip()
                        if line:
                            self._receive_encoder(line)
                        if not encoder_link.in_waiting:
                            break
                except (serial.SerialException, OSError, TypeError):
                    self.disconnect_encoder()
            if moving and watch:
                with self.lock:
                    enc = self._encoder_snapshot()
                if not enc['valid'] or enc.get('epoch') != watch['epoch']:
                    self.stop('External encoder signal/reference lost during a monitored move')
            if not link and not encoder_link:
                time.sleep(.03)

    def export_csv(self):
        with self.lock:
            rows = list(self.logs)
        stream = io.StringIO()
        fields = ['utc', 'sequence', 'armed', 'moving', 'steps', 'remaining', 'rate', 'reason',
                  'alarmRaw', 'pendRaw', 'alarmMode', 'moveId', 'pulsesPerRev', 'drumDiameterIn',
                  'commandedTurns', 'estimatedInches', 'encoderValid', 'encoderCounts', 'encoderTurns', 'encoderInches']
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return stream.getvalue().encode()

    def close(self):
        self.closed = True
        self.disconnect()
        self.disconnect_encoder()
        self.worker.join(timeout=1)


def make_handler(controller, token, address):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, content_type='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.headers.get('Host') != address:
                return self.send(403, {'error': 'Local controller only'})
            if self.path == '/':
                html = (ROOT / 'index.html').read_text().replace('__TOKEN__', token)
                return self.send(200, html.encode(), 'text/html; charset=utf-8')
            if self.path == '/api/state':
                return self.send(200, controller.snapshot())
            if self.path == '/api/ports':
                return self.send(200, ports())
            if self.path == '/log.csv':
                return self.send(200, controller.export_csv(), 'text/csv; charset=utf-8')
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if self.headers.get('Host') != address:
                return self.send(403, {'error': 'Local controller only'})
            if self.headers.get('X-Bench-Token') != token:
                return self.send(403, {'error': 'Page expired. Refresh this page, then click Arm motor again.',
                                       'code': 'page_expired'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 4096:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('Expected an object')
                result = None
                if self.path == '/api/heartbeat': controller.heartbeat()
                elif self.path == '/api/connect': controller.connect(data['port'])
                elif self.path == '/api/disconnect': controller.disconnect()
                elif self.path == '/api/arm': controller.arm()
                elif self.path == '/api/move': result = controller.move(data)
                elif self.path == '/api/stop': controller.stop()
                elif self.path == '/api/zero': controller.zero()
                elif self.path == '/api/alarm': controller.alarm_mode(data['mode'])
                elif self.path == '/api/encoder/connect': controller.connect_encoder(data['port'])
                elif self.path == '/api/encoder/disconnect': controller.disconnect_encoder()
                elif self.path == '/api/encoder/zero': controller.zero_encoder(data)
                else: return self.send(404, {'error': 'Not found'})
                return self.send(200, {'ok': True, 'result': result})
            except (ValueError, KeyError, serial.SerialException, OSError) as exc:
                return self.send(400, {'error': str(exc)})
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    controller = BenchController()
    address = f'127.0.0.1:{args.port}'
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(controller, secrets.token_urlsafe(24), address))
    server.daemon_threads = True
    def shutdown(*_):
        controller.stop('Bench application closing')
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f'Stepper Bench: http://{address}/ — no automatic connection or movement.', flush=True)
    if not args.no_browser:
        webbrowser.open(f'http://{address}/')
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        controller.close()
        server.server_close()


if __name__ == '__main__':
    main()
