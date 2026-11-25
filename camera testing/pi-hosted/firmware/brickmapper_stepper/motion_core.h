#pragma once
#include <stdint.h>
#include <limits.h>

// Shared with host-side tests. One tick is 50 us; STEP is high for two ticks.
class PulseEngine {
 public:
  static constexpr uint32_t TICK_HZ = 20000;
  // Y needs 5333.334 pulses/s for 200 RPM at 1600 pulses/revolution.
  static constexpr uint32_t MAX_RATE = BRICKMAPPER_MOTOR_AXIS == 2 ? 6000000 : 5000000;
  static constexpr uint32_t LINK_TICKS = TICK_HZ * 8 / 10;
  enum Reason : uint8_t { IDLE, RUNNING, DONE, STOPPED, LINK_LOST, ALARM };
  bool armed = false, moving = false, high = false;
  int32_t position = 0;
  uint32_t remaining = 0, rate = 0, heartbeat = LINK_TICKS;
  Reason reason = IDLE;

  void ping() { heartbeat = 0; }
  bool arm() {
    if (moving || heartbeat >= LINK_TICKS) return false;
    armed = true;
    reason = IDLE;
    return true;
  }
  bool begin(int32_t steps) {
    if (!armed || moving || !steps || steps == INT32_MIN ||
        heartbeat >= LINK_TICKS ||
        (int64_t(position) + steps > INT32_MAX) ||
        (int64_t(position) + steps < INT32_MIN)) return false;
    direction_ = steps > 0 ? 1 : -1;
    remaining = steps > 0 ? uint32_t(steps) : uint32_t(-steps);
    rate = phase_ = 0;
    highTicks_ = 0;
    high = false;
    moving = true;
    reason = RUNNING;
    return true;
  }
  void stop(Reason why = STOPPED) {
    moving = armed = high = false;
    remaining = rate = phase_ = 0;
    highTicks_ = 0;
    reason = why;
  }
  bool tick() {
    high = highTicks_ != 0;
    if (highTicks_) --highTicks_;
    if (heartbeat < LINK_TICKS) ++heartbeat;
    if (armed && heartbeat >= LINK_TICKS) stop(LINK_LOST);
    if (!moving) return high;
    phase_ += rate;
    if (phase_ >= TICK_HZ * 1000UL) {
      phase_ -= TICK_HZ * 1000UL;
      high = true;
      highTicks_ = 1;
      position += direction_;
      if (--remaining == 0) {
        moving = false;
        rate = 0;
        reason = DONE;
      }
    }
    return high;
  }
 private:
  uint32_t phase_ = 0;
  int8_t direction_ = 1;
  uint8_t highTicks_ = 0;
};

inline uint32_t rampRate(uint32_t rate, uint32_t limit, uint32_t acceleration,
                         uint32_t remaining, uint32_t dtMs) {
  if (!remaining) return 0;
  // rate is milli-step/s; acceleration is milli-step/s^2.
  const uint64_t denominator = uint64_t(2) * acceleration * 1000;
  const uint32_t stopping = denominator ?
      uint32_t((uint64_t(rate) * rate + denominator - 1) / denominator) : UINT32_MAX;
  uint32_t change = uint32_t(uint64_t(acceleration) * dtMs / 1000);
  if (!change) change = 1;
  if (remaining <= stopping + 1 && rate > 10000) {
    return rate > change + 10000 ? rate - change : 10000;
  }
  if (rate >= limit) return limit;
  return change >= limit - rate ? limit : rate + change;
}
