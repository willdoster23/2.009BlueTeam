// Dedicated bench Nano Every. D9 -> PUL+, D8 -> DIR+, GND -> both minus inputs.
// Optional isolated driver outputs: ALM+ -> D2, PEND+ -> D3, both minus -> GND.
// ENA is intentionally NOT controlled: STOP ends pulses and preserves holding current.
#include <Arduino.h>
#include <avr/interrupt.h>
#include <avr/wdt.h>
#include <util/atomic.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include "motion_core.h"

#if !defined(ARDUINO_AVR_NANO_EVERY) || !defined(MILLIS_USE_TIMERB3)
#error Use the official Nano Every megaAVR core with TCB3 timekeeping.
#endif

constexpr uint8_t STEP_PIN = 9, DIR_PIN = 8, ALARM_PIN = 2, PEND_PIN = 3;
PulseEngine engine;
volatile uint8_t alarmMode = 0; // 0 = not configured; 1 = high means fault; 2 = low.
volatile uint16_t alarmTicks = 0;
uint32_t maxRate = 80000, acceleration = 400000, moveId = 0, lastId = 0;
uint32_t lastRamp = 0, lastReport = 0, sequence = 0;
char commandLine[100];
uint8_t used = 0;
bool discard = false;

bool alarmActive() {
  const bool level = (VPORTA.IN & PIN0_bm) != 0; // D2 = PA0
  return alarmMode && (alarmMode == 1 ? level : !level);
}

ISR(TCB2_INT_vect) {
  TCB2.INTFLAGS = TCB_CAPT_bm;
  if (alarmActive()) {
    if (alarmTicks < 200) ++alarmTicks; // 10 ms debounce; no default guessed polarity.
    if (alarmTicks >= 200 && engine.armed) engine.stop(PulseEngine::ALARM);
  } else alarmTicks = 0;
  if (engine.tick()) VPORTB.OUT |= PIN0_bm; // Nano D9 = PB0
  else VPORTB.OUT &= ~PIN0_bm;
}

const char *reasonName(PulseEngine::Reason reason) {
  switch (reason) {
    case PulseEngine::RUNNING: return "moving";
    case PulseEngine::DONE: return "done";
    case PulseEngine::STOPPED: return "stopped";
    case PulseEngine::LINK_LOST: return "link_lost";
    case PulseEngine::ALARM: return "alarm";
    default: return "idle";
  }
}

void acknowledge(const char *name, uint32_t id = 0) {
  Serial.print("SBA "); Serial.print(name); Serial.print(' '); Serial.println(id);
}
void error(const char *message) { Serial.print("SBE "); Serial.println(message); }
bool number(char *text, int32_t &out) {
  if (!text || !*text) return false;
  errno = 0;
  char *end;
  const long value = strtol(text, &end, 10);
  if (errno || *end) return false;
  out = value;
  return true;
}

void command(char *line) {
  char *save;
  char *name = strtok_r(line, " ", &save);
  if (!name) return;
  if (strcmp(name, "HELLO") == 0 && !strtok_r(nullptr, " ", &save)) {
    Serial.println("SBH1 WINCH_NANO_EVERY_BENCH 1 STEP=9 DIR=8 ALM=2 PEND=3");
  } else if (strcmp(name, "PING") == 0 && !strtok_r(nullptr, " ", &save)) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { engine.ping(); }
  } else if (strcmp(name, "STOP") == 0 && !strtok_r(nullptr, " ", &save)) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { engine.stop(); VPORTB.OUT &= ~PIN0_bm; }
    acknowledge("STOP");
  } else if (strcmp(name, "ARM") == 0 && !strtok_r(nullptr, " ", &save)) {
    bool ok = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!alarmActive()) ok = engine.arm();
    }
    if (ok) acknowledge("ARM"); else error("cannot_arm");
  } else if (strcmp(name, "ZERO") == 0 && !strtok_r(nullptr, " ", &save)) {
    bool ok = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!engine.moving) { engine.position = 0; ok = true; }
    }
    if (ok) acknowledge("ZERO"); else error("busy");
  } else if (strcmp(name, "ALARM") == 0) {
    int32_t mode;
    if (!number(strtok_r(nullptr, " ", &save), mode) || mode < 0 || mode > 2 ||
        strtok_r(nullptr, " ", &save)) { error("invalid_alarm_mode"); return; }
    bool ok = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (!engine.moving && !engine.armed) { alarmMode = mode; alarmTicks = 0; ok = true; }
    }
    if (ok) acknowledge("ALARM"); else error("disarm_first");
  } else if (strcmp(name, "MOVE") == 0) {
    int32_t id, steps, rate, accel;
    if (!number(strtok_r(nullptr, " ", &save), id) ||
        !number(strtok_r(nullptr, " ", &save), steps) ||
        !number(strtok_r(nullptr, " ", &save), rate) ||
        !number(strtok_r(nullptr, " ", &save), accel) ||
        strtok_r(nullptr, " ", &save) || id <= 0 || uint32_t(id) <= lastId ||
        !steps || steps == INT32_MIN ||
        rate < 1000 || rate > int32_t(PulseEngine::MAX_RATE) ||
        accel < 1000 || accel > 10000000L) { error("invalid_move"); return; }
    bool busy;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { busy = engine.moving || !engine.armed; }
    if (busy || alarmActive()) { error("not_ready"); return; }
    digitalWrite(DIR_PIN, steps > 0 ? HIGH : LOW);
    delayMicroseconds(20); // DIR setup > manual's 5-6 us.
    bool ok;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      maxRate = uint32_t(rate); acceleration = uint32_t(accel);
      ok = engine.begin(steps);
    }
    if (!ok) { error("not_ready"); return; }
    lastId = moveId = uint32_t(id);
    lastRamp = millis();
    acknowledge("MOVE", moveId);
  } else error("unknown_command");
}

void setup() {
  // No automatic movement on boot, USB reconnection or watchdog reset.
  pinMode(STEP_PIN, OUTPUT); digitalWrite(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW);
  pinMode(ALARM_PIN, INPUT_PULLUP);
  pinMode(PEND_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  TCB2.CTRLA = 0;
  TCB2.CTRLB = TCB_CNTMODE_INT_gc;
  TCB2.CCMP = uint16_t(F_CPU / PulseEngine::TICK_HZ - 1);
  TCB2.CNT = 0;
  TCB2.INTFLAGS = TCB_CAPT_bm;
  TCB2.INTCTRL = TCB_CAPT_bm;
  TCB2.CTRLA = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
  wdt_enable(WDTO_1S);
  Serial.println("SBH1 WINCH_NANO_EVERY_BENCH 1 STEP=9 DIR=8 ALM=2 PEND=3");
}

void loop() {
  wdt_reset();
  for (uint8_t n = 0; n < 100 && Serial.available(); ++n) {
    const char ch = Serial.read();
    if (ch == '\n') {
      if (!discard) { commandLine[used] = 0; command(commandLine); }
      else error("line_too_long");
      discard = false; used = 0;
    } else if (ch != '\r' && !discard) {
      if (used < sizeof(commandLine) - 1) commandLine[used++] = ch;
      else discard = true;
    }
  }
  const uint32_t now = millis();
  if (uint32_t(now - lastRamp) >= 5) {
    const uint32_t elapsed = min(uint32_t(20), uint32_t(now - lastRamp));
    lastRamp = now;
    uint32_t rate, left;
    bool moving;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { rate = engine.rate; left = engine.remaining; moving = engine.moving; }
    if (moving) {
      const uint32_t next = rampRate(rate, maxRate, acceleration, left, elapsed);
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { if (engine.moving) engine.rate = next; }
    }
  }
  if (uint32_t(now - lastReport) >= 50) {
    bool armed, moving;
    int32_t position;
    uint32_t left, rate;
    PulseEngine::Reason reason;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      armed = engine.armed; moving = engine.moving; position = engine.position;
      left = engine.remaining; rate = engine.rate; reason = engine.reason;
    }
    char report[128];
    const int length = snprintf(report, sizeof(report), "SB1 %lu %u %u %ld %lu %lu %s %u %u %u %lu\n",
      ++sequence, armed, moving, long(position), left, rate, reasonName(reason),
      digitalRead(ALARM_PIN), digitalRead(PEND_PIN), alarmMode, moveId);
    // Reports can exceed the core's 64-byte TX ring; write() drains the rest
    // with interrupts enabled, so the independent pulse timer keeps running.
    if (length > 0 && length < int(sizeof(report)) && Serial.availableForWrite() >= 32) {
      Serial.write(reinterpret_cast<const uint8_t *>(report), length);
      lastReport = now;
    }
  }
}
