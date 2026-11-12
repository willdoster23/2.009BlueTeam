"""Pi 4 hardware PWM with the Arduino tap timing."""
import math
import os
from pathlib import Path
import pwd
import sys
import threading
import time

PERIOD_NS = 2_040_000  # ATmega328P Uno D9: 16 MHz / (64 * 510)
PEAK_NS = PERIOD_NS * 210 // 255
HOLD_NS = PERIOD_NS * 50 // 255
PEAK_SECONDS = 0.050
PULSE_SECONDS = 0.080
LEASE_SECONDS = 1.6


def channel():
    for chip in Path('/sys/class/pwm').glob('pwmchip*'):
        if chip.joinpath('device').resolve().name == 'fe20c000.pwm':
            return chip / 'pwm0'
    raise FileNotFoundError('GPIO18 hardware PWM is unavailable')


def write(path, name, value):
    (path / name).write_text(str(value))


def setup(username):
    path = channel()
    if not path.exists():
        write(path.parent, 'export', 0)
    write(path, 'enable', 0)
    write(path, 'duty_cycle', 0)
    write(path, 'period', PERIOD_NS)
    write(path, 'polarity', 'normal')
    uid = pwd.getpwnam(username).pw_uid
    for name in ('duty_cycle', 'enable'):
        os.chown(path / name, uid, -1)


class TapController:
    def __init__(self):
        self.path = channel()
        self.condition = threading.Condition()
        self.mode = 'idle'
        self.run_id = None
        self.stage = 'peak'
        self.next_start = 0.0
        self.lease = 0.0
        self.hz = 2.0
        self.error = None
        write(self.path, 'duty_cycle', 0)
        write(self.path, 'enable', 1)
        threading.Thread(target=self.work, daemon=True).start()

    def status(self):
        with self.condition:
            return {'available': self.error is None, 'mode': self.mode,
                    'hz': self.hz, 'runId': self.run_id, 'error': self.error}

    def stop(self):
        write(self.path, 'duty_cycle', 0)
        self.mode = 'idle'
        self.run_id = None

    def command(self, payload):
        action = payload.get('action')
        run_id = payload.get('id')
        with self.condition:
            if self.error:
                return 503, {'error': self.error}
            if action == 'stop':
                self.stop()
                self.condition.notify()
                return 200, self.status()
            if action == 'renew':
                if self.mode != 'auto' or self.run_id != run_id or time.monotonic() >= self.lease:
                    return 409, {'error': 'Automatic tapping has stopped'}
                self.lease = time.monotonic() + LEASE_SECONDS
                self.condition.notify()
                return 200, self.status()
            if action not in ('single', 'auto') or not isinstance(run_id, str) or not 1 <= len(run_id) <= 100:
                return 400, {'error': 'Invalid tap request'}
            if self.run_id == run_id:
                return 200, self.status()
            if self.mode != 'idle' or time.monotonic() < self.next_start:
                return 409, {'error': 'Tap is running or waiting for the next interval'}
            hz = payload.get('hz', 2.0) if action == 'auto' else 2.0
            if isinstance(hz, bool) or not isinstance(hz, (float, int)) or not math.isfinite(hz) or not 0.1 <= hz <= 12.4:
                return 400, {'error': 'Enter a frequency from 0.1 to 12.4 Hz'}
            now = time.monotonic()
            self.hz = hz
            self.interval = int(1000 / hz + 0.5) / 1000
            self.mode, self.run_id, self.stage = action, run_id, 'peak'
            self.when, self.lease = now, now + LEASE_SECONDS
            self.condition.notify()
            return 200, self.status()

    def work(self):
        with self.condition:
            while True:
                if self.mode == 'idle':
                    self.condition.wait()
                    continue
                now = time.monotonic()
                try:
                    if self.mode == 'auto' and now >= self.lease:
                        self.stop()
                        continue
                    deadline = min(self.when, self.lease) if self.mode == 'auto' else self.when
                    if now < deadline:
                        self.condition.wait(deadline - now)
                        continue
                    if self.stage == 'peak':
                        write(self.path, 'duty_cycle', PEAK_NS)
                        self.next_start = now + self.interval
                        self.pulse_end = now + PULSE_SECONDS
                        self.stage, self.when = 'hold', now + PEAK_SECONDS
                    elif self.stage == 'hold' and now < self.pulse_end:
                        write(self.path, 'duty_cycle', HOLD_NS)
                        self.stage, self.when = 'release', self.pulse_end
                    else:
                        write(self.path, 'duty_cycle', 0)
                        if self.mode == 'single':
                            self.stop()
                        else:
                            self.stage, self.when = 'peak', self.next_start
                except OSError:
                    self.error = 'Hardware PWM fault'
                    try:
                        self.stop()
                    finally:
                        # systemd resets the PWM output if this process exits.
                        os._exit(1)


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'setup':
            setup(sys.argv[2])
        else:
            path = channel()
            if path.exists():
                write(path, 'duty_cycle', 0)
                write(path, 'enable', 0)
    except FileNotFoundError:
        # The controller can serve telemetry before the PWM overlay is enabled.
        pass
