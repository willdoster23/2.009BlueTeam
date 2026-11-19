"""Run with python3 -m unittest discover -s tests. No GPIO hardware is used."""
from pathlib import Path
import json
import math
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from outrigger import PulseResponse, WINCH_PROFILES, PulleyAxis, Reading, WinchMove
import winch


class ResponseTests(unittest.TestCase):
    def test_probe_and_direction_specific_limits(self):
        up = PulseResponse(WINCH_PROFILES['up'])
        down = PulseResponse(WINCH_PROFILES['down'])
        self.assertEqual(up.duration(6), 400)
        self.assertEqual(down.duration(6), 250)
        for response in (up, down):
            for _ in range(10):
                response.observe(.3, 100)
                duration = response.duration(6)
                self.assertLessEqual(duration, response.profile.maximum_ms)
                self.assertGreaterEqual(duration, response.profile.minimum_ms)
        self.assertGreater(up.last_ms, down.last_ms)

    def test_no_progress_cannot_increase_pulse(self):
        for profile in WINCH_PROFILES.values():
            response = PulseResponse(profile)
            previous = response.duration(6)
            response.observe(0, 100)
            self.assertLessEqual(response.duration(6), previous)

    def test_fast_response_is_used_immediately(self):
        response = PulseResponse(WINCH_PROFILES['down'])
        response.duration(2)
        response.observe(.1, 100)
        previous = response.duration(2)
        response.observe(2, 100)
        self.assertEqual(response.rate, .02)
        self.assertLess(response.duration(.5), previous)

    def test_slow_response_does_not_provoke_a_large_jump(self):
        response = PulseResponse(WINCH_PROFILES['up'])
        response.duration(6)
        response.observe(.5, 100)
        previous = response.duration(6)
        response.observe(.01, 100)
        self.assertGreaterEqual(response.rate, .005 * .85)
        self.assertLessEqual(response.duration(6), round(previous * response.profile.growth))


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.timers = []
        self.levels = {}
        self.edges = []
        owner = self

        class Clock:
            @staticmethod
            def monotonic():
                return owner.now

        class Output:
            def __init__(self, pin, **_):
                self.pin = pin
                owner.levels[pin] = False

            def on(self):
                owner.assertFalse(any(owner.levels.values()), 'Relay contacts overlap')
                owner.levels[self.pin] = True
                owner.edges.append((self.pin, True, owner.now))

            def off(self):
                if owner.levels[self.pin]:
                    owner.edges.append((self.pin, False, owner.now))
                owner.levels[self.pin] = False

            close = off

        self.clock_patch = patch.object(winch, 'time', Clock)
        self.clock_patch.start()
        self.relays = winch.WinchRelays(winch.FOUR_CHANNEL_PINS, output_factory=Output)
        self.relays._timer = lambda seconds, callback, token: self.timers.append((self.now + seconds, callback, token))

    def tearDown(self):
        self.relays.close()
        self.clock_patch.stop()

    def advance(self, until):
        while self.timers and min(item[0] for item in self.timers) <= until:
            item = min(self.timers, key=lambda item: item[0])
            self.timers.remove(item)
            self.now, callback, token = item
            callback(token)
        self.now = until

    def active_pins(self):
        return [pin for pin, on, _ in self.edges if on]

    def test_down_jog_sends_short_pulse_then_explicit_stop(self):
        self.assertTrue(self.relays.jog('down'))
        self.advance(2)
        self.assertEqual(self.active_pins(), [19, 6, 26])
        down = [t for pin, _, t in self.edges if pin == 6]
        self.assertAlmostEqual(down[1] - down[0], .09)
        self.assertFalse(self.relays.busy())

    def test_stop_cancels_queued_jog(self):
        self.relays.jog('down')
        self.advance(.35)
        self.relays.pulse('stop')
        self.advance(2)
        self.assertEqual(self.active_pins(), [19, 26])

    def test_stop_during_down_does_not_send_duplicate_stop(self):
        self.relays.jog('down')
        self.advance(.53)
        self.relays.pulse('stop')
        self.advance(2)
        self.assertEqual(self.active_pins(), [19, 6, 26])

    def test_longer_up_pulse_is_bounded_and_cancelable(self):
        self.assertTrue(self.relays.pulse('up', 600))
        self.advance(.1)
        self.relays.release()
        self.advance(1)
        self.assertFalse(self.relays.busy())
        self.assertAlmostEqual(self.relays.motion_seconds(), -.1)
        with self.assertRaises(ValueError):
            self.relays.pulse('up', 1501)
        with self.assertRaises(ValueError):
            self.relays.pulse('down', 1501)


class DirectionTests(unittest.TestCase):
    def run_move(self, offsets, direction, sign, distance):
        now = 0.0
        pending = iter(offsets)
        offset = 0
        disarms = []

        class Clock:
            @staticmethod
            def monotonic():
                return now

            @staticmethod
            def sleep(seconds):
                nonlocal now
                now += seconds

        class Driver:
            active = None
            until = 0.0
            releases = 0

            def busy(self):
                return now < self.until

            def action(self):
                return self.active if self.busy() else None

            def pulse(self, action, duration_ms=300):
                self.active, self.until = action, now + duration_ms / 1000
                return True

            def release(self):
                self.active, self.until = None, now
                self.releases += 1

            def motion_seconds(self):
                return 0.0

        def sample(_):
            nonlocal offset
            offset = next(pending, offset)
            return Reading('Y', 1, 1, (8000 + offset * direction) * sign,
                           'idle', 7, now)

        with tempfile.TemporaryDirectory() as directory, patch('outrigger.time', Clock):
            driver = Driver()
            axis = PulleyAxis(SimpleNamespace(reading=sample), driver, threading.RLock(),
                              lambda: True, lambda: disarms.append(True),
                              Path(directory) / 'pulley.json')
            axis.sign = sign
            axis.origin = (1, 7, 0)
            job = WinchMove('direction-test', 8000 + distance * direction,
                            direction, 10.0)
            axis.active = job
            axis.pulse_control = {'pulses': 0}
            axis._move(job, 8000, distance, True)
            return axis, driver, disarms

    def test_reverse_readings_below_quarter_turn_can_recover_and_finish(self):
        for direction in (-1, 1):
            for sign in (-1, 1):
                for distance in (65, 4096):
                    with self.subTest(direction=direction, sign=sign, distance=distance):
                        axis, _, disarms = self.run_move(
                            [0, -1, -64, -1023, 0, distance], direction, sign, distance)
                        self.assertEqual(axis.outcome, 'done')
                        self.assertEqual(axis.message, 'Y target reached.')
                        self.assertFalse(disarms)

    def test_quarter_turn_or_more_in_reverse_stops_and_disarms(self):
        for direction in (-1, 1):
            for sign in (-1, 1):
                for distance in (65, 4096):
                    for reverse in (1024, 1025, 4096):
                        with self.subTest(direction=direction, sign=sign,
                                          distance=distance, reverse=reverse):
                            axis, driver, disarms = self.run_move(
                                [0, -300, -600, -900, -1023, -reverse],
                                direction, sign, distance)
                            self.assertIsNone(axis.active)
                            self.assertEqual(axis.outcome, 'error')
                            self.assertEqual(axis.message, 'Y encoder moved opposite the requested motion.')
                            self.assertEqual(disarms, [True])
                            self.assertEqual(driver.releases, 1)


class TuningTests(unittest.TestCase):
    def test_tuning_persists_without_moving_or_changing_zero(self):
        boards = SimpleNamespace(reading=lambda _: Reading('Y', 1, 1, 10, 'idle', 7, time.monotonic()))
        driver = SimpleNamespace()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory, 'pulley.json')
            axis = PulleyAxis(boards, driver, threading.RLock(), lambda: self.fail('No arming'),
                              lambda: self.fail('No stopping'), config)
            self.assertEqual(axis.command({'id':'zero', 'action':'zero'})[0], 200)
            settings = axis.tuning()
            settings['adaptive'] = False
            settings['up'].update(pulseMs=800, maxPulseMs=1000, settleMs=400)
            settings['down'].update(pulseMs=500, minPulseMs=250, maxPulseMs=500, settleMs=900)
            status, result = axis.command({'id':'tune', 'action':'tune', 'winchTuning':settings})
            self.assertEqual(status, 200)
            self.assertTrue(result['zeroed'])
            self.assertEqual(result['counts'], 0)
            self.assertEqual(driver.jog_pulses, {'up':800, 'down':500})
            restored = PulleyAxis(boards, driver, threading.RLock(), lambda: False, lambda: None, config)
            self.assertEqual(restored.tuning(), settings)
            self.assertTrue(restored.snapshot()['zeroed'])
            restored.command({'id':'calibrate', 'action':'calibrate', 'inchesPerTurn':math.tau})
            self.assertEqual(json.loads(config.read_text())['winchTuning'], settings)
            invalid = {**settings, 'down':{**settings['down'], 'pulseMs':2000}}
            self.assertEqual(restored.command({'id':'bad', 'action':'tune', 'winchTuning':invalid})[0], 400)
            self.assertEqual(restored.tuning(), settings)


if __name__ == '__main__':
    unittest.main()
