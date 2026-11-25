import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch
import zlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import capture


def frame(word=2048,baseline=2048.):
    raw=struct.pack('<1000H',*([word]*1000))
    meta=dict(schema=1,rate_hz=250000,sample_count=1000,trigger_index=100,
              baseline_counts=baseline,baseline_noise_counts=.6,crc32=zlib.crc32(raw),or_wired=True)
    return meta,raw


class ConversionTests(unittest.TestCase):
    def test_73_grams_at_one_g_is_point_71588545_newtons_of_inertial_force(self):
        meta,raw=frame(2048,2048-16.38)
        rows,summary=capture.convert(meta,raw)
        self.assertAlmostEqual(rows[0]['acceleration_g'],1)
        self.assertAlmostEqual(rows[0]['inertial_force_estimate_N'],.71588545)
        self.assertFalse(summary['force_is_contact_force'])
        self.assertEqual(summary['moving_mass_g'],73)

    def test_stationary_baseline_removes_bias_and_constant_gravity(self):
        meta,raw=frame(2064,2064)
        rows,_=capture.convert(meta,raw)
        self.assertEqual(rows[0]['acceleration_g'],0)
        self.assertEqual(rows[0]['inertial_force_estimate_N'],0)
        self.assertAlmostEqual(rows[0]['time_s'],-.0004)
        self.assertEqual(rows[100]['time_s'],0)

    def test_force_can_be_omitted_and_recomputed_with_a_different_total_mass(self):
        meta,raw=frame(2048,2048-16.38)
        rows,summary=capture.convert(meta,raw,mass_g=0)
        self.assertIsNone(rows[0]['inertial_force_estimate_N'])
        self.assertIsNone(summary['moving_mass_g'])
        rows,_=capture.convert(meta,raw,mass_g=100)
        self.assertAlmostEqual(rows[0]['inertial_force_estimate_N'],.980665)

    def test_adc_errors_are_not_converted_into_acceleration_or_force(self):
        meta,raw=frame()
        raw=struct.pack('<H',0x8fff)+raw[2:];meta['crc32']=zlib.crc32(raw)
        rows,summary=capture.convert(meta,raw)
        self.assertIsNone(rows[0]['acceleration_g'])
        self.assertIsNone(rows[0]['inertial_force_estimate_N'])
        self.assertEqual(summary['adc_error_samples'],1)

    def test_sensor_range_is_checked_before_adc_rails(self):
        meta,raw=frame(3700)
        rows,summary=capture.convert(meta,raw)
        self.assertTrue(rows[0]['sensor_range_warning'])
        self.assertFalse(rows[0]['adc_rail_warning'])
        self.assertTrue(any('100 g' in value for value in summary['software_warnings']))

    def test_overrange_and_unwired_or_are_distinct_warnings(self):
        meta,raw=frame();meta.update(overrange_seen=True,or_wired=False)
        _,summary=capture.convert(meta,raw)
        self.assertTrue(any('overload recovery' in value for value in summary['software_warnings']))
        self.assertTrue(any('not declared wired' in value for value in summary['software_warnings']))

    def test_corrupt_or_incomplete_transfers_are_rejected(self):
        meta,raw=frame()
        with self.assertRaisesRegex(ValueError,'checksum'):capture.validate_capture(meta,raw[:-1]+b'\x01')
        with self.assertRaisesRegex(ValueError,'byte count'):capture.validate_capture(meta,raw[:-2])

    def test_invalid_rate_length_mass_and_calibration_are_rejected(self):
        meta,raw=frame()
        for changed in [dict(sample_count=2**31),dict(rate_hz=float('nan')),dict(trigger_index=-1),dict(baseline_counts=5000)]:
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                capture.validate_capture({**meta,**changed},raw)
        for mass in [-1,float('nan')]:
            with self.assertRaises(ValueError):capture.convert(meta,raw,mass_g=mass)
        with self.assertRaises(ValueError):capture.convert(meta,raw,supply_v=5)

    def test_read_exact_handles_fragmented_binary_packets(self):
        class Fragmented:
            data=io.BytesIO(b'abcdef')
            def read(self,count):return self.data.read(min(count,2))
        self.assertEqual(capture.read_exact(Fragmented(),6),b'abcdef')

    def test_header_fragments_are_preserved_across_serial_timeouts(self):
        class Fragmented:
            parts=iter([b'AXL1 DA',b'',b'TA {"schema":1}',b'\n'])
            def readline(self,limit):return next(self.parts)
        self.assertEqual(capture.read_line(Fragmented(),capture.time.monotonic()+1),b'AXL1 DATA {"schema":1}\n')

    def test_demo_has_a_valid_checksum_and_is_explicitly_simulated(self):
        meta,raw=capture.demo(50000,5000,True)
        rows,summary=capture.convert(meta,raw)
        self.assertTrue(meta['simulated'])
        self.assertEqual(len(rows),50000)
        self.assertGreater(summary['observed_peak_abs_acceleration_g'],20)


class Link(io.BytesIO):
    def __init__(self,content):super().__init__(content);self.sent=[]
    def reset_input_buffer(self):pass
    def write(self,data):self.sent.append(data);return len(data)
    def __enter__(self):return self
    def __exit__(self,*args):pass


class ProtocolTests(unittest.TestCase):
    def test_only_identified_pico_receives_arm_and_binary_capture_round_trips(self):
        meta,raw=frame()
        link=Link(b'AXL1 HELLO version=1 board=pico2 adc=26\nAXL1 READY {"threshold_counts":33}\n'+
                  b'AXL1 DATA '+json.dumps(meta).encode()+b'\n'+raw+b'\nAXL1 END\n')
        with patch('serial.Serial',return_value=link),contextlib.redirect_stdout(io.StringIO()):
            received,payload=capture.record('mock-pico',33,1000,100,1)
        self.assertEqual(payload,raw)
        self.assertEqual(received,meta)
        self.assertEqual(link.sent,[b'HELLO\n',b'ARM 33 1000 100 1000\n'])

    def test_wrong_board_never_receives_arm(self):
        link=Link(b'SBH1 BRICKMAPPER_STEPPER Y 1 STEP=9 DIR=8\n')
        clock=iter(range(20))
        with patch('serial.Serial',return_value=link),patch.object(capture.time,'monotonic',side_effect=lambda:next(clock)):
            with self.assertRaisesRegex(RuntimeError,'No ARM request'):
                capture.record('wrong-board',33,1000,100,1)
        self.assertEqual(link.sent,[b'HELLO\n'])


if __name__=='__main__':unittest.main()
