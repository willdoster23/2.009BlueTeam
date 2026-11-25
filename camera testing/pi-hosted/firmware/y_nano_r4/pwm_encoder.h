#pragma once
#include <stdint.h>

// Shared by the sketch and host tests. Timestamps are unsigned micros(), so
// elapsed-time subtraction also works across the 32-bit timer rollover.
class PwmEncoder {
 public:
  static constexpr uint32_t SIGNAL_TIMEOUT_US = 20000;
  explicit PwmEncoder(uint32_t clockHz = 48000000, uint32_t bootEpoch = 65536)
      : epoch(bootEpoch), clockHz_(clockHz) {}

  void expire(uint32_t nowUs) {
    if (valid && uint32_t(nowUs - lastFrameUs) > SIGNAL_TIMEOUT_US) {
      valid = false;
      counts = 0;
      // Each boot reserves 65536 epoch values in EEPROM. Never reuse a tag if
      // that reservation is exhausted; a power cycle allocates another block.
      if ((epoch & 0xffffU) == 0xffffU) exhausted = true;
      else ++epoch;
    }
  }

  bool capture(uint32_t periodTicks, uint32_t highTicks, uint32_t nowUs) {
    expire(nowUs);
    if (exhausted || !clockHz_ || !periodTicks || highTicks >= periodTicks ||
        uint64_t(periodTicks) * 1000000 < uint64_t(clockHz_) * 787 ||
        uint64_t(periodTicks) * 1000000 > uint64_t(clockHz_) * 1275) return false;
    // AS5048A PWM: 16 leading clocks, 4096 angle clocks, 4119 total clocks.
    int32_t angle = int32_t((uint64_t(highTicks) * 4119 + periodTicks / 2) / periodTicks) - 16;
    if (angle < -2 || angle >= 4098) return false;
    if (angle < 0) angle = 0;
    if (angle > 4095) angle = 4095;
    if (valid) {
      int32_t delta = (angle - previousAngle_ + 6144) % 4096 - 2048;
      counts += delta;
    }
    previousAngle_ = angle;
    lastFrameUs = nowUs;
    valid = true;
    ++frames;
    return true;
  }

  bool valid = false;
  bool exhausted = false;
  int64_t counts = 0;
  uint32_t epoch;
  uint32_t frames = 0;
  uint32_t lastFrameUs = 0;

 private:
  uint32_t clockHz_;
  int32_t previousAngle_ = 0;
};
