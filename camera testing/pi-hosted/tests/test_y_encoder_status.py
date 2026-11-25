"""Y transport and signal diagnostics must not enable motion on invalid PWM."""
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
from outrigger import PulleyAxis, Reading


class YStatusTests(unittest.TestCase):
    def status(self, state):
        reading = None if state is None else Reading('Y', 1, 120, 500, state, 65536, time.monotonic())
        with tempfile.TemporaryDirectory() as directory:
            axis = PulleyAxis(SimpleNamespace(reading=lambda role: reading), None,
                              threading.RLock(), lambda: True, lambda: None,
                              Path(directory) / 'pulley.json')
            return axis.snapshot()

    def test_signal_fault_keeps_transport_diagnostics_and_disables_motion(self):
        status = self.status('signal')
        self.assertTrue(status['encoderUsbConnected'])
        self.assertFalse(status['encoderSignalValid'])
        self.assertFalse(status['connected'])
        self.assertFalse(status['ready'])
        self.assertIsNone(status['rawCounts'])
        self.assertEqual(status['encoderSequence'], 120)
        self.assertIn('no valid PWM signal', status['message'])

    def test_missing_board_is_distinct_from_signal_fault(self):
        status = self.status(None)
        self.assertFalse(status['encoderUsbConnected'])
        self.assertIsNone(status['encoderAgeMs'])
        self.assertIn('not reporting', status['message'])

    def test_valid_r4_protocol_is_connected_but_still_requires_zero(self):
        status = self.status('idle')
        self.assertTrue(status['connected'])
        self.assertTrue(status['encoderSignalValid'])
        self.assertFalse(status['ready'])
        self.assertEqual(status['rawCounts'], 500)
        self.assertIn('Set Y zero', status['message'])
