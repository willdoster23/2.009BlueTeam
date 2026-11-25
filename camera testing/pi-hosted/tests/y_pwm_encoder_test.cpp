// c++ -std=c++11 -Wall -Wextra -Werror tests/y_pwm_encoder_test.cpp -o /tmp/y-pwm-test
#include <assert.h>
#include <stdint.h>
#include "../firmware/y_nano_r4/pwm_encoder.h"

static bool sample(PwmEncoder &encoder, int angle, uint32_t at) {
  constexpr uint32_t period = 44000;  // About 1091 PWM frames/s at 48 MHz.
  uint32_t high = (uint64_t(angle + 16) * period + 2059) / 4119;
  return encoder.capture(period, high, at);
}

int main() {
  PwmEncoder encoder;
  assert(sample(encoder, 4000, 1000));
  assert(encoder.counts == 0 && encoder.valid);
  assert(sample(encoder, 80, 2000));
  assert(encoder.counts == 176);  // Forward wrap through zero.
  assert(sample(encoder, 4000, 3000));
  assert(encoder.counts == 0);  // Reverse wrap through zero.
  for (int i = 1; i <= 4096; ++i) assert(sample(encoder, (4000 + i) % 4096, 3000 + i * 917));
  assert(encoder.counts == 4096);  // Whole revolution, every PWM frame consumed.
  encoder.counts = int64_t(1) << 33;
  assert(sample(encoder, 4010, 3000 + 4097 * 917));
  assert(encoder.counts == (int64_t(1) << 33) + 10);

  const uint32_t epoch = encoder.epoch;
  encoder.expire(encoder.lastFrameUs + 20001);
  assert(!encoder.valid && encoder.counts == 0 && encoder.epoch == epoch + 1);
  encoder.expire(encoder.lastFrameUs + 40001);
  assert(encoder.epoch == epoch + 1);  // A sustained loss only resets once.
  assert(sample(encoder, 2000, encoder.lastFrameUs + 50000));
  assert(encoder.counts == 0);
  assert(sample(encoder, 2100, encoder.lastFrameUs + 30000));
  assert(encoder.counts == 0 && encoder.epoch == epoch + 2);  // Gap without a loop call.

  PwmEncoder bad;
  assert(!bad.capture(0, 0, 0));
  assert(!bad.capture(44000, 44000, 0));
  assert(!bad.capture(20000, 10000, 0));
  assert(!bad.capture(70000, 10000, 0));
  assert(!sample(bad, -3, 1000));
  assert(!sample(bad, 4098, 2000));
  assert(sample(bad, 0, 3000));
  assert(sample(bad, 4095, 4000));
  assert(bad.counts == -1);

  PwmEncoder rollover;
  assert(sample(rollover, 100, UINT32_MAX - 1000));
  assert(sample(rollover, 120, 200));
  assert(rollover.counts == 20 && rollover.epoch == 65536);
  rollover.expire(20201);
  assert(!rollover.valid && rollover.epoch == 65537);

  PwmEncoder exhausted(48000000, 0x1ffff);
  assert(sample(exhausted, 100, 1000));
  exhausted.expire(22000);
  assert(exhausted.exhausted && !sample(exhausted, 100, 23000));
}
