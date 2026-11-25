#!/usr/bin/env python3
"""Flash only a connected Nano R4; never select the X Nano Every's port."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time


def main():
    root = Path('/opt/brick-mapper')
    firmware = root / 'firmware/y_nano_r4/y_nano_r4.ino.bin'
    manifest = json.loads((firmware.parent / 'manifest.json').read_text())
    assert hashlib.sha256(firmware.read_bytes()).hexdigest() == manifest['sha256'], 'Firmware checksum mismatch'
    assert os.geteuid() == 0, 'Run with sudo to access DFU and pause the controller'
    boards = []
    for device in Path('/sys/bus/usb/devices').iterdir():
        try:
            if ((device / 'idVendor').read_text().strip() == '2341' and
                    (device / 'idProduct').read_text().strip() in ('0074', '0374')):
                boards.append(device)
        except OSError:
            pass
    if len(boards) != 1:
        print('Connect exactly one Nano R4 to the roof Pi by USB-C. No board was flashed.')
        return 3
    sys.path.insert(0, str(root / 'app'))
    import network
    code, state = network.request('roof', '/api/status', timeout=3)
    assert code == 200, 'Controller status unavailable; no board was flashed'
    assert not any((state.get(key) or {}).get('moving') for key in ('xAxis', 'yAxis')), 'Finish motion before programming'
    relays = state.get('relays') or {}
    assert not relays.get('busy') and not relays.get('action'), 'Finish relay command before programming'
    assert not any(channel.get('closed') for channel in relays.get('channels', [])), 'Relay active; no board was flashed'
    command = [str(root / 'tools/dfu-util'), '--device', '2341:0074,2341:0374',
               '-D', str(firmware), '-a', '0', '-R']
    print('Programming the Y Nano R4 with D2 hardware capture. X firmware is untouched.', flush=True)
    subprocess.run(['systemctl', 'stop', 'blueteam-network.service'], check=True)
    try:
        result = subprocess.run(command, timeout=45)
    finally:
        subprocess.run(['systemctl', 'start', 'blueteam-network.service'], check=True)
    if result.returncode:
        print('Upload tool reported failure. If the R4 did not enter DFU mode, press RESET twice, then retry.')
        return result.returncode
    for attempt in range(20):
        time.sleep(.25)
        code, state = network.request('roof', '/api/status', timeout=2)
        y = state.get('yAxis') or {}
        if code == 200 and y.get('encoderUsbConnected'):
            print('Nano R4 is reporting Y encoder data over USB.')
            print('PWM signal valid:', y.get('encoderSignalValid'))
            print('Set Y zero again after the valid encoder signal is restored.')
            return 0
    print('Firmware upload finished; Y serial reports are not yet detected. Check USB and the board LED.')
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
