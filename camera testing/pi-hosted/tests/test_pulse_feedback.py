"""Closed-loop pulse tests with a virtual clock, relay driver and delayed winch."""
from dataclasses import replace
import math
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from outrigger import PulseResponse, PulseSettling, PulleyAxis, Reading, WinchMove, WINCH_PROFILES
from winch import WinchRelays, FOUR_CHANNEL_PINS


def simulate(direction='down', *, speed=3, latency=.1, coast=.05, ignore_first=False,
             stalled=False, noise=False, ignores_stop=False, settle=1, target=6, expires=100):
    now = position = 0.0
    motor = 0
    starts_at = ends_at = math.inf
    timers, pulses, overlap, disarms = [], [], [], []
    presses = 0

    def advance(until):
        nonlocal now, position
        position += motor * speed * max(0, min(until, ends_at) - max(now, starts_at))
        now = until

    class Clock:
        @staticmethod
        def monotonic():
            return now

        @staticmethod
        def sleep(seconds):
            until = now + seconds
            while timers and min(item[0] for item in timers) <= until:
                event = min(timers, key=lambda item: item[0]); timers.remove(event)
                advance(event[0]); event[1](event[2])
            advance(until)

    class Output:
        def __init__(self, pin, **_):
            self.direction = {13: -1, 6: 1}.get(pin, 0)

        def on(self):
            nonlocal motor, starts_at, ends_at, presses
            if not self.direction:
                return
            if now < ends_at and now > starts_at:
                overlap.append(now)
            presses += 1
            if stalled or (ignore_first and presses == 1):
                return
            motor = self.direction
            starts_at, ends_at = now + latency, math.inf

        def off(self):
            nonlocal ends_at
            if self.direction and not ignores_stop:
                ends_at = now + coast if now >= starts_at else now

        close = off

    class Driver(WinchRelays):
        def pulse(self, action, duration_ms=None):
            if action in ('up', 'down'):
                pulses.append((action, duration_ms, now))
            return super().pulse(action, duration_ms)

    def sample(_):
        counts = round(position * 4096 / math.tau)
        if noise:
            counts += 2 if round(now * 50) % 2 else -2
        return Reading('Y', 1, round(now * 100), counts, 'idle', 7, now)

    with tempfile.TemporaryDirectory() as folder, patch('outrigger.time', Clock), patch('winch.time', Clock):
        driver = Driver(FOUR_CHANNEL_PINS, output_factory=Output)
        driver._timer = lambda seconds, callback, token: timers.append((now + seconds, callback, token))
        def arm():
            return driver.pulse('start')
        def disarm():
            disarms.append(now); driver.pulse('stop')
        axis = PulleyAxis(SimpleNamespace(reading=sample), driver, threading.RLock(), arm, disarm,
                          Path(folder) / 'pulley.json')
        axis.profiles = {key: replace(value, probe_ms=310, minimum_ms=210, maximum_ms=530,
                                     settle_seconds=settle) for key, value in axis.profiles.items()}
        axis.origin = (1, 7, 0)
        sign = 1 if direction == 'down' else -1
        distance = round(target * 4096 / axis.travel)
        job = WinchMove('simulation', sign * distance, sign, expires)
        axis.active = job
        axis.pulse_control = {'pulses': 0}
        arm()
        axis._move(job, 0, distance, True)
        result = SimpleNamespace(outcome=axis.outcome, message=axis.message, warning=axis.warning,
                                 position=position, pulses=pulses, overlap=overlap,
                                 control=axis.pulse_control, disarms=disarms, elapsed=now)
        driver.close()
        return result


class FeedbackTests(unittest.TestCase):
    def test_no_response_repeats_the_same_pulse_even_close_to_target(self):
        for profile in WINCH_PROFILES.values():
            response = PulseResponse(profile)
            first = response.duration(6)
            response.observe(0, first)
            self.assertEqual(response.duration(.51), first)
            self.assertEqual(response.reason, 'same-pulse retry')

    def test_real_relay_timing_varies_pulses_and_reaches_half_inch_window(self):
        for direction in ('up', 'down'):
            result = simulate(direction)
            self.assertEqual(result.outcome, 'done', result.message)
            self.assertLessEqual(abs(abs(result.position) - 6), .5 + math.tau / 4096)
            durations = [pulse[1] for pulse in result.pulses]
            self.assertEqual(durations[0], 310)
            self.assertGreater(len(set(durations)), 1)
            self.assertTrue(all(210 <= pulse <= 530 for pulse in durations))
            self.assertGreater(result.control['actualPulseMs'], 0)
            self.assertFalse(result.disarms)

    def test_one_ignored_press_can_recover_without_shortening_or_increasing_retry(self):
        result = simulate(ignore_first=True)
        self.assertEqual(result.outcome, 'done', result.message)
        self.assertEqual([pulse[1] for pulse in result.pulses[:2]], [310, 310])

    def test_encoder_noise_does_not_count_as_progress_or_expand_pulses(self):
        result = simulate(stalled=True, noise=True)
        self.assertEqual(result.outcome, 'error')
        self.assertIn('without pulley progress', result.message)
        self.assertEqual([pulse[1] for pulse in result.pulses], [310, 310])
        self.assertIsNone(result.control['responseInchesPerSecond'])
        self.assertEqual(len(result.disarms), 1)

    def test_late_coasting_finishes_before_the_next_direction_pulse(self):
        result = simulate(speed=.8, coast=.9, settle=.05, target=3)
        self.assertEqual(result.outcome, 'done', result.message)
        self.assertGreater(len(result.pulses), 1)
        self.assertFalse(result.overlap, 'A correction started while the previous pulse was still coasting')

    def test_continuing_motion_after_stop_does_not_trigger_another_pulse(self):
        result = simulate(speed=.8, ignores_stop=True, settle=.05, target=12)
        self.assertEqual(result.outcome, 'error')
        self.assertIn('did not settle', result.message)
        self.assertEqual(len(result.pulses), 1)
        self.assertEqual(len(result.disarms), 1)

    def test_expired_lease_still_releases_and_disarms(self):
        result = simulate(expires=.7)
        self.assertEqual(result.outcome, 'error')
        self.assertIn('timed out', result.message)
        self.assertEqual(len(result.disarms), 1)

    def test_settling_requires_a_fresh_quiet_reading_and_the_whole_configured_wait(self):
        settling = PulseSettling(0, 10, 1, 13)
        self.assertFalse(settling.ready(0, 10.99))
        self.assertFalse(settling.ready(20, 11))
        self.assertFalse(settling.ready(20, 11.14))
        self.assertTrue(settling.ready(20, 11.16))


if __name__ == '__main__':
    unittest.main()
