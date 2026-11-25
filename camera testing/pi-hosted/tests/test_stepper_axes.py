"""No GPIO/USB: stepper routing, measured Y, estimated X, asynchronous feedback."""
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
import stepper
from outrigger import SerialBoards, Port, Reading


class Clock:
    now=100.
    callback=None
    def monotonic(self):return self.now
    def sleep(self,seconds):
        self.now+=seconds
        if self.callback:self.callback(self.now)


class Boards:
    def __init__(self,clock):
        self.clock=clock
        self.encoder=Reading('Y',1,1,0,'idle',65536,clock.now)
        self.sent=[]
        self.motor={role:dict(sequence=1,generation=1,steps=0,remaining=0,rate=0,moving=False,
                              armed=False,reason='idle',alarmRaw=1,placeRaw=1,alarmMode=0,moveId=0,
                              received=clock.now) for role in ('X','Y')}
    def reading(self,role):return self.encoder if role=='Y' else None
    def motor_reading(self,role='Y'):return dict(self.motor[role]) if self.motor.get(role) else None
    def start_motor(self,generation,move_id,steps,rate,accel,role='Y'):
        self.sent.append((role,steps,rate,accel))
        self.motor[role].update(moveId=move_id,moving=True,armed=True,remaining=abs(steps),rate=rate/1000,reason='moving')
        return True
    def stop_motor(self,role='Y'):
        self.sent.append((role,'STOP'))
        self.motor[role].update(moving=False,armed=False,remaining=0,rate=0,reason='stopped')
        return True
    def renew_motor(self,role='Y'):return True
    def motor_alarm(self,mode,role='Y'):
        self.motor[role]['alarmMode']=mode
        return True
    def pulley(self,inches,travel=6.283185307179586,epoch=65536):
        self.encoder=Reading('Y',1,int(self.clock.now*100),round(inches/travel*4096),'idle',epoch,self.clock.now)


class StepperTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.clock=Clock();self.clock.now=100.;self.clock.callback=None
        self.patch=patch.object(stepper,'time',self.clock);self.patch.start()
        self.boards=Boards(self.clock)
        self.y=stepper.StepperAxis(self.boards,threading.RLock(),'Y',Path(self.directory.name)/'pulley.json')
        self.x=stepper.StepperAxis(self.boards,threading.RLock(),'X',Path(self.directory.name)/'x-reference.json')
        for axis in (self.x,self.y):
            code,_=axis.command(dict(id='settings',action='stepper-settings',settings={**axis.profile,'calibrated':True}))
            self.assertEqual(code,200)
            self.assertEqual(axis.command(dict(id='zero',action='zero'))[0],200)
    def tearDown(self):self.patch.stop();self.directory.cleanup()
    def arm(self,axis):
        self.assertEqual(axis.command(dict(id='arm-'+str(self.clock.now)+'-'+str(len(axis.handled)),action='arm'))[0],200)
    def move(self,axis,**payload):
        with patch.object(threading.Thread,'start'):
            return axis.command(dict(id='move',mode='live',armId=axis.arm_id,**payload))

    def test_basic_test_uses_fixed_speed_without_encoder_zero_or_calibration(self):
        self.y.profile['calibrated']=False
        self.y.origin=None
        self.boards.encoder=None
        self.boards.motor['Y']['fixedSpeedSupported']=True
        self.arm(self.y)
        code,_=self.move(self.y,action='basic-test',direction=1)
        self.assertEqual(code,202)
        self.assertEqual(self.boards.sent[-1],('Y',80,80000,0))
        self.assertIsNone(self.y.active.target)

    def test_old_firmware_cannot_silently_run_basic_test_with_a_ramp(self):
        self.arm(self.x)
        self.assertEqual(self.move(self.x,action='basic-test',direction=1)[0],400)
        self.assertEqual(self.boards.sent,[])

    def test_y_distance_and_speed_are_from_pulley_not_motor_commands(self):
        self.boards.motor['Y']['steps']=1_000_000
        self.boards.pulley(2)
        self.y.velocity_samples.clear()
        self.assertAlmostEqual(self.y.snapshot()['inches'],2,places=2)
        self.clock.now+=.4;self.boards.pulley(3)
        state=self.y.snapshot()
        self.assertAlmostEqual(state['cableSpeedInchesPerSecond'],2.5,places=2)
        self.assertAlmostEqual(state['motor']['commandedTurns'],625)
        self.assertFalse(state['estimated'])

    def test_x_is_estimated_and_zero_does_not_write_to_motor(self):
        self.boards.motor['X']['steps']=1600
        state=self.x.snapshot()
        self.assertTrue(state['estimatedFromSteps'])
        self.assertAlmostEqual(state['inches'],self.x.profile['travelPerRev'])
        self.assertEqual(self.x.command(dict(id='zero2',action='zero'))[0],200)
        self.assertEqual(self.x.snapshot()['inches'],0)
        self.assertEqual(self.boards.sent,[])

    def test_no_old_bench_distance_or_duration_caps(self):
        self.arm(self.y)
        code,_=self.move(self.y,action='rotate',turns=1300,direction=1)
        self.assertEqual(code,202)
        self.assertEqual(self.boards.sent[0][1],2_080_000)
        self.assertIsNone(self.y.snapshot()['moveTimeoutSeconds'])

    def test_y_rpm_cap_is_200_and_can_generate_the_required_rate(self):
        self.assertEqual(self.y.snapshot()['maxMotorRPM'],200)
        self.assertEqual(self.y.profile['rpm'],3)
        for ppr in (200,1600):
            profile={**self.y.profile,'pulsesPerRev':ppr,'rpm':200.01}
            self.assertEqual(self.y.command(dict(id='over-cap',action='stepper-settings',settings=profile))[0],400)
        profile={**self.y.profile,'rpm':200}
        self.assertEqual(self.y.command(dict(id='at-cap',action='stepper-settings',settings=profile))[0],200)
        self.arm(self.y)
        self.assertEqual(self.move(self.y,action='rotate',turns=1,direction=1)[0],202)
        self.assertEqual(self.boards.sent[0][2],5_333_333)
        self.assertAlmostEqual(self.x.snapshot()['maxMotorRPM'],187.5)

    def test_finer_microsteps_can_limit_y_below_200_rpm(self):
        profile={**self.y.profile,'pulsesPerRev':6400,'rpm':200}
        self.assertEqual(self.y.command(dict(id='too-fast',action='stepper-settings',settings=profile))[0],400)
        profile['rpm']=56.25
        self.assertEqual(self.y.command(dict(id='fine-steps',action='stepper-settings',settings=profile))[0],200)
        self.assertEqual(self.y.snapshot()['maxMotorRPM'],56.25)

    def test_existing_y_bench_firmware_is_not_commanded_above_its_pulse_limit(self):
        self.boards.motor['Y']['maxPulsesPerSecond']=5000
        self.assertEqual(self.y.snapshot()['maxMotorRPM'],187.5)
        self.y.profile['rpm']=200
        self.arm(self.y)
        code,result=self.move(self.y,action='rotate',turns=1,direction=1)
        self.assertEqual(code,400)
        self.assertIn('prepared Y motor firmware',result['error'])
        self.assertEqual(self.boards.sent,[])

    def test_short_feedback_gap_preserves_zero_but_sustained_loss_does_not(self):
        self.y.snapshot();self.boards.encoder=None
        self.clock.now+=.6
        self.assertTrue(self.y.snapshot()['zeroed'])
        self.clock.now+=.5
        self.assertFalse(self.y.snapshot()['zeroed'])

    def test_encoder_epoch_reset_requires_new_reference(self):
        self.boards.pulley(1,epoch=131072)
        self.assertFalse(self.y.snapshot()['zeroed'])

    def test_pulley_latency_after_motor_completion_does_not_false_fault(self):
        self.arm(self.y)
        self.assertEqual(self.move(self.y,action='down',inches=5)[0],202)
        job=self.y.active
        def advance(now):
            self.boards.motor['Y'].update(moving=False,reason='done',remaining=0)
            self.boards.pulley(0 if now<100.25 else 4.9)
        self.clock.callback=advance
        self.y._watch(job)
        self.assertEqual(self.y.outcome,'done')
        self.assertGreaterEqual(self.clock.now,100.3)

    def test_missing_feedback_during_move_stops_once_after_grace(self):
        self.arm(self.y);self.move(self.y,action='down',inches=5)
        job=self.y.active
        self.clock.callback=lambda now:setattr(self.boards,'encoder',None)
        self.y._watch(job)
        self.assertGreaterEqual(self.clock.now,101.)
        self.assertFalse(self.y.armed())
        self.assertEqual(self.boards.sent.count(('Y','STOP')),1)

    def test_stop_cancels_pending_id_and_disarms_even_when_idle(self):
        self.arm(self.x)
        self.x.command(dict(id='stop',action='stop',cancelId='move'))
        self.assertFalse(self.x.armed())
        self.arm(self.x)
        self.assertEqual(self.move(self.x,action='rotate',turns=.125,direction=1)[0],409)
        self.assertEqual(self.boards.sent,[('X','STOP')])

    def test_motor_disconnect_and_expired_browser_lease_stop(self):
        self.arm(self.x);self.move(self.x,action='rotate',turns=1,direction=1)
        job=self.x.active;self.clock.now=job.expires+.01
        self.x._watch(job)
        self.assertEqual(self.x.outcome,'error')
        self.assertFalse(self.x.armed())

    def test_a_late_move_from_before_estop_cannot_run_after_rearming(self):
        self.arm(self.x)
        stale_arm=self.x.arm_id
        self.x.command(dict(id='estop',action='stop'))
        self.arm(self.x)
        code,_=self.x.command(dict(id='late',action='rotate',turns=1,direction=1,mode='live',armId=stale_arm))
        self.assertEqual(code,409)
        self.assertEqual(self.boards.sent,[('X','STOP')])

    def test_route_limit_and_direction_are_enforced_without_global_y_cap(self):
        self.arm(self.y)
        code,_=self.move(self.y,action='down',inches=5,routeLimitInches=4)
        self.assertEqual(code,400)
        self.assertEqual(self.boards.sent,[])
        code,_=self.move(self.y,action='up',inches=100)
        self.assertEqual(code,202)
        self.assertLess(self.boards.sent[0][1],0)

    def test_uncalibrated_x_cannot_present_inches_or_move_by_distance(self):
        self.x.profile['calibrated']=False
        self.assertIsNone(self.x.snapshot()['inches'])
        self.arm(self.x)
        self.assertEqual(self.move(self.x,action='right',inches=2)[0],400)

    def test_speed_change_preserves_x_zero_but_geometry_change_does_not(self):
        original=self.x.motor_origin
        payload=dict(id='speed',action='stepper-settings',settings={**self.x.profile,'rpm':4})
        self.assertEqual(self.x.command(payload)[0],200)
        self.assertEqual(self.x.motor_origin,original)
        payload['settings']['travelPerRev']=4
        self.assertEqual(self.x.command(payload)[0],200)
        self.assertIsNone(self.x.motor_origin)

    def test_y_zero_uses_encoder_even_if_motor_nano_is_disconnected(self):
        self.boards.motor['Y']=None
        self.boards.pulley(2)
        self.assertEqual(self.y.command(dict(id='zero-only-encoder',action='zero'))[0],200)
        self.assertEqual(self.y.snapshot()['inches'],0)
        self.assertFalse(self.y.snapshot()['motorConnected'])


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.boards=SerialBoards.__new__(SerialBoards)
        self.boards.lock=threading.RLock();self.boards.generation=0
        self.boards._write=lambda *_:True
        self.boards.ports={}
        self.boards.motor_moves={}
    def receive(self,port,line):self.boards._receive(port,line.encode(),time.monotonic())
    def test_role_handshake_and_independent_encoder_never_cross_route(self):
        x,y,encoder=Port(-1,1),Port(-2,2),Port(-3,3)
        self.boards.ports=dict(x=x,y=y,encoder=encoder)
        self.receive(x,'SB1 X 1 0 0 0 0 0 idle 1 1 0 0')
        self.assertIsNone(self.boards.motor_reading('X'))
        self.receive(x,'SBH1 BRICKMAPPER_STEPPER X 1 STEP=9 DIR=8 ALM=2 PLACE=3')
        self.receive(y,'SBH1 BRICKMAPPER_STEPPER Y 1 STEP=9 DIR=8 ALM=2 PLACE=3')
        self.receive(y,'SB1 Y 1 0 0 500 0 0 idle 1 1 0 0')
        self.receive(encoder,'BM1 Y 99 8192 idle 65536')
        self.assertEqual(self.boards.motor_reading('X')['steps'],0)
        self.assertEqual(self.boards.motor_reading('Y')['steps'],500)
        self.assertEqual(self.boards.motor_reading('Y')['maxPulsesPerSecond'],6000)
        self.assertEqual(self.boards.reading('Y').counts,8192)
    def test_duplicate_motor_roles_are_not_selected(self):
        for i in range(2):
            port=Port(-i-1,i+1);self.boards.ports[str(i)]=port
            self.receive(port,'SBH1 BRICKMAPPER_STEPPER X 1 STEP=9 DIR=8')
            self.receive(port,'SB1 X 1 0 0 0 0 0 idle 1 1 0 0')
        self.assertIsNone(self.boards.motor_reading('X'))

    def motor_ports(self):
        x,y=Port(-1,1),Port(-2,2)
        self.boards.ports={'current-x':x,'current-y':y}
        for role,port in [('X',x),('Y',y)]:
            self.receive(port,f'SBH1 BRICKMAPPER_STEPPER {role} 1 STEP=9 DIR=8')
            self.receive(port,f'SB1 {role} 1 0 0 0 0 0 idle 0 0 0 0')
        return x,y

    def test_separate_motor_nanos_route_each_command_to_its_own_port(self):
        x,y=self.motor_ports()
        sent=[]
        self.boards._write=lambda p,packet:sent.append((p,packet)) or True
        self.assertTrue(self.boards.start_motor(1,1,1600,80000,400000,'X'))
        self.assertEqual(sent[-1],(x,b'PING\nARM\nMOVE 1 1600 80000 400000\n'))
        self.assertFalse(self.boards.start_motor(2,1,1600,80000,400000,'Y'))
        self.assertTrue(self.boards.stop_motor('Y'))
        self.assertEqual(sent[-1],(y,b'STOP\n'))
        self.assertIn('X',self.boards.motor_moves)
        self.assertTrue(self.boards.stop_motor('X'))
        self.assertEqual(sent[-1],(x,b'STOP\n'))
        self.assertTrue(self.boards.start_motor(2,1,-1600,80000,400000,'Y'))
        self.assertEqual(sent[-1],(y,b'PING\nARM\nMOVE 1 -1600 80000 400000\n'))

    def test_current_y_bench_firmware_remains_compatible(self):
        y,encoder=Port(-1,1),Port(-2,2)
        self.boards.ports={'current-y':y,'pulley':encoder}
        self.receive(y,'SBH1 WINCH_NANO_EVERY_BENCH 1 STEP=9 DIR=8 ALM=2 PEND=3')
        self.receive(y,'SB1 1 0 0 500 0 0 idle 1 1 0 0')
        self.receive(encoder,'BM1 Y 99 8192 idle 65536')
        self.assertEqual(self.boards.motor_reading('Y')['steps'],500)
        self.assertEqual(self.boards.motor_reading('Y')['maxPulsesPerSecond'],5000)
        self.assertEqual(self.boards.reading('Y').counts,8192)
        self.assertTrue(self.boards.start_motor(1,1,1600,80000,400000,'Y'))

    def test_one_motor_disconnect_does_not_hide_the_other_or_pulley(self):
        x,y=self.motor_ports()
        encoder=Port(-3,3);self.boards.ports['pulley']=encoder
        self.receive(encoder,'BM1 Y 99 8192 idle 65536')
        del self.boards.ports['current-x']
        self.assertIsNone(self.boards.motor_reading('X'))
        self.assertIsNotNone(self.boards.motor_reading('Y'))
        self.assertEqual(self.boards.reading('Y').counts,8192)
        self.boards.ports['current-x']=x
        del self.boards.ports['current-y']
        self.assertIsNotNone(self.boards.motor_reading('X'))
        self.assertIsNone(self.boards.motor_reading('Y'))
        self.assertEqual(self.boards.reading('Y').counts,8192)

    def test_wrong_role_frames_cannot_cross_route_and_shared_layout_is_not_selected(self):
        x,y=self.motor_ports()
        self.receive(x,'SB1 Y 2 0 0 999 0 0 idle 0 0 0 0')
        self.assertEqual(self.boards.motor_reading('X')['steps'],0)
        self.assertEqual(self.boards.motor_reading('Y')['steps'],0)
        shared=Port(-3,3);self.boards.ports={'obsolete-shared':shared}
        self.receive(shared,'SBH1 BRICKMAPPER_STEPPER XY 1 XSTEP=10 XDIR=7 YSTEP=9 YDIR=8')
        self.receive(shared,'SB1 X 1 0 0 0 0 0 idle 0 0 0 0')
        self.assertIsNone(self.boards.motor_reading('X'))
        self.assertIsNone(self.boards.motor_reading('Y'))

    def test_old_x_firmware_cannot_act_as_the_new_stepper_controller(self):
        old=Port(-1,1);self.boards.ports={'not-yet-repurposed-x':old}
        self.receive(old,'BM1 X 99 8192 idle 65536')
        self.assertIsNone(self.boards.motor_reading('X'))
        self.assertFalse(self.boards.start_motor(1,1,1600,80000,400000,'X'))

    def test_live_motion_report_also_blocks_the_other_axis_without_a_host_lease(self):
        x,y=self.motor_ports()
        self.receive(x,'SB1 X 2 1 1 100 1500 80000 moving 0 0 0 1')
        self.assertFalse(self.boards.start_motor(2,1,1600,80000,400000,'Y'))

    def test_y_telemetry_accepts_200_rpm_pulse_rate_without_expanding_x_limit(self):
        x,y=self.motor_ports()
        self.receive(y,'SB1 Y 2 1 1 100 1500 5333333 moving 0 0 0 1')
        self.assertAlmostEqual(self.boards.motor_reading('Y')['rate'],5333.333)
        self.receive(x,'SB1 X 2 1 1 100 1500 5333333 moving 0 0 0 1')
        self.assertEqual(self.boards.motor_reading('X')['sequence'],1)


if __name__=='__main__':unittest.main()
