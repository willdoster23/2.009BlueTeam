#!/usr/bin/env python3
"""On the roof Pi, install the prepared X/Y images by fixed USB identity."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MOTOR_SERIALS = {'X': 'D92DB2CC515452344D202020FF094441',
                 'Y': '498847005154524C4A202020FF0E4352'}
PULLEY_SERIAL = '73F97AA75154524C4A202020FF0C3720'
ROOT = Path('/opt/brick-mapper')
SERVICE = 'blueteam-network.service'


def run(*args, timeout=15):
    result = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr)[-3000:])
    return result.stdout


def port(identity):
    from serial.tools import list_ports
    matches = [p.device for p in list_ports.comports()
               if (p.vid, p.pid, p.serial_number) == (0x2341, 0x0058, identity)]
    if identity == PULLEY_SERIAL or identity not in MOTOR_SERIALS.values() or len(matches) != 1:
        raise RuntimeError('Motor USB identity is missing or ambiguous; encoder is protected.')
    return matches[0]


def programming_mode(identity, reset_usb=False):
    import serial
    # Reset only this motor's SAMD11 USB bridge, leaving the encoder connected.
    if reset_usb:
        for device in Path('/sys/bus/usb/devices').iterdir():
            try:
                if ((device / 'serial').read_text().strip() == identity and
                        (device / 'idVendor').read_text().strip() == '2341' and
                        (device / 'idProduct').read_text().strip() == '0058'):
                    node = '/dev/bus/usb/%03d/%03d' % (
                        int((device / 'busnum').read_text()), int((device / 'devnum').read_text()))
                    with open(node, 'wb', buffering=0) as usb:
                        try: fcntl.ioctl(usb.fileno(), 0x5514)
                        except OSError as error:
                            if error.errno != 19: raise
                    break
            except FileNotFoundError:
                continue
    time.sleep(.8)
    selected = port(identity)
    run('stty', '-F', selected, '1200', 'hupcl')
    time.sleep(.15)
    with serial.Serial(selected, 1200, timeout=.2): time.sleep(.3)
    time.sleep(.7)
    return port(identity)


def verify(axis, target):
    import serial
    reports = []; identity = False; fixed_speed = False
    with serial.Serial(port(target['usbSerial']), 115200, timeout=.15) as link:
        link.reset_input_buffer()
        until = time.monotonic() + 3.5
        next_hello = 0
        while time.monotonic() < until:
            if not identity and time.monotonic() >= next_hello:
                link.write(b'HELLO\n'); next_hello = time.monotonic() + .4
            line = link.readline(200).decode('ascii', errors='replace').strip()
            identity |= line.startswith(target['identity'])
            fixed_speed |= line == 'SBC FIXED=1 PULSE_US=100'
            fields = line.split()
            if len(fields) == 13 and fields[0] == 'SB1':
                if fields[1] != axis or fields[3:6] != ['0', '0', '0']:
                    raise RuntimeError('Unexpected motor identity, armed state or commanded pulses.')
                reports.append(int(fields[2]))
    if not identity or not fixed_speed or len(reports) < 30 or not all(b > a for a, b in zip(reports, reports[1:])):
        raise RuntimeError(f'{axis} passive verification failed: identity={identity}, reports={len(reports)}.')
    return {'axis': axis, 'usbSerial': target['usbSerial'], 'reports': len(reports),
            'armed': False, 'moving': False, 'commandedPulses': 0, 'fixedSpeed': True, 'pulseWidthUs': 100}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--power-disconnected', action='store_true', required=True)
    parser.add_argument('--axis', nargs='+', choices=('X', 'Y'), default=['X', 'Y'],
                        help='Program selected axes; passively verify both before enabling stepper mode.')
    args = parser.parse_args()
    if os.geteuid() != 0: raise RuntimeError('Run as root on the roof Pi.')
    manifest = json.loads((ROOT / 'firmware/brickmapper_stepper/hex/manifest.json').read_text())
    if manifest.get('protectedEncoderSerial') != PULLEY_SERIAL:
        raise RuntimeError('Firmware manifest has the wrong protected encoder.')
    for axis, serial_number in MOTOR_SERIALS.items():
        target = manifest['axes'][axis]
        if (target['usbSerial'] != serial_number or target['file'] != axis + '.hex' or
                target['identity'] != f'SBH1 BRICKMAPPER_STEPPER {axis} 1 '):
            raise RuntimeError('Prepared motor manifest has the wrong identity.')
        image = ROOT / 'firmware/brickmapper_stepper/hex' / target['file']
        if hashlib.sha256(image.read_bytes()).hexdigest() != target['sha256']:
            raise RuntimeError('Prepared motor firmware checksum mismatch.')
        port(serial_number)
    sys.path.insert(0, str(ROOT / 'app'))
    import network
    code, state = network.request('roof', '/api/status', timeout=3)
    if (code != 200 or state.get('role') != 'roof' or
            any((state.get(axis) or {}).get('moving') for axis in ('xAxis', 'yAxis')) or
            (state.get('relays') or {}).get('busy') or (state.get('relays') or {}).get('action') or
            (state.get('poweroff') or {}).get('scheduled')):
        raise RuntimeError('Roof controller must be idle before programming.')
    run('systemctl', 'stop', SERVICE)
    results = []
    try:
        for axis in ('X', 'Y'):
            target = manifest['axes'][axis]
            if axis in args.axis:
                print('Programming ' + axis + ' motor Nano…', flush=True)
                selected = programming_mode(target['usbSerial'])
                image = ROOT / 'firmware/brickmapper_stepper/hex' / target['file']
                run(str(ROOT / 'tools/avrdude/bin/avrdude'), '-C', str(ROOT / 'tools/avrdude/etc/avrdude.conf'),
                    '-p', 'atmega4809', '-c', 'jtag2updi', '-P', selected, '-b', '115200', '-e', '-D',
                    '-U', 'flash:w:' + str(image) + ':i', '-U', 'fuse2:w:0x01:m',
                    '-U', 'fuse5:w:0xC9:m', '-U', 'fuse8:w:0x00:m', timeout=40)
                time.sleep(.3)
            results.append(verify(axis, target))
            print(json.dumps(results[-1]), flush=True)
        # Activate the new protocol only once both motor images are verified.
        override = Path('/etc/systemd/system/blueteam-network.service.d/stepper-axes.conf')
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -E -s '
                            '/opt/blueteam-network/agent.py --role roof --token-file '
                            '/opt/blueteam-network/network.key --stepper-axes\n')
        override.chmod(0o644)
        run('systemctl', 'daemon-reload')
    finally:
        run('systemctl', 'start', SERVICE)
    print('Both motor Nanos verified; stepper controller enabled. No motion commands sent.', flush=True)


if __name__ == '__main__': main()
