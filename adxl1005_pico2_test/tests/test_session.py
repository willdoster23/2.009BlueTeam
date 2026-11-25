import csv
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from test_capture import frame
import capture
import gui_support
import session


CONFIG=gui_support.settings(dict(mass='73',threshold='2',window='200',pre='20',timeout='30',supply='3.3',reference='3.3',cooldown='0'))


def force_frame(force):
    counts_per_g=16.38
    return frame(2048,baseline=2048-force*counts_per_g/(.073*capture.G0))


class SessionTests(unittest.TestCase):
    def test_three_newton_boundary_includes_exactly_three_and_rejects_missing_force(self):
        for peak,expected in [(2.999,False),(3.,True),(3.001,True),(None,False),(float('nan'),False)]:
            with self.subTest(peak=peak):
                self.assertEqual(session.counts_as_tap({'observed_peak_abs_inertial_force_estimate_N':peak}),expected)

    def test_weak_motion_is_excluded_and_positive_and_negative_taps_count(self):
        weak=force_frame(2.99);positive=force_frame(3.01);negative=force_frame(-3.01)
        seen=[]
        with patch.object(capture,'record',side_effect=[weak,positive,negative,capture.CaptureCancelled('Stopped')]):
            result=session.record_session('mock',CONFIG,30,True,threading.Event(),lambda *_:None,seen.append)
        self.assertEqual(result['ignored_below_minimum_count'],1)
        self.assertEqual(result['minimum_tap_force_N'],3)
        self.assertEqual([tap['number'] for tap in seen],[1,2])
        self.assertEqual([(tap['meta'],tap['raw']) for tap in result['taps']],[positive,negative])
        self.assertIsNone(result['issue'])

    def test_weak_motion_does_not_start_settling_pause(self):
        cancel=threading.Event();seen=[]
        with patch.object(capture,'record',side_effect=[force_frame(2.99),capture.CaptureCancelled('Stopped')]), \
             patch.object(cancel,'wait',side_effect=AssertionError('Weak motion must re-arm without a settling pause.')):
            result=session.record_session('mock',{**CONFIG,'cooldown_s':2},30,True,cancel,lambda *_:None,seen.append)
        self.assertEqual(result['ignored_below_minimum_count'],1)
        self.assertEqual(seen,[])
        self.assertIsNone(result['issue'])

    def test_force_threshold_uses_configured_moving_mass(self):
        for mass,accepted in [(73,1),(36.5,0)]:
            with self.subTest(mass=mass),patch.object(capture,'record',side_effect=[force_frame(3.01),capture.CaptureCancelled('Stopped')]):
                result=session.record_session('mock',{**CONFIG,'mass_g':mass},30,True,threading.Event(),lambda *_:None,lambda *_:None)
                self.assertEqual(len(result['taps']),accepted)
                self.assertEqual(result['ignored_below_minimum_count'],1-accepted)

    def test_export_excludes_weak_motion_and_preserves_accepted_raw_and_csv(self):
        weak=force_frame(2.99);strong=force_frame(3.01)
        result=dict(taps=[dict(number=n,meta=meta,raw=raw,received_offset_s=n) for n,(meta,raw) in enumerate([weak,strong],1)],
                    requested_period_s=30,elapsed_s=3,stopped=True,issue=None)
        with tempfile.TemporaryDirectory() as temporary:
            folder,previews=session.save_session(result,Path(temporary)/'session',CONFIG)
            with (folder/'all_taps.csv').open() as stream:rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),1000)
            self.assertEqual({row['tap_number'] for row in rows},{'1'})
            self.assertAlmostEqual(float(rows[0]['inertial_force_estimate_N']),3.01)
            self.assertEqual((folder/'tap-001/raw_adc_u16le.bin').read_bytes(),strong[1])
            self.assertFalse((folder/'tap-002').exists())
            info=json.loads((folder/'session.json').read_text())
            self.assertEqual(info['tap_count'],1)
            self.assertEqual(info['ignored_below_minimum_count'],1)
            self.assertEqual(info['minimum_tap_force_N'],3)
            self.assertEqual(len(previews),1)

    def test_settling_pause_delays_rearm_and_respects_session_deadline(self):
        class Clock:
            now=100.
            def monotonic(self):return self.now
        clock=Clock();arms=[];pauses=[];phases=[]
        class Cancel:
            def is_set(self):return False
            def wait(self,seconds):
                pauses.append(seconds);clock.now+=seconds;return False
        def record(*_args,**_kwargs):
            arms.append(clock.now);return frame(2160)
        with patch.object(session.time,'monotonic',clock.monotonic),patch.object(capture,'record',side_effect=record):
            result=session.record_session('mock',{**CONFIG,'cooldown_s':2},5,False,Cancel(),lambda phase,_:phases.append(phase),lambda *_:None)
        self.assertEqual(arms,[100,102,104])
        self.assertEqual(pauses,[2,2,1])
        self.assertEqual(phases,['cooldown']*3)
        self.assertEqual(result['elapsed_s'],5)
        self.assertEqual(len(result['taps']),3)
        self.assertIsNone(result['issue'])

    def test_stopping_during_settling_preserves_capture_without_arming_again(self):
        cancel=threading.Event()
        def progress(phase,_text):
            if phase=='cooldown':cancel.set()
        with patch.object(capture,'record',return_value=frame(2160)) as record:
            result=session.record_session('mock',{**CONFIG,'cooldown_s':2},30,False,cancel,progress,lambda *_:None)
        self.assertEqual(record.call_count,1)
        self.assertEqual(len(result['taps']),1)
        self.assertTrue(result['stopped'])
        self.assertIsNone(result['issue'])

    def test_stop_preserves_completed_taps_and_only_cancels_pending_capture(self):
        cancel=threading.Event();seen=[]
        def record(*_args,**_kwargs):
            if len(seen)==2:
                cancel.set();raise capture.CaptureCancelled('Stopped')
            return frame(2160)
        with patch.object(capture,'record',side_effect=record):
            result=session.record_session('mock',CONFIG,30,True,cancel,lambda *_:None,seen.append)
        self.assertEqual(len(result['taps']),2)
        self.assertTrue(result['stopped'])
        self.assertIsNone(result['issue'])

    def test_normal_wait_timeout_ends_a_recording_period_without_a_false_error(self):
        class Clock:
            now=100.
            def monotonic(self):return self.now
        clock=Clock()
        def record(*_args,**_kwargs):
            clock.now+=30
            raise RuntimeError('AXL1 ERROR trigger_timeout')
        with patch.object(session.time,'monotonic',clock.monotonic),patch.object(capture,'record',side_effect=record):
            result=session.record_session('mock',CONFIG,30,True,threading.Event(),lambda *_:None,lambda *_:None)
        self.assertEqual(result['taps'],[])
        self.assertEqual(result['elapsed_s'],30)
        self.assertIsNone(result['issue'])

    def test_serial_failure_keeps_prior_tap_available_for_saving(self):
        meta,raw=frame(2160)
        with patch.object(capture,'record',side_effect=[(meta,raw),OSError('Pico disconnected')]):
            result=session.record_session('mock',CONFIG,30,False,threading.Event(),lambda *_:None,lambda *_:None)
        self.assertEqual(len(result['taps']),1)
        self.assertIn('disconnected',result['issue'])

    def test_session_csv_preserves_every_sample_and_separate_tap_time_axes(self):
        meta,raw=frame(2160)
        result=dict(taps=[dict(number=n,meta=meta,raw=raw,received_offset_s=n) for n in (1,2)],
                    requested_period_s=30,elapsed_s=3,stopped=True,issue=None)
        with tempfile.TemporaryDirectory() as temporary:
            folder,previews=session.save_session(result,Path(temporary)/'session',CONFIG)
            with (folder/'all_taps.csv').open() as file:rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),2000)
            self.assertEqual(rows[0]['tap_number'],'1')
            self.assertEqual(rows[1000]['tap_number'],'2')
            self.assertEqual(rows[0]['time_s'],rows[1000]['time_s'])
            info=json.loads((folder/'session.json').read_text())
            self.assertFalse(info['quiet_time_recorded'])
            self.assertFalse(info['force_is_contact_force'])
            self.assertEqual(info['cooldown_s'],0)
            self.assertIn('not a verified physical tap count',info['count_note'])
            self.assertTrue((folder/'session_graphs.png').exists())
            self.assertEqual(len(previews),2)

    def test_overview_envelope_keeps_short_positive_and_negative_impact_peaks(self):
        rows=[dict(time_s=i/250000,acceleration_g=0.) for i in range(50000)]
        rows[12345]['acceleration_g']=90
        rows[12346]['acceleration_g']=-80
        compact=session.preview_rows(rows)
        self.assertLess(len(compact),3000)
        self.assertEqual(max(row['acceleration_g'] for row in compact),90)
        self.assertEqual(min(row['acceleration_g'] for row in compact),-80)


if __name__=='__main__':unittest.main()
