"""Relative X-axis moves using a DRV8871 and channel-A rising edges."""
from collections import OrderedDict
import json
import math
import threading
import time

MM_PER_COUNT = math.pi * 24 / (108 * 11)
MAX_JOG_INCHES = 6
PIN_NAMES = ('driver_in1', 'driver_in2', 'encoder_a', 'encoder_b')
CONFIG = '/etc/brick-mapper/x-axis.json'


class Motor:
    def __init__(self, pins):
        from gpiozero import DigitalInputDevice, PWMOutputDevice

        self.lock = threading.Lock()
        self.count = 0
        self.devices = []
        try:
            for pin in pins[:2]:
                self.devices.append(PWMOutputDevice(pin, initial_value=0, frequency=1000))
            for pin in pins[2:]:
                self.devices.append(DigitalInputDevice(pin, pull_up=True))
            self.in1, self.in2, self.a, self.b = self.devices
            # With a pull-up, deactivation is a physical rising edge.
            self.a.when_deactivated = self._rising
        except Exception:
            self.close()
            raise

    def _rising(self):
        with self.lock:
            self.count += 1 if self.b.pin.state else -1

    def position(self):
        with self.lock:
            return self.count

    def drive(self, direction, duty):
        active, inactive = (self.in1, self.in2) if direction > 0 else (self.in2, self.in1)
        inactive.off()
        active.value = duty

    def stop(self):
        try:
            self.in1.off()
        finally:
            self.in2.off()

    def close(self):
        for device in reversed(self.devices):
            device.close()


class XAxis:
    def __init__(self, motor=None, config_error=None):
        self.motor = motor
        self.mode = 'live' if motor else 'simulation'
        self.lock = threading.RLock()
        self.sample_count = 0
        self.active = None
        self.closed = False
        self.error = config_error
        self.message = 'X zero is relative to controller startup.' if motor else 'Ready to simulate X travel.'
        self.handled = OrderedDict()

    @classmethod
    def from_file(cls, path, reserved_pins):
        try:
            pins = json.loads(path.read_text())
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as error:
            return cls(config_error=f'Cannot read X-axis pin configuration: {error}')
        if not isinstance(pins, dict) or set(pins) != set(PIN_NAMES):
            return cls(config_error='X-axis configuration must specify the four GPIO fields.')
        pins = [pins[name] for name in PIN_NAMES]
        if all(pin is None for pin in pins):
            return cls()
        if (any(type(pin) is not int or not 2 <= pin <= 27 for pin in pins)
                or min(pins[:2]) < 9
                or len(set(pins)) != len(pins) or reserved_pins.intersection(pins)):
            return cls(config_error='Choose four distinct BCM GPIO pins that are free on the rooftop Pi.')
        try:
            return cls(Motor(pins))
        except Exception as error:
            print(f'X-axis GPIO unavailable: {error}', flush=True)
            return cls(config_error='X-axis GPIO could not be opened. Check the pins and permissions.')

    def position(self):
        return self.motor.position() if self.motor else self.sample_count

    def snapshot(self):
        with self.lock:
            count = self.position()
            return {'mode': self.mode if self.motor or not self.error else 'unavailable',
                    'ready': not self.closed and not self.error, 'moving': self.active is not None,
                    'counts': count, 'inches': round(count * MM_PER_COUNT / 25.4, 4),
                    'maxJogInches': MAX_JOG_INCHES, 'message': self.error or self.message}

    def _remember(self, command_id):
        self.handled[command_id] = None
        if len(self.handled) > 200:
            self.handled.popitem(last=False)

    def command(self, payload):
        action, command_id = payload.get('action'), payload.get('id')
        if action not in ('left', 'right', 'stop') or not isinstance(command_id, str) or not 1 <= len(command_id) <= 100:
            return 400, {'error': 'Invalid X-axis command'}
        with self.lock:
            if action == 'stop':
                cancel_id = payload.get('cancelId')
                if isinstance(cancel_id, str) and 1 <= len(cancel_id) <= 100:
                    self._remember(cancel_id)
                self.stop()
                if self.error and self.motor:
                    return 503, {'error': self.error}
                return 200, self.snapshot()
            if self.closed or self.error:
                return 503, {'error': self.error or 'X-axis controller is closed'}
            if payload.get('mode') != self.mode:
                return 409, {'error': 'Refresh Brick Mapper to use the current X-axis mode.'}
            if command_id in self.handled:
                return 409, {'error': 'This X-axis request was already handled or canceled.'}
            if self.active is not None:
                return 409, {'error': 'An X move is already in progress.'}
            inches = payload.get('inches')
            if type(inches) not in (int, float) or not 0.1 <= inches <= MAX_JOG_INCHES:
                return 400, {'error': f'Choose an X distance from 0.1 to {MAX_JOG_INCHES} inches.'}
            direction = 1 if action == 'right' else -1
            start = self.position()
            target = start + direction * round(inches * 25.4 / MM_PER_COUNT)
            self._remember(command_id)
            self.active = command_id
            self.message = f'Moving {action} {inches:g} in…'
            threading.Thread(target=self._move, args=(command_id, direction, start, target),
                             daemon=True).start()
            return 200, self.snapshot()

    def _move(self, command_id, direction, start, target):
        began = last_progress = time.monotonic()
        timeout = min(12, 2 + abs(target - start) * MM_PER_COUNT / 12)
        furthest = 0
        while True:
            with self.lock:
                if self.active != command_id:
                    return
                now = time.monotonic()
                if not self.motor:
                    distance = round((now - began) * 40 / MM_PER_COUNT)
                    self.sample_count = start + direction * min(distance, abs(target - start))
                count = self.position()
                progress = direction * (count - start)
                remaining = direction * (target - count) * MM_PER_COUNT
                if progress > furthest:
                    furthest, last_progress = progress, now
                if remaining <= 0:
                    self.stop('X target reached.')
                    return
                if progress <= -4:
                    self.stop('X stopped because the encoder counted in the opposite direction.')
                    return
                if now - last_progress >= 0.65 or now - began >= timeout:
                    self.stop('X stopped before its target. Check motor and encoder feedback.')
                    return
                if self.motor:
                    try:
                        self.motor.drive(direction, min(0.55, 0.30 + remaining * 0.05))
                    except Exception as error:
                        print(f'X-axis drive failed: {error}', flush=True)
                        self.error = 'X motor output fault.'
                        self.stop()
                        return
            time.sleep(0.01)

    def stop(self, message='X stopped.'):
        with self.lock:
            self.active = None
            self.message = message
            try:
                if self.motor:
                    self.motor.stop()
            except Exception as error:
                print(f'X-axis stop failed: {error}', flush=True)
                self.error = 'X motor output could not be released. Cut motor power.'
                return False
            return True

    def close(self):
        with self.lock:
            self.closed = True
            self.stop()
            if self.motor:
                self.motor.close()
