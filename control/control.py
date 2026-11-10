#!/usr/bin/env python3
"""Manual lifecycle control. Does not install login items or system services.

Works on macOS and Windows.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

WINDOWS = os.name == 'nt'
MAC = sys.platform == 'darwin'
if WINDOWS:
    import msvcrt
else:
    import fcntl

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / '.runtime'
STATE = RUNTIME / 'server.json'
BASE = 'http://127.0.0.1:8767'
URL = BASE + '/map.html'
STOP_NAME = 'Stop.bat' if WINDOWS else 'Stop.command'
MACHINE = 'this PC' if WINDOWS else 'this Mac' if MAC else 'this computer'


def request(path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, headers=headers or {})
    with urllib.request.urlopen(req, timeout=1.5) as response:
        return json.load(response)


def identity():
    try:
        result = request('/api/app')
        return result if result.get('app') == 'blue-team-brick-map' else None
    except (OSError, ValueError):
        return None


def port_open():
    try:
        with socket.create_connection(('127.0.0.1', 8767), timeout=0.3):
            return True
    except OSError:
        return False


def load_state():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def ours(info, state):
    return bool(info and state and info.get('instance') == state.get('instance')
                and info.get('pid') == state.get('pid')
                and info.get('folder') == str(ROOT))


def open_viewer(no_browser):
    if no_browser:
        return
    if MAC:
        subprocess.run(['open', '-a', 'Safari', URL], check=False)
    else:
        webbrowser.open(URL)


def detached_options():
    """Start the server so it keeps running after the launcher window closes."""
    if WINDOWS:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        return {'creationflags': flags}
    return {'start_new_session': True}


def start(no_browser):
    info, state = identity(), load_state()
    if ours(info, state):
        print('Already running. A second server was not started.')
        print(URL)
        open_viewer(no_browser)
        return 0
    if port_open():
        print('Port 8767 is already in use. No other application was stopped.')
        print(f'If another copy of Brick Mapper is running, use its {STOP_NAME} first.')
        return 1
    instance, control_token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    env = dict(os.environ, BRICK_MAP_INSTANCE=instance, BRICK_MAP_CONTROL_TOKEN=control_token,
               PYTHONIOENCODING='utf-8')
    with (RUNTIME / 'server.log').open('ab', buffering=0) as log:
        process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'app' / 'server.py')],
                                   cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   **detached_options())
    expected = {'instance': instance, 'pid': process.pid, 'control_token': control_token}
    for _ in range(50):
        info = identity()
        if ours(info, expected):
            temporary = RUNTIME / 'server.json.tmp'
            temporary.write_text(json.dumps(expected), encoding='utf-8')
            with contextlib.suppress(OSError):
                temporary.chmod(0o600)
            temporary.replace(STATE)
            print(f'Brick Mapper is running locally on {MACHINE}.')
            print(URL)
            print(f'Use {STOP_NAME} when finished. Closing the browser alone does not stop it.')
            open_viewer(no_browser)
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    print('The app could not start. See .runtime/server.log inside this folder.')
    return 1


def stop():
    state, info = load_state(), identity()
    if not port_open():
        STATE.unlink(missing_ok=True)
        print('Brick Mapper is stopped. No local viewer server is listening on port 8767.')
        return 0
    if not ours(info, state):
        print('This folder does not own the service on port 8767. Nothing was stopped.')
        print(f'Use {STOP_NAME} from the folder that started it.')
        return 1
    request('/api/shutdown', b'', {'Origin': BASE, 'X-Control-Token': state['control_token']})
    for _ in range(80):
        if not port_open():
            STATE.unlink(missing_ok=True)
            print('Stopped. Photos and the saved map are preserved.')
            return 0
        time.sleep(0.1)
    print(f'Still finishing a request. Wait a few seconds and run {STOP_NAME} again.')
    return 1


def status():
    info, state = identity(), load_state()
    if ours(info, state):
        print('Running: ' + URL)
        try:
            health = request('/api/health')
            print('Pi bridge: ' + ('reachable' if health.get('reachable') else 'not responding'))
        except OSError:
            print('Pi bridge status unavailable.')
        return 0
    print('Not running from this folder.' + (' Port 8767 is used by another service.' if port_open() else ''))
    return 0


@contextlib.contextmanager
def exclusive_lock(path):
    """Only one start/stop/status runs at a time, on any platform."""
    with path.open('a+') as handle:
        if WINDOWS:
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'status'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with exclusive_lock(RUNTIME / 'control.lock'):
        return start(args.no_browser) if args.action == 'start' else stop() if args.action == 'stop' else status()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, urllib.error.URLError) as error:
        print('Could not complete the action: ' + str(error))
        sys.exit(1)
