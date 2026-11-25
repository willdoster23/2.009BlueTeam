"""STEP/DIR axes. Pulley feedback owns Y; X is explicitly a step-count estimate."""
from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time
import threading

from outrigger import PulleyAxis, MOTION_LEASE, PULLEY_COUNTS, valid_id, Y_CONFIG

MAX_STEPS = 2**31 - 1
MAX_PULSES_PER_SECOND = {'X': 5000, 'Y': 6000}
Y_MAX_RPM = 200
ENCODER_GRACE = 1.0


def number(value, label, minimum, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'Check {label}.')
    return value


def rpm_limit(role, pulses_per_rev, pulse_capacity=None):
    capacity = min(MAX_PULSES_PER_SECOND[role], pulse_capacity) if pulse_capacity else MAX_PULSES_PER_SECOND[role]
    timing_limit = capacity * 60 / pulses_per_rev
    return min(Y_MAX_RPM, timing_limit) if role == 'Y' else timing_limit


def settings(value, role):
    if not isinstance(value, dict):
        raise ValueError('Use valid stepper settings.')
    ppr = number(value.get('pulsesPerRev'), 'pulses per revolution', 200, 51200)
    if int(ppr) != ppr:
        raise ValueError('Pulses per revolution must be a whole number.')
    rpm = number(value.get('rpm'), 'motor RPM', .1, rpm_limit(role, ppr))
    accel = number(value.get('accel'), 'acceleration', .1, 10_000_000 * 60 / (ppr * 1000))
    travel = number(value.get('travelPerRev'), 'travel per motor revolution', .001, 1000)
    sign, alarm = value.get('positiveDirection'), value.get('alarmMode', 0)
    if type(sign) is not int or sign not in (-1, 1) or type(alarm) is not int or alarm not in (0, 1, 2):
        raise ValueError('Check motor direction and alarm interpretation.')
    return dict(pulsesPerRev=int(ppr), rpm=rpm, accel=accel, travelPerRev=travel,
                positiveDirection=sign, alarmMode=alarm if role == 'Y' else 0,
                calibrated=value.get('calibrated') is True)


@dataclass
class Move:
    id: str
    motor_id: int
    generation: int
    expires: float
    began: float
    direction: int
    target: float | None = None
    route_limit: float | None = None
    finished_at: float | None = None
    start_inches: float | None = None
    progress_inches: float = 0
    progress_at: float = 0


class StepperAxis(PulleyAxis):
    def __init__(self, boards, lock, role='Y', config=Y_CONFIG):
        self.role = role
        self.last_good_encoder = None
        self.velocity_samples = deque(maxlen=150)
        self.motor_origin = None
        self.arm_id = None
        self.arm_until = 0
        self.settings_error = ''
        super().__init__(boards, None, lock, lambda: False, lambda: None, config)
        self.settings_file = config.with_name(role.lower() + '-stepper.json')
        self.profile = dict(pulsesPerRev=1600, rpm=3, accel=10,
                            travelPerRev=math.pi * (24 / 25.4 if role == 'X' else 4),
                            positiveDirection=1, alarmMode=0, calibrated=False)
        try:
            self.profile = settings(json.loads(self.settings_file.read_text()), role)
        except FileNotFoundError:
            pass
        except (ValueError, OSError) as exc:
            self.settings_error = f'Stepper settings unavailable: {exc}'
        self.last_motor_generation = None

    @staticmethod
    def _timing(_):
        return False, 1.0  # Never use relay time or motor counts as measured cable length.

    def _sample(self):
        reading = self.boards.reading('Y')
        if reading is not None and reading.state == 'idle':
            self.last_good_encoder = reading
        elif self.last_good_encoder and time.monotonic() - self.last_good_encoder.received <= ENCODER_GRACE:
            reading = self.last_good_encoder
        else:
            reading = None
        if reading and time.monotonic() - reading.received > ENCODER_GRACE:
            reading = None
        if self.origin and (reading is None or self.origin[:2] != (reading.generation, reading.tag)):
            self._forget_origin()
            self.velocity_samples.clear()
        if reading and self.saved_origin is not None:
            tag, count = self.saved_origin
            self.saved_origin = None
            if tag == reading.tag:
                self.origin = (reading.generation, tag, count)
        if reading:
            if not self.velocity_samples or reading.received > self.velocity_samples[-1][0]:
                self.velocity_samples.append((reading.received, reading.counts * self.sign * self.travel / PULLEY_COUNTS))
            while self.velocity_samples and reading.received - self.velocity_samples[0][0] > .7:
                self.velocity_samples.popleft()
        return reading

    def armed(self):
        return bool(self.arm_id and time.monotonic() < self.arm_until)

    def snapshot(self):
        motor = self.boards.motor_reading(self.role)
        if motor is None and self.arm_id:
            self.stop('Motor Nano connection lost; stopped and disarmed.')
        if motor and self.last_motor_generation is not None and motor['generation'] != self.last_motor_generation:
            self.arm_id = self.motor_origin = None
        if motor:
            self.last_motor_generation = motor['generation']
        profile = dict(self.profile)
        if self.role == 'Y':
            result = super().snapshot()
        else:
            zeroed = motor is not None and self.motor_origin is not None and self.motor_origin[0] == motor['generation']
            count = (motor['steps'] - self.motor_origin[1]) * profile['positiveDirection'] if zeroed else None
            result = dict(mode='live', connected=motor is not None, zeroed=zeroed, counts=count,
                          inches=count / profile['pulsesPerRev'] * profile['travelPerRev'] if count is not None and profile['calibrated'] else None,
                          warning=None, coordinateEpoch=str(self.motor_origin), zeroInches=0,
                          message=self.message or 'X position is estimated from commanded steps; skipped steps are not detected.')
        zeroed = result['zeroed']
        capacity = min(MAX_PULSES_PER_SECOND[self.role],
                       motor.get('maxPulsesPerSecond', MAX_PULSES_PER_SECOND[self.role])) if motor else MAX_PULSES_PER_SECOND[self.role]
        result.update(drive='stepper', role=self.role, motorConnected=motor is not None,
                      motorArmed=self.armed(), armId=self.arm_id, motor=({**motor,
                          'commandedRPM': motor['rate'] * 60 / profile['pulsesPerRev'],
                          'commandedTurns': motor['steps'] / profile['pulsesPerRev']} if motor else None),
                      stepperSettings=profile, settingsError=self.settings_error,
                      maxMotorRPM=rpm_limit(self.role, profile['pulsesPerRev'], capacity),
                      maxPulsesPerSecond=capacity,
                      motorRPMCap=Y_MAX_RPM if self.role == 'Y' else None,
                      ready=bool(motor and zeroed and self.armed() and profile['calibrated']),
                      upReady=bool(motor and zeroed and self.armed() and profile['calibrated']),
                      downReady=bool(motor and zeroed and self.armed() and profile['calibrated']),
                      moving=self.active is not None, moveId=self.last_id, outcome=self.outcome,
                      estimated=self.role == 'X', estimatedFromSteps=self.role == 'X',
                      maxJogInches=MAX_STEPS / profile['pulsesPerRev'] * profile['travelPerRev'],
                      routeLimitSupported=True, moveTimeoutSeconds=None,
                      message=self.settings_error or self.message or result['message'])
        result['cableSpeedInchesPerSecond'] = None
        if self.role == 'Y' and len(self.velocity_samples) > 1:
            first, last = self.velocity_samples[0], self.velocity_samples[-1]
            if last[0] - first[0] >= .2 and time.monotonic() - last[0] <= ENCODER_GRACE:
                result['cableSpeedInchesPerSecond'] = (last[1] - first[1]) / (last[0] - first[0])
        return result

    def stop(self, message='Stepper stopped and disarmed.', disarm=True):
        self.arm_id = None
        self.arm_until = 0
        self.boards.stop_motor(self.role)
        if self.active:
            self.active = None
            self.outcome = 'error'
        self.message = message

    def close(self):
        self.stop()

    def command(self, payload):
        action, command_id = payload.get('action'), payload.get('id')
        if not valid_id(command_id):
            return 400, {'error': 'Invalid motion command ID.'}
        if action == 'stop':
            if valid_id(payload.get('cancelId')):
                self.handled.append(payload['cancelId'])
            self.stop()
            return 200, self.snapshot()
        if action == 'renew':
            if self.active and command_id == self.active.id and time.monotonic() < self.active.expires:
                self.active.expires = time.monotonic() + MOTION_LEASE
                self.arm_until = self.active.expires
                self.boards.renew_motor(self.role)
            elif not self.active and command_id == self.arm_id and self.armed():
                self.arm_until = time.monotonic() + MOTION_LEASE
            return 200, self.snapshot()
        if self.active:
            return 409, {'error': 'Wait for this move to finish.'}
        if command_id in self.handled:
            return 409, {'error': 'This motion command was already handled or canceled.'}
        if action == 'stepper-settings':
            if self.armed():
                return 409, {'error': 'Disarm before changing motor settings.'}
            try:
                profile = settings(payload.get('settings'), self.role)
                scratch = self.settings_file.with_suffix('.tmp')
                scratch.write_text(json.dumps(profile) + '\n')
                scratch.replace(self.settings_file)
                geometry_changed = any(profile[key] != self.profile[key] for key in
                                       ('pulsesPerRev', 'travelPerRev', 'positiveDirection'))
                self.profile, self.settings_error = profile, ''
                if geometry_changed:
                    self.motor_origin = None
            except (ValueError, OSError) as exc:
                return 400, {'error': str(exc)}
            return 200, self.snapshot()
        if action == 'calibrate' and self.role == 'Y':
            if self.armed():
                return 409, {'error': 'Disarm before changing pulley calibration.'}
            return super().command({**payload, 'timedY': False})
        if action == 'zero' and self.role == 'Y':
            return super().command(payload)
        motor = self.boards.motor_reading(self.role)
        if motor is None:
            return 503, {'error': f'Connect the {self.role} motor Nano over USB.'}
        if action == 'arm':
            if self.settings_error:
                return 409, {'error': self.settings_error}
            if motor['reason'] == 'alarm' or motor['moving']:
                return 409, {'error': 'Clear the motor fault or wait for it to stop.'}
            if motor['alarmMode'] != self.profile['alarmMode']:
                self.boards.motor_alarm(self.profile['alarmMode'], self.role)
                return 409, {'error': 'Applying driver alarm interpretation; arm again after the status updates.'}
            self.arm_id, self.arm_until = command_id, time.monotonic() + MOTION_LEASE
            self.handled.append(command_id)
            self.message = 'Armed for the next move.'
            return 200, self.snapshot()
        if action == 'zero':
            self.motor_origin = (motor['generation'], motor['steps'])
            self.handled.append(command_id)
            self.message = 'X commanded position zero set here.'
            return 200, self.snapshot()
        if not self.armed():
            return 409, {'error': 'Arm this motor before moving.'}
        if payload.get('armId') != self.arm_id:
            return 409, {'error': 'Motor arming changed. Wait for status to refresh before moving.'}
        if payload.get('mode') != 'live':
            return 409, {'error': 'Refresh the hardware controls before moving.'}
        try:
            return self._begin(payload, motor)
        except ValueError as exc:
            return 400, {'error': str(exc)}

    def _begin(self, payload, motor):
        p = self.profile
        target = route_limit = None
        basic = payload['action'] == 'basic-test'
        if basic:
            if not motor.get('fixedSpeedSupported'):
                raise ValueError('Upload the fixed-speed pulse firmware before using this test.')
            direction = payload.get('direction')
            if type(direction) is not int or direction not in (-1, 1):
                raise ValueError('Choose a test direction.')
            # One second at the selected RPM. No geometry, encoder or ramp is used.
            turns = p['rpm'] / 60
        elif payload['action'] == 'rotate':
            turns = number(payload.get('turns'), 'motor turns', .000001, MAX_STEPS / p['pulsesPerRev'])
            direction = payload.get('direction')
            if type(direction) is not int or direction not in (-1, 1):
                raise ValueError('Choose positive or negative motor turns.')
        else:
            positive, negative = ('right', 'left') if self.role == 'X' else ('down', 'up')
            if payload['action'] not in (positive, negative):
                raise ValueError('Choose a direction or a motor-turn test.')
            inches = number(payload.get('inches'), 'move distance', .1, math.inf)
            if not p['calibrated']:
                raise ValueError('Confirm travel per motor revolution in Setup first.')
            direction = 1 if payload['action'] == positive else -1
            turns = inches / p['travelPerRev']
            status = self.snapshot()
            if not status['zeroed']:
                raise ValueError('Set this axis zero before moving by inches.')
            if self.role == 'Y':
                target = status['inches'] + direction * inches
                route_limit = payload.get('routeLimitInches')
                if route_limit is not None:
                    number(route_limit, 'lower Y limit', 0, math.inf)
                    if status['inches'] > route_limit or target > route_limit + 1e-8:
                        raise ValueError('That route move exceeds the lower Y limit.')
        pulses = turns * p['pulsesPerRev']
        if not math.isfinite(pulses) or not .5 <= pulses <= MAX_STEPS:
            raise ValueError('Move exceeds the motor pulse-counter range.')
        steps = round(pulses) * direction * p['positiveDirection']
        if not -(2**31) <= motor['steps'] + steps <= MAX_STEPS:
            raise ValueError('Motor pulse counter is full; reset the motor Nano while idle before continuing.')
        motor_id = motor['moveId'] + 1
        if motor_id > MAX_STEPS:
            raise ValueError('Motor command counter is full; restart the motor Nano while idle.')
        rate = round(p['rpm'] * p['pulsesPerRev'] / 60 * 1000)
        accel = 0 if basic else round(p['accel'] * p['pulsesPerRev'] / 60 * 1000)
        capacity = min(MAX_PULSES_PER_SECOND[self.role], motor.get('maxPulsesPerSecond', MAX_PULSES_PER_SECOND[self.role]))
        if self.role == 'Y' and rate > capacity * 1000 and capacity < MAX_PULSES_PER_SECOND['Y']:
            raise ValueError('Upload the prepared Y motor firmware before using this speed; the existing Y bench firmware has a lower pulse limit.')
        if not 1000 <= rate <= capacity * 1000 or (not basic and not 1000 <= accel <= 10_000_000):
            raise ValueError('Speed or acceleration is outside the pulse timing range.')
        job = Move(payload['id'], motor_id, motor['generation'], time.monotonic() + MOTION_LEASE,
                   time.monotonic(), direction, target, route_limit)
        if target is not None:
            job.start_inches = status['inches']
            job.progress_at = job.began
        if not self.boards.start_motor(motor['generation'], motor_id, steps, rate, accel, self.role):
            raise ValueError('Motor connection changed before the move; arm again.')
        self.active, self.last_id, self.outcome = job, job.id, 'moving'
        self.handled.append(job.id)
        self.message = f'Moving {self.role}.'
        threading.Thread(target=self._watch, args=(job,), daemon=True).start()
        return 202, self.snapshot()

    def _watch(self, job):
        while True:
            with self.lock:
                if self.active is not job:
                    return
                motor = self.boards.motor_reading(self.role)
                error = None
                done = False
                if time.monotonic() >= job.expires:
                    error = 'Control page connection lost; stopped and disarmed.'
                elif motor is None or motor['generation'] != job.generation:
                    error = 'Motor Nano connection changed; stopped and disarmed.'
                elif motor['moveId'] != job.motor_id:
                    if time.monotonic() - job.began > .8:
                        error = 'Motor did not confirm this move; stopped and disarmed.'
                elif motor['reason'] == 'alarm':
                    error = 'Driver alarm input stopped the motor.'
                elif not motor['moving']:
                    if motor['reason'] == 'done':
                        done = True
                    else:
                        error = 'Motor stopped before completing its command.'
                if not error and job.target is not None:
                    reading = self._sample()
                    measured = self._counts(reading)
                    if measured is None:
                        error = 'Pulley feedback lost for more than one second or its reference reset; set Y zero again.'
                    else:
                        inches = measured * self.travel / PULLEY_COUNTS
                        progress = (inches - job.start_inches) * job.direction
                        if progress >= job.progress_inches + .02:
                            job.progress_inches, job.progress_at = progress, time.monotonic()
                        if job.route_limit is not None and inches > job.route_limit:
                            error = 'Lower Y limit reached; stopped and disarmed.'
                        elif progress < -self.travel / 4:
                            error = 'Pulley travel is opposite the requested direction; check direction setup.'
                        elif (job.target - inches) * job.direction <= 0:
                            done = True
                        elif done:
                            # Allow the independent 100 Hz encoder to arrive and settle
                            # after the 20 Hz motor status; never compare equal-time frames.
                            if job.finished_at is None:
                                job.finished_at = time.monotonic()
                            settled = time.monotonic() - job.finished_at >= .3 and reading.received >= job.finished_at
                            done = settled
                            if settled and abs(job.target - inches) > .5:
                                error = 'Motor command finished outside the Y target window; check travel calibration and the mechanism.'
                            elif time.monotonic() - job.finished_at > ENCODER_GRACE and not settled:
                                error = 'Pulley feedback did not return after this move; set Y zero again.'
                        elif time.monotonic() - job.progress_at > max(5, .04 / (self.profile['rpm'] * self.profile['travelPerRev'] / 60)):
                            error = 'No sustained pulley progress while the motor was commanded; check the mechanism.'
                if error:
                    self.stop(error)
                    return
                if done:
                    self.boards.stop_motor(self.role)
                    self.active, self.outcome = None, 'done'
                    self.message = 'Measured Y target reached.' if job.target is not None else f'{self.role} commanded move completed.'
                    return
            time.sleep(.02)
