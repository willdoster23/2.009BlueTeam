#include <assert.h>
#include <stdint.h>
#include "../firmware/y_nano_every/pwm_encoder.h"

static bool sample(PwmEncoder &encoder, int32_t angle, uint32_t at) {
  const uint32_t period = 14667;  // 16 MHz / approximately 1091 Hz.
  const uint32_t high = (uint64_t(angle + 16) * period + 2059) / 4119;
  return encoder.capture(period, high, at);
}

int main() {
  PwmEncoder encoder(16000000);
  assert(sample(encoder, 4095, 1000));
  assert(sample(encoder, 0, 1917));
  assert(encoder.counts == 1);
  assert(sample(encoder, 4095, 2834));
  assert(encoder.counts == 0);
  uint32_t now = 2834;
  for (int32_t i = 1; i <= 50000; ++i) {
    now += 917;
    assert(sample(encoder, (4095 + i * 137) % 4096, now));
    assert(encoder.counts == int64_t(i) * 137);
  }
  const uint32_t tag = encoder.epoch;
  encoder.invalidate();  // Queue overflow must invalidate the old zero.
  assert(!encoder.valid && encoder.counts == 0 && encoder.epoch == tag + 1);
  assert(sample(encoder, 30, now + 917));
  assert(encoder.counts == 0);
  encoder.expire(now + 20918);
  assert(!encoder.valid && encoder.epoch == tag + 2);
  assert(!encoder.capture(0, 0, now + 21000));
  assert(!encoder.capture(14667, 14667, now + 21001));
  assert(!encoder.capture(5000, 2000, now + 21002));
  assert(!encoder.capture(30000, 2000, now + 21003));
  assert(sample(encoder, 30, UINT32_MAX - 500));
  assert(sample(encoder, 20, 416));
  assert(encoder.valid && encoder.counts == -10);
}
