#!/usr/bin/env python3
"""Back up, program and passively verify ONE dedicated motor Nano Every."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parent
FIRMWARE = ROOT.parent.parent / 'firmware/winch_nano_every_bench'
PROTECTED = {'D92DB2CC515452344D202020FF094441', '73F97AA75154524C4A202020FF0C3720'}
ARDUINO = Path.home() / 'Library/Arduino15'


def find_port(identity):
    matches = [p.device for p in list_ports.comports()
               if (p.vid, p.pid, p.serial_number) == (0x2341, 0x0058, identity)]
    if len(matches) != 1:
        raise RuntimeError('The specified motor Nano Every is not uniquely connected')
    return matches[0]


def touch(identity):
    time.sleep(.8)  # MuxTO must finish the previous programming session first.
    with serial.Serial(find_port(identity), 1200, timeout=.1) as link:
        time.sleep(.3)
        link.dtr = False
        time.sleep(.1)
    time.sleep(.9)
    for _ in range(30):
        try:
            return find_port(identity)
        except RuntimeError:
            time.sleep(.1)
    raise RuntimeError('Nano did not return after entering programming mode')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', required=True)
    parser.add_argument('--power-disconnected', action='store_true', required=True,
                        help='Confirm the 48 V motor supply is unplugged and the motor is unloaded')
    parser.add_argument('--resume-backup', type=Path, help='Reuse the verified original backup for this same USB serial')
    args = parser.parse_args()
    if args.serial in PROTECTED:
        raise SystemExit('Refusing to overwrite an existing X or Y board')
    find_port(args.serial)
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True)
    build = runtime / 'build'
    cli = shutil.which('arduino-cli') or '/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli'
    subprocess.run([cli, 'compile', '--fqbn', 'arduino:megaavr:nona4809:mode=off',
                    '--build-path', str(build), '--build-cache-path', str(runtime / 'cache'),
                    '--warnings', 'all', str(FIRMWARE)], check=True, timeout=180)
    hex_path = build / 'winch_nano_every_bench.ino.hex'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = args.resume_backup.resolve() if args.resume_backup else ROOT / 'backups' / f'{stamp}-{args.serial}'
    if not args.resume_backup:
        backup.mkdir(parents=True)
    avrdude = ARDUINO / 'packages/arduino/tools/avrdude/6.3.0-arduino17'
    base = [str(avrdude / 'bin/avrdude'), '-C', str(avrdude / 'etc/avrdude.conf'),
            '-p', 'atmega4809', '-c', 'jtag2updi', '-b', '115200']
    log = runtime / 'program.log'

    def run(options):
        port = touch(args.serial)
        result = subprocess.run(base + ['-P', port] + options, capture_output=True, text=True, timeout=60)
        with log.open('a') as stream:
            stream.write(result.stdout + result.stderr + '\n')
        if result.returncode:
            raise RuntimeError(f'Programming command failed. See {log}\n' + result.stderr[-1800:])

    metadata = dict(usbSerial=args.serial, createdUtc=stamp, firmwareSha256=hashlib.sha256(hex_path.read_bytes()).hexdigest(),
                    sourceSha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in FIRMWARE.iterdir() if p.suffix in ('.ino', '.h')},
                    fqbn='arduino:megaavr:nona4809:mode=off', pins={'step': 9, 'dir': 8, 'alarm': 2, 'pend': 3})
    if args.resume_backup:
        original = json.loads((backup / 'manifest.json').read_text())
        assert original['usbSerial'] == args.serial, 'Backup belongs to another board'
        assert original['firmwareSha256'] == metadata['firmwareSha256'], 'Retry firmware differs from the backed-up installation plan'
        assert all((backup / p).stat().st_size > 0 for p in
                   ('flash-before.hex','eeprom-before.hex','fuse2-before.txt','fuse5-before.txt','fuse8-before.txt'))
        print('Reusing original backup for this exact USB serial.', flush=True)
    else:
        print('Backing up this motor Nano only; X/Y boards are protected.', flush=True)
        run(['-U', f'flash:r:{backup / "flash-before.hex"}:i',
             '-U', f'eeprom:r:{backup / "eeprom-before.hex"}:i',
             '-U', f'fuse2:r:{backup / "fuse2-before.txt"}:h',
             '-U', f'fuse5:r:{backup / "fuse5-before.txt"}:h',
             '-U', f'fuse8:r:{backup / "fuse8-before.txt"}:h'])
        (backup / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    # Re-check identity immediately before upload, then after entering programming
    # mode. Never reuse a /dev/cu.* path from a previous USB enumeration.
    current = [p for p in list_ports.comports() if (p.vid,p.pid)==(0x2341,0x0058)]
    assert len(current) == 1 and current[0].serial_number == args.serial, 'Only the intended motor Nano may be attached during programming'
    target = touch(args.serial)
    assert find_port(args.serial) == target
    print(f'Verified motor Nano {args.serial} at {target}; protected X/Y are not selected.', flush=True)
    print('Uploading with Arduino CLI and verifying flash...', flush=True)
    result = subprocess.run([cli, 'upload', '--fqbn', 'arduino:megaavr:nona4809:mode=off',
                             '--port', target, '--input-dir', str(build), '--verify'],
                            capture_output=True, text=True, timeout=90)
    with log.open('a') as stream:
        stream.write(result.stdout + result.stderr + '\n')
    if result.returncode:
        raise RuntimeError('Arduino upload failed. See ' + str(log) + '\n' + result.stderr[-1800:])
    time.sleep(.5)
    samples = []
    identity = False
    with serial.Serial(find_port(args.serial), 115200, timeout=.1, write_timeout=.2) as link:
        link.write(b'HELLO\n')
        until = time.monotonic() + 3
        while time.monotonic() < until:
            line = link.readline(200).decode('ascii', errors='replace').strip()
            if line.startswith('SBH1 WINCH_NANO_EVERY_BENCH 1 '):
                identity = True
            fields = line.split()
            if len(fields) == 12 and fields[0] == 'SB1':
                assert fields[2:5] == ['0', '0', '0'], 'Unexpected armed/moving/nonzero initial state'
                samples.append((time.monotonic(), int(fields[1])))
    assert identity and len(samples) >= 30, 'Firmware uploaded, but passive serial verification failed'
    assert all(b[1] > a[1] for a, b in zip(samples, samples[1:])), 'Nano restarted or sequence did not advance'
    hz = (len(samples) - 1) / (samples[-1][0] - samples[0][0])
    result = dict(**metadata, backup=str(backup), verifiedReports=len(samples), reportingHz=round(hz, 2),
                  armed=False, moving=False, commandedSteps=0, physicalMotionTested=False)
    (runtime / 'installed.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
