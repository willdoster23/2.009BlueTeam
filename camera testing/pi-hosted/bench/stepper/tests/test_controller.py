import importlib.util
import io
import json
import math
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location('bench_controller', Path(__file__).resolve().parents[1] / 'controller.py')
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


class PlannerTests(unittest.TestCase):
    def data(self, **changes):
        return dict(ppr=1600, diameter=4, distance=.125, unit='turns', rpm=3, accel=10, direction=1, **changes)

    def test_rotation_and_inches_are_consistent(self):
        data=self.data()
        plan=bench.plan_move(data)
        self.assertEqual(plan['steps'],200)
        self.assertEqual(plan['rate'],80000)
        data.update(unit='inches',distance=math.pi*4/8,direction=-1)
        self.assertEqual(bench.plan_move(data)['steps'],-200)

    def test_invalid_and_unrepresentable_commands(self):
        for key,value in [('rpm',float('nan')),('distance',float('inf')),('direction',True),
                          ('ppr',0),('rpm',0),('unit','meters'),('accel',float('inf'))]:
            data=self.data();data[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):bench.plan_move(data)
        for changes in (dict(distance=1e308), dict(distance=(1 << 31) / 1600), dict(ppr=51200,rpm=60)):
            data=self.data();data.update(changes)
            with self.assertRaises(ValueError):bench.plan_move(data)

    def test_long_moves_have_no_bench_distance_or_duration_cutoff(self):
        for changes in (dict(distance=1300), dict(distance=1,rpm=.1),
                        dict(distance=1200,unit='inches')):
            data=self.data();data.update(changes)
            plan=bench.plan_move(data)
            self.assertGreater(plan['steps'], 0)
            self.assertEqual(plan['steps'], round(plan['turns'] * data['ppr']))
        data=self.data();data['distance']=bench.MAX_MOVE_STEPS/1600
        self.assertEqual(bench.plan_move(data)['steps'],bench.MAX_MOVE_STEPS)

    def test_parser_distinguishes_real_measurements(self):
        state=bench.parse_status('SB1 23 1 1 -20 120 80000 moving 1 0 0 9')
        self.assertTrue(state['moving'])
        self.assertEqual(state['steps'],-20)
        self.assertNotIn('encoder',state)
        self.assertIsNone(bench.parse_status('BM1 Y 1 0 idle 65536'))
        self.assertIsNone(bench.parse_status('SB1 23 1 1 0 0 x idle 0 0 0 1'))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        # The thread and serial transport are mocked; these tests cannot open hardware.
        with patch.object(threading.Thread,'start'):
            self.c=bench.BenchController()
        self.c.link=object()
        self.c.last_rx=time.monotonic()
        self.c.lease=time.monotonic()
        self.c.state=dict(sequence=1,armed=True,moving=False,steps=0,moveId=0)

    def test_external_encoder_zero_epoch_and_signal_loss(self):
        self.c.encoder_link=object()
        self.c._receive_encoder('BM1 Y 1 100 idle 65536')
        self.c.zero_encoder(dict(travelPerRev=12,sign=-1))
        self.c._receive_encoder('BM1 Y 2 4196 idle 65536')
        state=self.c._encoder_snapshot()
        self.assertEqual(state['turns'],-1)
        self.assertEqual(state['inches'],-12)
        self.c._receive_encoder('BM1 Y 3 0 idle 131072')
        self.assertFalse(self.c._encoder_snapshot()['zeroed'])
        self.c.encoder_rx-=1
        self.assertFalse(self.c._encoder_snapshot()['valid'])

    def test_monitoring_requires_actual_encoder(self):
        data=PlannerTests().data();data['requireEncoder']=True
        with self.assertRaisesRegex(ValueError,'external encoder'):
            self.c.move(data)

    def test_stop_invalidates_waiting_commands_and_does_not_disable_driver(self):
        generation=self.c.generation
        sent=[]
        self.c.write=sent.append
        self.c.stop()
        self.assertEqual(sent,['STOP'])
        self.assertFalse(self.c.state['armed'])
        with self.assertRaises(ValueError):self.c._ready(generation)

    def test_wrong_board_is_refused_before_opening_serial(self):
        device=dict(port='fake',nano=True,serial='D92DB2CC515452344D202020FF094441',role='X')
        with patch.object(bench,'ports',return_value=[device]),patch.object(bench.serial,'Serial') as transport:
            with self.assertRaisesRegex(ValueError,'existing X/Y'):self.c.connect('fake')
            transport.assert_not_called()

    def test_move_is_finite_and_calibration_cannot_silently_change(self):
        requests=[]
        self.c.request=lambda *args,**kwargs:requests.append(args)
        plan=self.c.move(PlannerTests().data())
        self.assertEqual(plan['steps'],200)
        self.assertEqual(requests,[('MOVE 1 200 80000 266667','MOVE',1)])
        self.c.state['steps']=200
        changed=PlannerTests().data();changed['diameter']=5
        with self.assertRaisesRegex(ValueError,'Zero'):self.c.move(changed)

    def test_large_moves_reach_firmware_but_position_overflow_is_rejected(self):
        requests=[]
        self.c.request=lambda *args,**kwargs:requests.append(args)
        data=PlannerTests().data();data['distance']=1300
        self.assertEqual(self.c.move(data)['steps'],2_080_000)
        self.assertTrue(requests[0][0].startswith('MOVE 1 2080000 '))
        self.c.state['steps']=bench.MAX_MOVE_STEPS
        with self.assertRaisesRegex(ValueError,'counter range'):
            self.c.move(PlannerTests().data())
        self.assertEqual(len(requests),1)

    def test_encoder_reports_from_x_are_ignored(self):
        self.c._receive_encoder('BM1 X 1 100 idle 2')
        self.assertIsNone(self.c.encoder_state)

    def test_stop_cancels_a_command_at_the_final_serial_write(self):
        original=self.c.generation
        self.c.write=lambda *_:None
        self.c.stop()
        with self.assertRaisesRegex(ValueError,'Stop interrupted'):
            self.c.request('ARM','ARM',generation=original)

    def test_monitored_move_rejects_an_old_encoder_zero(self):
        self.c.encoder_link=object()
        self.c._receive_encoder('BM1 Y 1 0 idle 65536')
        self.c.zero_encoder(dict(travelPerRev=12,sign=1))
        self.c._receive_encoder('BM1 Y 2 0 idle 131072')
        data=PlannerTests().data();data['requireEncoder']=True
        with self.assertRaisesRegex(ValueError,'Zero the external encoder'):
            self.c.move(data)

    def test_pre_move_idle_report_does_not_remove_encoder_monitor(self):
        self.c.encoder_watch=dict(epoch=65536,moveId=2)
        self.c._receive('SB1 1 1 0 0 0 0 idle 1 1 0 1')
        self.assertIsNotNone(self.c.encoder_watch)
        self.c._receive('SB1 2 1 1 5 10 10000 moving 1 1 0 2')
        self.assertIsNotNone(self.c.encoder_watch)
        self.c._receive('SB1 3 1 0 15 0 0 done 1 1 0 2')
        self.assertIsNone(self.c.encoder_watch)


class PageConnectionTests(unittest.TestCase):
    def request(self, token, host='127.0.0.1:8766'):
        # Parse an actual HTTP request in memory; no USB or network is opened.
        class Connection:
            def __init__(self, request):
                self.input = io.BytesIO(request)
                self.output = bytearray()

            def makefile(self, *_):
                return self.input

            def sendall(self, data):
                self.output.extend(data)

        self.controller = Mock()
        self.controller.arm.return_value = None
        headers = f'POST /api/arm HTTP/1.0\r\nHost: {host}\r\nContent-Length: 2\r\n'
        if token is not None:
            headers += f'X-Bench-Token: {token}\r\n'
        connection = Connection((headers + '\r\n{}').encode())
        handler = bench.make_handler(self.controller, 'current-page-key', '127.0.0.1:8766')
        handler(connection, ('127.0.0.1', 12345), object())
        head, body = bytes(connection.output).split(b'\r\n\r\n', 1)
        return int(head.split()[1]), json.loads(body)

    def test_expired_or_missing_page_key_explains_recovery_and_cannot_arm(self):
        for token in ('previous-server-key', None):
            with self.subTest(token=token):
                status, body = self.request(token)
                self.assertEqual(status, 403)
                self.assertEqual(body['code'], 'page_expired')
                self.assertIn('Refresh', body['error'])
                self.controller.arm.assert_not_called()

    def test_fresh_page_can_arm_without_issuing_a_move(self):
        status, body = self.request('current-page-key')
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        self.controller.arm.assert_called_once_with()
        self.controller.move.assert_not_called()

    def test_nonlocal_host_is_still_rejected_with_a_current_key(self):
        status, body = self.request('current-page-key', host='example.com:8766')
        self.assertEqual(status, 403)
        self.assertEqual(body['error'], 'Local controller only')
        self.controller.arm.assert_not_called()


if __name__=='__main__':unittest.main()
