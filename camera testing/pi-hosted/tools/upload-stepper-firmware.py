#!/usr/bin/env python3
"""Later, with motor power off: back up and program the selected motor Nano only."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT/'firmware/brickmapper_stepper/hex'
# Fixed board mapping also protects against an accidentally edited manifest.
MOTOR_SERIALS = {'X':'D92DB2CC515452344D202020FF094441',
                'Y':'498847005154524C4A202020FF0E4352'}
PULLEY_SERIAL = '73F97AA75154524C4A202020FF0C3720'


def prepared_target(axis, usb_serial):
    """Validate all local artifacts before enumerating or opening any USB device."""
    if axis not in MOTOR_SERIALS or usb_serial != MOTOR_SERIALS[axis]:
        raise SystemExit('USB identity does not match this motor axis. The pulley encoder must never be overwritten.')
    manifest=json.loads((IMAGES/'manifest.json').read_text())
    target=manifest['axes'][axis]
    if (manifest.get('layout')!='separate-motor-nanos' or target['usbSerial']!=MOTOR_SERIALS[axis] or
            manifest['protectedEncoderSerial']!=PULLEY_SERIAL or
            target['identity']!=f'SBH1 BRICKMAPPER_STEPPER {axis} 1 ' or target['file']!=axis+'.hex'):
        raise SystemExit('Prepared manifest does not match the separate motor Nano layout; rebuild the images.')
    image=IMAGES/target['file']
    if hashlib.sha256(image.read_bytes()).hexdigest()!=target['sha256']:
        raise SystemExit('Prepared firmware checksum mismatch; rebuild the images.')
    for name,digest in manifest['sourceSha256'].items():
        if hashlib.sha256((IMAGES.parent/name).read_bytes()).hexdigest()!=digest:
            raise SystemExit('Firmware source changed; rebuild the images before uploading.')
    return manifest, target, image


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--axis',choices=('X','Y'),required=True)
    parser.add_argument('--serial',required=True)
    parser.add_argument('--power-disconnected',action='store_true',required=True,
                        help='Confirm 48 V is disconnected and both mechanisms are unloaded/supported')
    args=parser.parse_args()
    manifest,target,image=prepared_target(args.axis,args.serial)
    if sys.platform.startswith('linux'):
        result=subprocess.run(['systemctl','is-active','--quiet','blueteam-network.service'])
        if result.returncode==0:raise SystemExit('Stop blueteam-network.service before programming; restart it afterward.')

    def port():
        found=[p.device for p in list_ports.comports() if (p.vid,p.pid,p.serial_number)==(0x2341,0x0058,args.serial)]
        if len(found)!=1:raise RuntimeError('The selected motor Nano is not uniquely connected')
        return found[0]

    def touch():
        time.sleep(.8)
        with serial.Serial(port(),1200,timeout=.1) as link:
            time.sleep(.3);link.dtr=False;time.sleep(.1)
        time.sleep(.9)
        return port()  # Re-enumerate by USB identity immediately before programming.

    selected=port()
    if shutil.which('lsof'):
        owner=subprocess.run(['lsof','-t',selected],capture_output=True,text=True)
        if owner.stdout.strip():raise SystemExit('Close the serial monitor or local controller using this Nano first.')
    arduino=Path.home()/('Library/Arduino15' if sys.platform=='darwin' else '.arduino15')
    installations=sorted((arduino/'packages/arduino/tools/avrdude').glob('*/bin/avrdude'))
    if not installations:raise SystemExit('Install the Arduino megaAVR core with Arduino CLI first.')
    avrdude=installations[-1]
    config=avrdude.parent.parent/'etc/avrdude.conf'
    backup=ROOT/'outputs/stepper-backups'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+args.axis+'-'+args.serial)
    backup.mkdir(parents=True)
    base=[str(avrdude),'-C',str(config),'-p','atmega4809','-c','jtag2updi','-b','115200','-P',touch()]
    result=subprocess.run(base+['-U',f'flash:r:{backup/"flash-before.hex"}:i',
        '-U',f'eeprom:r:{backup/"eeprom-before.hex"}:i',
        '-U',f'fuse2:r:{backup/"fuse2.txt"}:h','-U',f'fuse5:r:{backup/"fuse5.txt"}:h',
        '-U',f'fuse8:r:{backup/"fuse8.txt"}:h'],capture_output=True,text=True,timeout=60)
    (backup/'backup.log').write_text(result.stdout+result.stderr)
    result.check_returncode()
    (backup/'manifest.json').write_text(json.dumps(dict(axis=args.axis,usbSerial=args.serial,firmwareSha256=target['sha256']),indent=2)+'\n')
    cli=shutil.which('arduino-cli') or '/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli'
    selected=touch()
    if selected!=port():raise RuntimeError('USB identity changed before upload')
    subprocess.run([cli,'upload','--fqbn',manifest['fqbn'],'--port',selected,'--input-file',str(image),'--verify'],check=True,timeout=90)
    time.sleep(.5)
    reports=[];identity=False
    with serial.Serial(port(),115200,timeout=.1) as link:
        until=time.monotonic()+3.5
        next_hello=0
        while time.monotonic()<until:
            if not identity and time.monotonic()>=next_hello:
                link.write(b'HELLO\n');next_hello=time.monotonic()+.4
            line=link.readline(200).decode('ascii',errors='replace').strip()
            identity=identity or line.startswith(target['identity'])
            fields=line.split()
            if len(fields)==13 and fields[0]=='SB1':
                if fields[1]!=args.axis:raise RuntimeError('Uploaded board reported the wrong motor axis')
                if fields[3:6]!=['0','0','0']:raise RuntimeError('Unexpected initial motor state')
                reports.append(int(fields[2]))
    if not identity or len(reports)<30 or not all(b>a for a,b in zip(reports,reports[1:])):
        raise RuntimeError('Flash uploaded but passive identity/report verification failed')
    print(f'{args.axis} verified, disarmed, zero commanded pulses. Backup: {backup}')


if __name__=='__main__':main()
