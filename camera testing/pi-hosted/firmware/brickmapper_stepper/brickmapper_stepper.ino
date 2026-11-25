// One motor per Nano Every: D9 -> PUL+, D8 -> DIR+, GND -> both minus inputs.
// X reuses the original X Nano; Y keeps its existing dedicated motor Nano.
// The external pulley encoder remains on its own, unchanged Nano.
#include <Arduino.h>
#include <avr/interrupt.h>
#include <avr/wdt.h>
#include <util/atomic.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include "motion_core.h"

#if !defined(ARDUINO_AVR_NANO_EVERY) || !defined(MILLIS_USE_TIMERB3)
#error Select Nano Every with the official megaAVR core and TCB3 timekeeping.
#endif
#if !defined(BRICKMAPPER_MOTOR_AXIS) || (BRICKMAPPER_MOTOR_AXIS != 1 && BRICKMAPPER_MOTOR_AXIS != 2)
#error Build with tools/build-stepper-firmware.py to select the X or Y image.
#endif
constexpr bool IS_Y = BRICKMAPPER_MOTOR_AXIS == 2;
constexpr char MOTOR_ROLE = IS_Y ? 'Y' : 'X';
constexpr uint8_t STEP_PIN = 9, DIR_PIN = 8, ALARM_PIN = 2, PLACE_PIN = 3;
PulseEngine engine;
uint32_t maxRate = 80000, acceleration = 400000, moveId = 0, lastId = 0;
uint32_t sequence = 0, lastRamp = 0, lastReport = 0;
volatile uint8_t alarmMode = 0;
volatile uint16_t alarmTicks = 0;
char line[110];
uint8_t used = 0;
bool discard = false;
bool fixedSpeed = false;

bool alarmActive() {
  if (!IS_Y) return false; // The X DM542 has no connected ALM/EX outputs.
  const bool high = (VPORTA.IN & PIN0_bm) != 0;
  return alarmMode && (alarmMode == 1 ? high : !high);
}

ISR(TCB2_INT_vect) {
  TCB2.INTFLAGS = TCB_CAPT_bm;
  if (alarmActive()) {
    if (alarmTicks < 200) ++alarmTicks;
    if (alarmTicks >= 200 && engine.armed) engine.stop(PulseEngine::ALARM);
  } else alarmTicks = 0;
  if (engine.tick()) VPORTB.OUT |= PIN0_bm; // D9 = PB0; leave other pins unchanged.
  else VPORTB.OUT &= ~PIN0_bm;
}

void identity() {
  Serial.print("SBH1 BRICKMAPPER_STEPPER ");
  Serial.print(MOTOR_ROLE);
  Serial.println(IS_Y ? " 1 STEP=9 DIR=8 ALM=2 PLACE=3" : " 1 STEP=9 DIR=8");
  Serial.println("SBC FIXED=1 PULSE_US=100");
}
const char* reasonName(PulseEngine::Reason reason) {
  switch (reason) {
    case PulseEngine::RUNNING: return "moving";
    case PulseEngine::DONE: return "done";
    case PulseEngine::STOPPED: return "stopped";
    case PulseEngine::LINK_LOST: return "link_lost";
    case PulseEngine::ALARM: return "alarm";
    default: return "idle";
  }
}
void ack(const char* name, uint32_t id = 0) {
  Serial.print("SBA "); Serial.print(MOTOR_ROLE); Serial.print(' ');
  Serial.print(name); Serial.print(' '); Serial.println(id);
}
void error(const char* message) { Serial.print("SBE "); Serial.println(message); }
bool number(char* text, int32_t &value) {
  if (!text || !*text) return false;
  errno = 0;
  char* end;
  const long n = strtol(text, &end, 10);
  if (errno || *end || n < INT32_MIN || n > INT32_MAX) return false;
  value = n;
  return true;
}
void command(char* packet) {
  char* save;
  char* name = strtok_r(packet, " ", &save);
  if (!name) return;
  if (!strcmp(name, "HELLO") && !strtok_r(nullptr, " ", &save)) {
    identity();
  } else if (!strcmp(name, "PING") && !strtok_r(nullptr, " ", &save)) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { engine.ping(); }
  } else if (!strcmp(name, "STOP") && !strtok_r(nullptr, " ", &save)) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { engine.stop(); VPORTB.OUT &= ~PIN0_bm; }
    ack("STOP");
  } else if (!strcmp(name, "ARM") && !strtok_r(nullptr, " ", &save)) {
    bool ok = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { if (!alarmActive()) ok = engine.arm(); }
    if (ok) ack("ARM"); else error("cannot_arm");
  } else if (!strcmp(name, "ALARM")) {
    int32_t mode;
    if (!number(strtok_r(nullptr, " ", &save), mode) || mode < 0 || mode > 2 ||
        (!IS_Y && mode) || strtok_r(nullptr, " ", &save)) {
      error("invalid_alarm_mode"); return;
    }
    bool ok = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!engine.armed && !engine.moving) { alarmMode = mode; alarmTicks = 0; ok = true; }
    }
    if (ok) ack("ALARM"); else error("disarm_first");
  } else if (!strcmp(name, "MOVE")) {
    int32_t id, steps, rate, accel;
    if (!number(strtok_r(nullptr, " ", &save), id) ||
        !number(strtok_r(nullptr, " ", &save), steps) ||
        !number(strtok_r(nullptr, " ", &save), rate) ||
        !number(strtok_r(nullptr, " ", &save), accel) || strtok_r(nullptr, " ", &save) ||
        id <= 0 || uint32_t(id) <= lastId || !steps || steps == INT32_MIN ||
        rate < 1000 || rate > int32_t(PulseEngine::MAX_RATE) ||
        (accel != 0 && accel < 1000) || accel > 10000000L) { error("invalid_move"); return; }
    bool ready;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { ready = engine.armed && !engine.moving; }
    if (!ready || alarmActive()) { error("not_ready"); return; }
    digitalWrite(DIR_PIN, steps > 0 ? HIGH : LOW);
    delayMicroseconds(20);
    bool ok;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      maxRate = rate; acceleration = accel; fixedSpeed = accel == 0;
      ok = engine.begin(steps);
      if (ok && fixedSpeed) engine.rate = rate;
    }
    if (!ok) { error("not_ready"); return; }
    lastId = moveId = id;
    lastRamp = millis();
    ack("MOVE", id);
  } else error("unknown_command");
}

void setup() {
  pinMode(STEP_PIN, OUTPUT); digitalWrite(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW);
  if (IS_Y) { pinMode(ALARM_PIN, INPUT_PULLUP); pinMode(PLACE_PIN, INPUT_PULLUP); }
  Serial.begin(115200);
  TCB2.CTRLA = 0; TCB2.CTRLB = TCB_CNTMODE_INT_gc;
  TCB2.CCMP = uint16_t(F_CPU / PulseEngine::TICK_HZ - 1);
  TCB2.CNT = 0; TCB2.INTFLAGS = TCB_CAPT_bm; TCB2.INTCTRL = TCB_CAPT_bm;
  TCB2.CTRLA = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
  wdt_enable(WDTO_1S);
  identity();
}
void loop() {
  wdt_reset();
  for (uint8_t n = 0; n < 110 && Serial.available(); ++n) {
    const char ch = Serial.read();
    if (ch == '\n') {
      if (!discard) { line[used] = 0; command(line); } else error("line_too_long");
      discard = false; used = 0;
    } else if (ch != '\r' && !discard) {
      if (used < sizeof(line) - 1) line[used++] = ch; else discard = true;
    }
  }
  const uint32_t now = millis();
  if (uint32_t(now - lastRamp) >= 5) {
    const uint32_t dt = min(uint32_t(20), uint32_t(now - lastRamp));
    lastRamp = now;
    bool moving; uint32_t rate, left;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { moving = engine.moving; rate = engine.rate; left = engine.remaining; }
    if (moving && !fixedSpeed) {
      const uint32_t next = rampRate(rate, maxRate, acceleration, left, dt);
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { if (engine.moving) engine.rate = next; }
    }
  }
  if (uint32_t(now - lastReport) >= 50) {
    bool armed, moving; int32_t position; uint32_t left, rate; PulseEngine::Reason reason;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      armed = engine.armed; moving = engine.moving; position = engine.position;
      left = engine.remaining; rate = engine.rate; reason = engine.reason;
    }
    char report[128];
    const int length = snprintf(report, sizeof(report), "SB1 %c %lu %u %u %ld %lu %lu %s %u %u %u %lu\n",
      MOTOR_ROLE, static_cast<unsigned long>(++sequence), armed, moving, long(position),
      static_cast<unsigned long>(left), static_cast<unsigned long>(rate), reasonName(reason),
      IS_Y ? digitalRead(ALARM_PIN) : 0, IS_Y ? digitalRead(PLACE_PIN) : 0, alarmMode,
      static_cast<unsigned long>(moveId));
    if (length > 0 && length < int(sizeof(report))) Serial.write(reinterpret_cast<const uint8_t*>(report), length);
    lastReport = now;
  }
}
