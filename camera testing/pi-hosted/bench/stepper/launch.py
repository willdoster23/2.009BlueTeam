#!/usr/bin/env python3
"""Start/stop this bench application without touching the inspection services."""
import argparse
import http.client
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import webbrowser

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / 'runtime'
PID = RUNTIME / 'controller.pid'
URL = 'http://127.0.0.1:8766/'


def running():
    try:
        connection = http.client.HTTPConnection('127.0.0.1', 8766, timeout=.4)
        connection.request('GET', '/api/state')
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status == 200 and b'"encoder"' in body and b'"boardSerial"' in body
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    if args.stop:
        if not PID.exists():
            print('Stepper Bench is not running.')
            return
        pid = int(PID.read_text())
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], text=True, capture_output=True)
        if result.returncode == 0 and str(ROOT / 'controller.py') in result.stdout:
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):
                if not running():
                    break
                time.sleep(.1)
            print('Stopped Stepper Bench. Step pulses stop; motor power is unchanged.')
        else:
            print('No matching Stepper Bench process is running.')
        PID.unlink(missing_ok=True)
        return
    if not running():
        with (RUNTIME / 'controller.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, str(ROOT / 'controller.py'), '--no-browser'],
                                     cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        PID.write_text(str(child.pid))
        for _ in range(40):
            if running():
                break
            if child.poll() is not None:
                raise SystemExit('Could not start Stepper Bench. See runtime/controller.log.')
            time.sleep(.1)
        else:
            child.terminate()
            PID.unlink(missing_ok=True)
            raise SystemExit('Stepper Bench did not start. See runtime/controller.log.')
    if not args.no_browser:
        webbrowser.open(URL)
    print(f'Stepper Bench is ready at {URL}\nConnection and movement require your button presses.')


if __name__ == '__main__':
    main()
