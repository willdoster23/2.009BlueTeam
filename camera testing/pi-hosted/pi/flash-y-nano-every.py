#!/usr/bin/env python3
"""Program and passively verify the separate Y Nano Every, preserving X."""
from pathlib import Path
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time

X_SERIAL = 'D92DB2CC515452344D202020FF094441'
ROOT = Path('/opt/brick-mapper')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True, help='USB serial number of the Y Nano Every')
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Run with sudo'
    assert re.fullmatch(r'[A-Za-z0-9]+', args.serial), 'Invalid USB serial number'
    assert args.serial != X_SERIAL, 'Refusing to replace the X Nano firmware'
    matches = list(Path('/dev/serial/by-id').glob('*Arduino_Nano_Every_' + args.serial + '-if00'))
    assert len(matches) == 1, 'The selected Y Nano Every is not connected'
    port = str(matches[0])
    firmware = ROOT / 'firmware/y_nano_every/y_nano_every.ino.hex'
    metadata = json.loads((firmware.parent / 'manifest.json').read_text())
    assert hashlib.sha256(firmware.read_bytes()).hexdigest() == metadata['sha256'], 'Firmware checksum mismatch'
    sys.path.insert(0, str(ROOT / 'app'))
    import network
    code, state = network.request('roof', '/api/status', timeout=3)
    assert code == 200, 'Controller unavailable'
    assert not any((state.get(key) or {}).get('moving') for key in ('xAxis', 'yAxis')), 'Finish motion before programming'
    relays = state.get('relays') or {}
    assert not relays.get('busy') and not relays.get('action'), 'Relay busy'
    assert not any(channel.get('closed') for channel in relays.get('channels', [])), 'Relay active'
    backup = Path('/var/backups/brick-mapper') / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-y-nano-every-flash')
    backup.mkdir(mode=0o700)
    origin = Path('/var/lib/brick-mapper-outrigger/pulley-origin.json')
    if origin.exists(): (backup / 'pulley-origin.json').write_bytes(origin.read_bytes())
    base = [str(ROOT / 'tools/avrdude/bin/avrdude'), '-C', str(ROOT / 'tools/avrdude/etc/avrdude.conf'),
            '-p', 'atmega4809', '-c', 'jtag2updi', '-P', port, '-b', '115200']
    import serial

    def programming_mode():
        # MuxTO needs time to leave a prior programming session and must see
        # 1200 baud with DTR high before the falling edge. Linux can coalesce
        # an immediate open/close; explicit separated transitions avoid that.
        time.sleep(.7)
        subprocess.run(['stty', '-F', port, '1200', 'hupcl'], check=True)
        time.sleep(.15)
        with serial.Serial(port, 1200, timeout=.2):
            time.sleep(.25)
        time.sleep(.7)

    reports, diagnostics, gpio_probe = [], [], []
    subprocess.run(['systemctl', 'stop', 'blueteam-network.service'], check=True)
    try:
        # Recover a stalled SAMD11 control endpoint on this Y board only.
        for device in Path('/sys/bus/usb/devices').iterdir():
            try:
                if ((device / 'serial').read_text().strip() == args.serial and
                        (device / 'idVendor').read_text().strip() == '2341' and
                        (device / 'idProduct').read_text().strip() == '0058'):
                    node = '/dev/bus/usb/%03d/%03d' % (int((device / 'busnum').read_text()), int((device / 'devnum').read_text()))
                    with open(node, 'wb', buffering=0) as usb:
                        try:
                            fcntl.ioctl(usb.fileno(), 0x5514)  # USBDEVFS_RESET
                        except OSError as error:
                            # MuxTO deliberately resets itself on a USB reset;
                            # its old USB device disappears while re-enumerating.
                            if error.errno != 19:
                                raise
                    break
            except FileNotFoundError:
                continue
        time.sleep(.7)
        for attempt in range(20):
            if Path(port).exists(): break
            time.sleep(.1)
        programming_mode()
        subprocess.run(base + ['-U', 'flash:r:' + str(backup / 'flash-before.hex') + ':i',
                               '-U', 'eeprom:r:' + str(backup / 'eeprom-before.hex') + ':i'], check=True, timeout=35)
        programming_mode()
        subprocess.run(base + ['-e', '-D', '-U', 'flash:w:' + str(firmware) + ':i',
                               '-U', 'fuse2:w:0x01:m', '-U', 'fuse5:w:0xC9:m', '-U', 'fuse8:w:0x00:m'],
                       check=True, timeout=35)
        # Flash was verified by avrdude. A new position origin is required.
        origin.unlink(missing_ok=True)
        time.sleep(.2)
        with serial.Serial(port, 115200, timeout=.15) as link:
            link.reset_input_buffer()
            until = time.monotonic() + 2.4
            while time.monotonic() < until:
                fields = link.readline().decode('ascii', errors='replace').strip().split()
                try:
                    if len(fields) == 6 and fields[:2] == ['BM1', 'Y']:
                        reports.append((time.monotonic(), int(fields[2]), int(fields[3]), fields[4], int(fields[5])))
                    elif len(fields) == 6 and fields[0] == 'BY1':
                        diagnostics.append(tuple(map(int, fields[1:])))
                    elif len(fields) == 5 and fields[0] == 'BY2':
                        gpio_probe.append(tuple(map(int, fields[1:])))
                except ValueError:
                    pass
    finally:
        subprocess.run(['systemctl', 'start', 'blueteam-network.service'], check=True)
    assert len(reports) >= 100, 'Firmware flashed, but too few Y serial reports were received'
    rate = ((reports[-1][1] - reports[0][1]) & 0xffffffff) / (reports[-1][0] - reports[0][0])
    valid = sum(report[3] == 'idle' for report in reports)
    stats = {'reports': len(reports), 'reportHz': round(rate, 1), 'validPwmReports': valid,
             'epochs': sorted(set(report[4] for report in reports)),
             'countRange': [min(report[2] for report in reports), max(report[2] for report in reports)],
             'diagnostics': diagnostics, 'gpioProbe20ms': gpio_probe, 'backup': str(backup)}
    if len(diagnostics) >= 2:
        stats['capturedFramesPerSecond'] = (diagnostics[-1][0] - diagnostics[0][0]) / (len(diagnostics) - 1)
    print('SERIAL_VERIFICATION', json.dumps(stats), flush=True)
    assert 95 <= rate <= 105, 'Firmware flashed; report timing needs investigation'
    assert valid == len(reports), 'Firmware is running; encoder PWM is missing or invalid on D2'
    assert len(stats['epochs']) == 1, 'Encoder signal was interrupted during verification'
    assert not any(item[3] for item in diagnostics), 'Capture queue overflowed'
    for attempt in range(20):
        code, state = network.request('roof', '/api/status', timeout=2)
        y = state.get('yAxis') or {}
        if code == 200 and y.get('connected'):
            print('CONTROLLER_VERIFICATION', json.dumps({'xConnected': (state.get('xAxis') or {}).get('connected'),
                  'yConnected': y.get('connected'), 'encoderAgeMs': y.get('encoderAgeMs'),
                  'zeroed': y.get('zeroed'), 'rawCounts': y.get('rawCounts')}), flush=True)
            print('Y Nano Every D2 installed and live. Set Y zero before distance moves. No motor was actuated.', flush=True)
            return 0
        time.sleep(.2)
    raise RuntimeError('Serial verification passed, but the roof controller has not received Y yet')


if __name__ == '__main__':
    raise SystemExit(main())
