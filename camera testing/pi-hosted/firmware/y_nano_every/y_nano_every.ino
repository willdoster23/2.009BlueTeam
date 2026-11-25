// Y only: AS5048A PWM on Nano Every D2 (PA0), USB to the roof Pi.
// TCB0 captures pulse width; TCB1 captures period on the same EVSYS input.
// No motor/relay outputs. X runs on its separate Nano Every.
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/interrupt.h>
#include <util/atomic.h>
#include "pwm_encoder.h"

#if !defined(ARDUINO_AVR_NANO_EVERY) || !defined(MILLIS_USE_TIMERB3)
#error Select Arduino Nano Every with the official megaAVR core (TCB3 timekeeping).
#endif

constexpr uint8_t ENCODER_PIN = 2, QUEUE_SIZE = 16;
constexpr uint32_t REPORT_US = 10000;
struct Frame { uint16_t period, high; uint32_t at; };
volatile Frame queue[QUEUE_SIZE];
volatile uint8_t head = 0, tail = 0;
volatile bool overflowed = false;
volatile uint16_t overruns = 0;
volatile bool capturePaused = false;
volatile uint8_t badTiming = 0;
volatile uint16_t rawPeriod = 0, rawHigh = 0;
PwmEncoder encoder(F_CPU);
bool epochReady = false;
uint32_t sequence = 0, lastReport = 0, lastDiagnostic = 0;
uint16_t lastPeriod = 0, lastHigh = 0;
uint32_t retryAt = 0;
uint16_t probeRises = 0, probeFalls = 0, probeMinimum = 65535, probeMaximum = 0;
bool probeReported = false;

void probeInput() {
  pinMode(ENCODER_PIN, INPUT);
  PORTA.PIN0CTRL = PORT_ISC_INTDISABLE_gc;
  TCB2.CTRLA = 0;
  TCB2.INTCTRL = 0;
  TCB2.CTRLB = TCB_CNTMODE_INT_gc;
  TCB2.CCMP = 65535;
  TCB2.CNT = 0;
  TCB2.CTRLA = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
  const uint32_t start = micros();
  uint8_t previous = VPORTA.IN & PIN0_bm;
  uint16_t rise = 0;
  while (uint32_t(micros() - start) < 20000) {
    for (uint8_t i = 0; i < 64; ++i) {
      const uint8_t level = VPORTA.IN & PIN0_bm;
      if (level == previous) continue;
      previous = level;
      if (level) {
        const uint16_t now = TCB2.CNT;
        if (probeRises) {
          const uint16_t period = now - rise;
          if (period < probeMinimum) probeMinimum = period;
          if (period > probeMaximum) probeMaximum = period;
        }
        rise = now;
        ++probeRises;
      } else ++probeFalls;
    }
  }
  TCB2.CTRLA = 0;
}

ISR(TCB0_INT_vect) {
  // Read hardware latches promptly; reading CCMP clears CAPT automatically.
  const uint16_t high = TCB0.CCMP;
  const uint16_t period = TCB1.CCMP;
  TCB0.INTFLAGS = TCB_CAPT_bm;
  TCB1.INTFLAGS = TCB_CAPT_bm;
  rawPeriod = period;
  rawHigh = high;
  // An unconnected/noisy input must not consume the CPU in an interrupt storm.
  // Reject impossible timing before queuing; retry a noisy line after 100 ms.
  if (period < (F_CPU / 1000000UL) * 787UL ||
      period > (F_CPU / 1000000UL) * 1275UL || high >= period) {
    if (++badTiming >= 8) {
      TCB0.INTCTRL = 0;
      capturePaused = true;
    }
    return;
  }
  badTiming = 0;
  const uint8_t next = (head + 1) & (QUEUE_SIZE - 1);
  if (next == tail) { overflowed = true; ++overruns; return; }
  queue[head].period = period;
  queue[head].high = high;
  queue[head].at = micros();
  head = next;
}

bool reserveEpoch() {
  uint32_t boots;
  EEPROM.get(0, boots);
  boots = boots == UINT32_MAX ? 1 : boots + 1;
  if (!boots || boots > 0xffffUL) return false;
  EEPROM.put(0, boots);
  uint32_t saved;
  EEPROM.get(0, saved);
  if (saved != boots) return false;
  encoder = PwmEncoder(F_CPU, boots << 16);
  return true;
}

void startCapture() {
  pinMode(ENCODER_PIN, INPUT);  // No pull-up into the 3.3 V encoder output.
  PORTA.PIN0CTRL = PORT_ISC_INTDISABLE_gc;
  TCB0.CTRLA = TCB1.CTRLA = 0;
  TCB0.INTCTRL = TCB1.INTCTRL = 0;
  TCB0.CTRLB = TCB_CNTMODE_PW_gc;
  TCB1.CTRLB = TCB_CNTMODE_FRQ_gc;
  TCB0.EVCTRL = TCB1.EVCTRL = TCB_CAPTEI_bm;
  TCB0.CNT = TCB1.CNT = 0;
  TCB0.INTFLAGS = TCB1.INTFLAGS = TCB_CAPT_bm;
  // Event channel 0's PORT0 is PORTA; D2 is PA0 on the Nano Every.
  EVSYS.CHANNEL0 = EVSYS_GENERATOR_PORT0_PIN0_gc;
  EVSYS.USERTCB0 = EVSYS.USERTCB1 = EVSYS_CHANNEL_CHANNEL0_gc;
  TCB0.INTCTRL = TCB_CAPT_bm;
  TCB1.CTRLA = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
  TCB0.CTRLA = TCB_CLKSEL_CLKDIV1_gc | TCB_ENABLE_bm;
}

void countText(int64_t value, char *result) {
  char reverse[24];
  uint8_t size = 0, out = 0;
  const bool negative = value < 0;
  uint64_t magnitude = negative ? uint64_t(-(value + 1)) + 1 : uint64_t(value);
  do { reverse[size++] = '0' + magnitude % 10; magnitude /= 10; } while (magnitude);
  if (negative) result[out++] = '-';
  while (size) result[out++] = reverse[--size];
  result[out] = 0;
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  epochReady = reserveEpoch();
  probeInput();
  startCapture();
  lastReport = lastDiagnostic = micros();
}

void loop() {
  if (capturePaused) {
    encoder.invalidate();
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { tail = head; }
    const uint32_t now = micros();
    if (!retryAt) retryAt = now + 100000UL;
    if (int32_t(now - retryAt) >= 0) {
      ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        tail = head;
        badTiming = 0;
        capturePaused = false;
        startCapture();
      }
      retryAt = 0;
    }
  }
  bool lost;
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    lost = overflowed;
    if (lost) { tail = head; overflowed = false; }
  }
  if (lost) encoder.invalidate();
  while (true) {
    Frame frame;
    bool available = false;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      if (tail != head) {
        frame.period = queue[tail].period;
        frame.high = queue[tail].high;
        frame.at = queue[tail].at;
        tail = (tail + 1) & (QUEUE_SIZE - 1);
        available = true;
      }
    }
    if (!available) break;
    lastPeriod = frame.period;
    lastHigh = frame.high;
    if (epochReady) encoder.capture(frame.period, frame.high, frame.at);
  }
  const uint32_t now = micros();
  encoder.expire(now);
  if (!probeReported && uint32_t(now - lastDiagnostic) > 100000UL) {
    char packet[64];
    const int size = snprintf(packet, sizeof(packet), "BY2 %u %u %u %u\n",
                              probeRises, probeFalls, probeMinimum, probeMaximum);
    if (Serial.availableForWrite() >= size) {
      Serial.write(reinterpret_cast<const uint8_t *>(packet), size);
      probeReported = true;
    }
  }
  if (uint32_t(now - lastReport) >= REPORT_US) {
    lastReport += REPORT_US;
    if (uint32_t(now - lastReport) >= REPORT_US) lastReport = now;
    char count[24], packet[64];
    countText(encoder.valid ? encoder.counts : 0, count);
    const int size = snprintf(packet, sizeof(packet), "BM1 Y %lu %s %s %lu\n",
                              ++sequence, count, encoder.valid ? "idle" : "signal", encoder.epoch);
    if (size > 0 && size < int(sizeof(packet)) && Serial.availableForWrite() >= size)
      Serial.write(reinterpret_cast<const uint8_t *>(packet), size);
    digitalWrite(LED_BUILTIN, encoder.valid || (millis() / 250) % 2);
  }
  if (uint32_t(now - lastDiagnostic) >= 1000000UL) {
    char packet[64];
    uint16_t dropped;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
      dropped = overruns;
      lastPeriod = rawPeriod;
      lastHigh = rawHigh;
    }
    // Optional diagnostics; the existing Pi BM1 parser safely ignores BY1.
    const int size = snprintf(packet, sizeof(packet), "BY1 %lu %u %u %u %lu\n",
                              encoder.frames, lastPeriod, lastHigh, dropped, encoder.epoch);
    if (size > 0 && size < int(sizeof(packet)) && Serial.availableForWrite() >= size) {
      Serial.write(reinterpret_cast<const uint8_t *>(packet), size);
      lastDiagnostic = now;
    }
  }
}
