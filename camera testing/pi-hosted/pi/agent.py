#!/usr/bin/env python3
"""Ethernet bench test. Relay outputs are simulated and never touch GPIO."""
import argparse
import collections
import datetime
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import threading
import time
import uuid


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class BenchState:
    def __init__(self, role, address, peer, token):
        self.role, self.address, self.peer, self.token = role, address, peer, token
        self.boot_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.enabled = False
        self.action = None
        self.until = 0
        self.updated = self.started
        self.x, self.y = 1000.0, 2000.0
        self.sequence = 0
        self.received = 0
        self.last_received = None
        self.exchange = {'connected': False, 'sent': 0, 'acknowledged': 0}
        self.commands = collections.OrderedDict()
        self.events = collections.deque(maxlen=30)
        self.tap = None

    def update(self):
        now = time.monotonic()
        moving_until = min(now, self.until)
        if self.enabled and self.action in ('up', 'down'):
            duration = max(0, moving_until - self.updated)
            self.y += duration * (100 if self.action == 'up' else -100)
        if now >= self.until:
            self.action = None
        self.updated = now

    def snapshot(self):
        with self.lock:
            self.update()
            return {
                'role': self.role, 'hostname': socket.gethostname(),
                'address': self.address, 'bootId': self.boot_id,
                'time': utc_now(), 'uptimeSeconds': round(time.monotonic() - self.started, 1),
                'position': {'x': round(self.x), 'y': round(self.y),
                             'source': 'simulated-rooftop', 'units': 'encoder counts'},
                'relays': {'mode': 'simulation', 'enabled': self.enabled,
                           'action': self.action, 'channels': [
                               {'channel': n, 'label': label, 'closed': self.action == action}
                               for n, label, action in ((1, 'Up', 'up'), (2, 'Down', 'down'),
                                                        (3, 'Start', 'start'), (4, 'Stop', 'stop'))]},
                'exchange': dict(self.exchange), 'receivedPackets': self.received,
                'lastReceived': self.last_received, 'events': list(self.events),
                'tap': self.tap.status() if self.tap else {'available': False},
            }

    def command(self, action, command_id):
        with self.lock:
            self.update()
            if command_id in self.commands:
                previous = self.commands[command_id]
                if previous['action'] != action:
                    return 409, {'error': 'Command ID was already used for another action'}
                return 200, dict(previous, duplicate=True)
            if action in ('up', 'down') and not self.enabled:
                return 409, {'error': 'Press Start before trying a simulated movement'}
            if action in ('up', 'down') and self.action in ('up', 'down'):
                return 409, {'error': 'A simulated movement is already in progress'}
            if action == 'start':
                self.enabled = True
            if action == 'stop':
                self.enabled = False
            self.action, self.until = action, time.monotonic() + 0.3
            result = {'id': command_id, 'action': action, 'mode': 'simulation',
                      'acceptedAt': utc_now(), 'pulseMs': 300,
                      'message': 'Simulated ' + action + ' accepted by rooftop controller'}
            self.commands[command_id] = result
            while len(self.commands) > 200:
                self.commands.popitem(last=False)
            self.events.append(result)
            return 200, result

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
                    # Reconnection always begins with all simulated contacts released.
                    self.update()
                    self.action, self.enabled = None, False
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
        self.respond(200, self.server.state.snapshot())

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
                if state.tap is None:
                    return self.respond(503, {'error': 'Hardware PWM is unavailable'})
                return self.respond(*state.tap.command(payload))
            if self.path == '/api/winch' and state.role == 'roof':
                action, command_id = payload.get('action'), payload.get('id')
                if action not in ('up', 'down', 'start', 'stop'):
                    raise ValueError('Unknown winch command')
                if not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
                    raise ValueError('Missing command ID')
                if action != 'stop' and not state.snapshot()['exchange'].get('connected'):
                    return self.respond(409, {'error': 'Robot link is unavailable'})
                return self.respond(*state.command(action, command_id))
            self.respond(404, {'error': 'Not found'})
        except (ValueError, TypeError, OSError):
            self.respond(400, {'error': 'Invalid request'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('roof', 'robot'), required=True)
    parser.add_argument('--tap', action='store_true')
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    address, peer = ('192.168.2.4', '192.168.2.3') if args.role == 'roof' else ('192.168.2.3', '192.168.2.4')
    token = args.token_file.read_text().strip()
    if len(token) < 32:
        raise SystemExit('Network key is missing or too short')
    state = BenchState(args.role, address, peer, token)
    if args.tap and args.role == 'robot':
        from tap import TapController
        try:
            state.tap = TapController()
        except OSError as error:
            print(f'Tap unavailable: {error}', flush=True)
    with ThreadingHTTPServer((address, 8091), Handler) as server:
        server.state = state
        server.daemon_threads = True
        if args.role == 'roof':
            threading.Thread(target=state.exchange_loop, daemon=True).start()
        print(f'{args.role} bench agent on {address}:8091; simulated relays', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
