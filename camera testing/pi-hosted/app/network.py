"""Fixed device roles, Ethernet-only requests, and bench telemetry."""
import copy
import http.client
import json
from pathlib import Path
import threading
import time

ROBOT_IP = '192.168.2.3'
ROOF_IP = '192.168.2.4'
KEY_FILE = Path('/opt/blueteam-network/network.key')
lock = threading.Lock()
devices = {role: {'reachable': False, 'error': 'Waiting for controller', 'data': None}
           for role in ('roof', 'robot')}


class EthernetConnection(http.client.HTTPConnection):
    def __init__(self, host, port, timeout):
        source = '127.0.0.1' if host == ROOF_IP else ROOF_IP
        super().__init__(host, port, timeout=timeout, source_address=(source, 0))


def request(role, path, payload=None):
    address = {'roof': ROOF_IP, 'robot': ROBOT_IP}[role]
    key = KEY_FILE.read_text().strip()
    connection = EthernetConnection(address, 8091, timeout=1.2)
    try:
        body = None if payload is None else json.dumps(payload)
        connection.request('GET' if payload is None else 'POST', path, body,
                           {'X-Network-Key': key, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = json.loads(response.read(65536))
        return response.status, data
    finally:
        connection.close()


def poll(role):
    while True:
        start = time.monotonic()
        try:
            status, data = request(role, '/api/status')
            if status != 200:
                raise ValueError(data.get('error', 'Controller unavailable'))
            if data.get('role') != role:
                raise ValueError('Controller identity does not match its configured role')
            value = {'reachable': True, 'checked': time.monotonic(), 'data': data,
                     'responseMs': round((time.monotonic() - start) * 1000, 2)}
        except (OSError, ValueError, http.client.HTTPException):
            value = {'reachable': False, 'error': 'Controller unavailable on Ethernet', 'data': None}
        with lock:
            devices[role] = value
        time.sleep(max(0.05, 0.5 - (time.monotonic() - start)))


def status():
    with lock:
        result = copy.deepcopy(devices)
    for value in result.values():
        age = time.monotonic() - value.pop('checked', 0)
        value['reachable'] = value['reachable'] and age < 2
        value['ageSeconds'] = round(age, 2) if value['reachable'] else None
    result['addresses'] = {'robot': ROBOT_IP, 'roof': ROOF_IP}
    result['configured'] = KEY_FILE.is_file()
    return result


def command(payload):
    if payload.get('action') not in ('up', 'down', 'start', 'stop'):
        return 400, {'error': 'Unknown winch command'}
    roof = status()['roof']
    if not roof['reachable'] or roof['data']['relays']['mode'] != 'simulation':
        return 503, {'error': 'Rooftop simulation is unavailable'}
    try:
        # A command is sent once. A lost acknowledgement never triggers a replay.
        return request('roof', '/api/winch', payload)
    except (OSError, ValueError, http.client.HTTPException):
        return 503, {'error': 'Command acknowledgement unavailable; check controller status'}


def tap(payload):
    try:
        return request('robot', '/api/tap', payload)
    except (OSError, ValueError, http.client.HTTPException):
        return 503, {'error': 'Robot tap controller is unavailable'}


def start():
    for role in devices:
        threading.Thread(target=poll, args=(role,), daemon=True).start()
