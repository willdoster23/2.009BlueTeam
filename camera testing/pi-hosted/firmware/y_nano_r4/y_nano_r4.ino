// Nano R4 Y encoder only. AS5048A PWM -> level buffer -> D2 (P105 / GPT1A).
// Encoder stays on 3.3 V; R4 D2 requires a guaranteed 5 V logic-level signal.
// USB-C connects to the roof Pi. No motor, winch, relay or tapper outputs.
#include <Arduino.h>
#include <EEPROM.h>
#include <FspTimer.h>
#include "pwm_encoder.h"

#ifndef ARDUINO_NANO_R4
#error Select Arduino Nano R4 (arduino:renesas_uno:nanor4).
#endif

constexpr uint8_t ENCODER_PIN = D2;
constexpr uint8_t CAPTURE_TIMER = 1;  // D2 is GPT1 GTIOCA on Nano R4.
constexpr uint32_t REPORT_US = 10000;
FspTimer captureTimer;
PwmEncoder encoder;
bool captureReady = false;
uint32_t sequence = 0, lastReportUs = 0;

void capturePulse(timer_callback_args_t *args) {
  if (args->event != TIMER_EVENT_CAPTURE_B) return;
  // The timer captures period on rising edges and clears itself on the same
  // edge. Falling edges capture high time. Neither measurement uses ISR timing.
  encoder.capture(R_GPT1->GTCCR[0], args->capture, micros());
}

bool reserveEpoch(uint32_t &epoch) {
  // Reserve an entire tag range per boot. A restart or signal loss must make
  // the roof discard the previous zero, even if position happens to be zero.
  uint32_t boots;
  EEPROM.get(0, boots);
  boots = boots == UINT32_MAX ? 1 : boots + 1;
  if (!boots || boots > 0xffffU) return false;
  EEPROM.put(0, boots);
  uint32_t saved;
  EEPROM.get(0, saved);
  if (saved != boots) return false;
  epoch = boots << 16;
  return true;
}

bool startCapture() {
  uint32_t epoch;
  if (!reserveEpoch(epoch)) return false;
  encoder = PwmEncoder(R_FSP_SystemClockHzGet(FSP_PRIV_CLOCK_PCLKD), epoch);
  pinMode(ENCODER_PIN, INPUT);  // No 5 V pull-up into the 3.3 V encoder.
  const auto rising = gpt_source_t(GPT_SOURCE_GTIOCA_RISING_WHILE_GTIOCB_LOW |
                                  GPT_SOURCE_GTIOCA_RISING_WHILE_GTIOCB_HIGH);
  const auto falling = gpt_source_t(GPT_SOURCE_GTIOCA_FALLING_WHILE_GTIOCB_LOW |
                                   GPT_SOURCE_GTIOCA_FALLING_WHILE_GTIOCB_HIGH);
  FspTimer::force_use_of_pwm_reserved_timer();
  if (!captureTimer.begin(TIMER_MODE_PERIODIC, GPT_TIMER, CAPTURE_TIMER,
                          UINT32_MAX, uint32_t(0), TIMER_SOURCE_DIV_1, capturePulse) ||
      !captureTimer.set_source_capture_a(rising) ||
      !captureTimer.set_source_clear(rising) ||
      !captureTimer.set_source_capture_b(falling) ||
      !captureTimer.setup_capture_b_irq(2) || !captureTimer.open()) return false;
  if (R_IOPORT_PinCfg(&g_ioport_ctrl, g_pin_cfg[ENCODER_PIN].pin,
                     IOPORT_CFG_PERIPHERAL_PIN | IOPORT_PERIPHERAL_GPT1) != FSP_SUCCESS) return false;
  return captureTimer.start();
}

void countText(int64_t value, char *result) {
  // Do not depend on the reduced newlib printf supporting %lld.
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
  captureReady = startCapture();
  lastReportUs = micros();
}

void loop() {
  const uint32_t now = micros();
  if (uint32_t(now - lastReportUs) < REPORT_US) return;
  lastReportUs = now;  // Skip a delayed slot rather than burst stale packets.
  const uint32_t mask = __get_PRIMASK();
  __disable_irq();
  // Sample time after masking: an ISR may have recorded a frame after 'now'.
  encoder.expire(micros());
  const bool valid = captureReady && encoder.valid;
  const int64_t counts = valid ? encoder.counts : 0;
  const uint32_t epoch = encoder.epoch;
  __set_PRIMASK(mask);
  char count[24], packet[96];
  countText(counts, count);
  const int size = snprintf(packet, sizeof(packet), "BM1 Y %lu %s %s %lu\n",
                            static_cast<unsigned long>(++sequence), count,
                            valid ? "idle" : "signal", static_cast<unsigned long>(epoch));
  digitalWrite(LED_BUILTIN, valid || (millis() / 250) % 2);
  if (Serial && size > 0 && size < int(sizeof(packet)) && Serial.availableForWrite() >= size)
    Serial.write(reinterpret_cast<const uint8_t *>(packet), size);
}
