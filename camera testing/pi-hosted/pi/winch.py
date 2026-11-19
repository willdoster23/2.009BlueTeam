"""Momentary commands for the rooftop relay HAT."""
import logging
import threading
import time

FOUR_CHANNEL_PINS = {'stop': 26, 'start': 19, 'up': 13, 'down': 6}


class WinchRelays:
    pulse_ms = 300
    arm_gap = 0.20

    def jog_duration(self, action):
        return self.jog_pulses[action]

    def __init__(self, pins, *, output_factory=None):
        if output_factory is None:
            from gpiozero import OutputDevice
            output_factory = OutputDevice

        self.lock = threading.Lock()
        self.jog_pulses = {'up': self.pulse_ms, 'down': min(90, self.pulse_ms)}
        self.outputs = {}
        self.active = None
        self.pending = None
        self.motion_after = 0
        self.arm_token = None
        self.closed = False
        self.motion_elapsed = 0.0
        self.pulse_started = 0.0
        try:
            for action, pin in pins.items():
                self.outputs[action] = output_factory(pin, active_high=False, initial_value=False)
        except Exception:
            self.close()
            raise

    def _busy(self):
        return self.active is not None or self.pending is not None or time.monotonic() < self.motion_after

    def busy(self):
        with self.lock:
            return self._busy()

    def pulse(self, action, duration_ms=None):
        duration_ms = self.pulse_ms if duration_ms is None else duration_ms
        maximum_ms = 1500 if action in ('up', 'down') else self.pulse_ms
        if not 0 < duration_ms <= maximum_ms:
            raise ValueError('Invalid relay pulse duration')
        with self.lock:
            if self.closed:
                raise RuntimeError('Winch relay driver is closed')
            if action == 'stop':
                self.pending = None
                self._release()
            elif self._busy():
                return False
            self._pulse(action, duration_ms)
            return True

    def jog(self, action):
        """A single requested jog, queued behind a Start pulse on the handset."""
        with self.lock:
            if self.closed:
                raise RuntimeError('Winch relay driver is closed')
            if action not in ('up', 'down') or action not in self.outputs or 'start' not in self.outputs:
                raise ValueError('Unsupported armed jog')
            if self._busy():
                return False
            self._pulse('start', self.pulse_ms)
            self.pending = action
            return True

    def _pulse(self, action, duration_ms):
        token = object()
        self.active = (action, token)
        self.pulse_started = time.monotonic()
        try:
            self.outputs[action].on()
            self._timer(duration_ms / 1000, self._finish_pulse, token)
        except Exception:
            self._release()
            raise

    @staticmethod
    def _timer(seconds, callback, token):
        timer = threading.Timer(seconds, callback, (token,))
        timer.daemon = True
        timer.start()

    def _finish_pulse(self, token):
        with self.lock:
            if self.active is None or self.active[1] is not token:
                return
            self._release()
            if self.pending == 'stop':
                self.pending = None
                try:
                    self._pulse('stop', self.pulse_ms)
                except (OSError, RuntimeError):
                    logging.exception('Winch failed to send Stop after the Down jog')
            elif self.pending is not None:
                self._timer(self.arm_gap, self._finish_jog, token)

    def _finish_jog(self, token):
        with self.lock:
            # An intervening Stop invalidates this timer, including after a new Start.
            if self.pending is None or self.active is not None or self.closed or self.arm_token is not token:
                return
            action, self.pending = self.pending, None
            try:
                self._pulse(action, self.jog_duration(action))
                self.pending = 'stop'
            except (OSError, RuntimeError):
                logging.exception('Winch failed to send the queued %s jog', action)

    def _release(self):
        if self.active is not None:
            action, token = self.active
            self.motion_elapsed += (time.monotonic() - self.pulse_started) * {'down': 1, 'up': -1}.get(action, 0)
            self.outputs[action].off()
            self.active = None
            if action == 'start':
                self.arm_token = token
                self.motion_after = time.monotonic() + self.arm_gap

    def release(self):
        with self.lock:
            self.pending = None
            self._release()

    def action(self):
        with self.lock:
            return self.active[0] if self.active else None

    def motion_seconds(self):
        with self.lock:
            direction = {'down': 1, 'up': -1}.get(self.active[0], 0) if self.active else 0
            return self.motion_elapsed + direction * (time.monotonic() - self.pulse_started)

    def close(self):
        with self.lock:
            self.closed = True
            self.pending = None
            try:
                self._release()
            finally:
                for output in self.outputs.values():
                    output.close()
