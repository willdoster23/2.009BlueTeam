#!/usr/bin/env python3
"""Build separate X/Y motor Nano Every images. Never opens USB or uploads."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'firmware/brickmapper_stepper'
OUTPUT = SOURCE / 'hex'
FQBN = 'arduino:megaavr:nona4809:mode=off'


def main():
    cli = shutil.which('arduino-cli') or '/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli'
    OUTPUT.mkdir(exist_ok=True)
    manifest = dict(fqbn=FQBN, layout='separate-motor-nanos',
                    pins={'X':{'step':9,'dir':8},'Y':{'step':9,'dir':8,'alarm':2,'place':3}},
                    motorReportHz=20, pulseTimerHz=20000, pulseWidthUs=100, fixedSpeedTest=True,
                    maxPulsesPerSecond={'X':5000,'Y':6000}, yMotorRpmCap=200,
                    protectedEncoderSerial='73F97AA75154524C4A202020FF0C3720',
                    sourceSha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE.iterdir() if p.suffix in ('.ino','.h')}, axes={})
    for selection, (axis, identity) in enumerate([
            ('X','D92DB2CC515452344D202020FF094441'),
            ('Y','498847005154524C4A202020FF0E4352')], start=1):
        with tempfile.TemporaryDirectory(prefix='brickmapper-stepper-build-') as build:
            args=[cli,'compile','--fqbn',FQBN,'--build-path',build,'--warnings','all',
                  '--build-property',f'compiler.cpp.extra_flags=-DBRICKMAPPER_MOTOR_AXIS={selection}']
            subprocess.run(args+[str(SOURCE)],check=True)
            image=Path(build,'brickmapper_stepper.ino.hex').read_bytes()
            (OUTPUT/(axis+'.hex')).write_bytes(image)
            manifest['axes'][axis]=dict(file=axis+'.hex',sha256=hashlib.sha256(image).hexdigest(),
                                        usbSerial=identity,identity=f'SBH1 BRICKMAPPER_STEPPER {axis} 1 ')
    (OUTPUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Prepared X.hex for the current X Nano and Y.hex for the current Y motor Nano. No board was opened or programmed.')


if __name__=='__main__':main()
