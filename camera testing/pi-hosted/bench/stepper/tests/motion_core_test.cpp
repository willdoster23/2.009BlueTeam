#include <assert.h>
#include <stdio.h>
#include "../../../firmware/winch_nano_every_bench/motion_core.h"

void exactMove(int32_t target, uint32_t speed) {
  PulseEngine p;
  assert(!p.begin(target));
  p.ping(); assert(p.arm()); assert(p.begin(target));
  p.rate = speed;
  uint32_t edges = 0;
  bool previous = false;
  const uint64_t pulses = target < 0 ? -int64_t(target) : target;
  const uint64_t maxTicks = pulses * PulseEngine::TICK_HZ * 1000 / speed + 2 * PulseEngine::TICK_HZ;
  for (uint64_t tick = 0; tick < maxTicks && (p.moving || p.high); ++tick) {
    if (tick % 2000 == 0) p.ping();
    bool high = p.tick();
    assert(!(high && previous)); // A whole tick LOW between STEP highs.
    if (high) ++edges;
    previous = high;
  }
  assert(p.reason == PulseEngine::DONE);
  assert(p.position == target);
  assert(edges == uint32_t(target < 0 ? -target : target));
  for (int i=0;i<1000;++i) assert(!p.tick());
}

int main() {
  exactMove(1000, 900000);
  exactMove(-1000, 5000000);
  exactMove(1, 100000);
  exactMove(2000001, 5000000);
  exactMove(-2000001, 5000000);
  exactMove(610, 10000); // 61 seconds: completes past the former duration cutoff.
  PulseEngine p;
  p.ping(); assert(p.arm()); assert(!p.begin(INT32_MIN));
  assert(!p.begin(0));
  assert(p.begin(INT32_MAX)); assert(p.remaining==uint32_t(INT32_MAX)); p.stop();
  p.ping(); assert(p.arm()); assert(p.begin(-INT32_MAX)); p.stop();
  p.ping(); assert(p.arm());
  assert(p.begin(100000)); p.rate=1000000;
  assert(!p.begin(100));
  for(uint32_t i=0;i<PulseEngine::LINK_TICKS;++i) p.tick();
  assert(!p.armed && !p.moving && !p.high && p.reason==PulseEngine::LINK_LOST);
  assert(!p.arm()); p.ping(); assert(p.arm());
  assert(p.begin(-100)); p.rate=1000000; p.tick(); p.stop();
  assert(!p.tick() && !p.moving && !p.armed);
  p.position=INT32_MAX; p.ping(); assert(p.arm()); assert(!p.begin(1));
  p.position=INT32_MIN; assert(!p.begin(-1));
  p.position=0; assert(p.begin(1000)); p.rate=1000;
  for(uint32_t i=0;i<PulseEngine::TICK_HZ*70;++i) {
    if(i%2000==0)p.ping();
    p.tick();
  }
  assert(p.reason==PulseEngine::RUNNING && p.moving && p.armed && p.position==70);
  p.stop(); assert(!p.tick() && !p.moving && !p.armed && !p.high);
  uint32_t rate=0;
  for(int i=0;i<1000;++i)rate=rampRate(rate,1000000,1000000,100000,5);
  assert(rate==1000000);
  assert(rampRate(rate,1000000,1000000,1,5)<rate);
  assert(rampRate(rate,1000000,1000000,0,5)==0);
  // Exercise an entire ramped finite move without overshoot.
  PulseEngine q; q.ping(); assert(q.arm()); assert(q.begin(1600));
  uint32_t rises=0;
  for(uint32_t t=0;t<400000 && q.moving;++t) {
    if(t%2000==0)q.ping();
    if(t%100==0)q.rate=rampRate(q.rate,800000,1200000,q.remaining,5);
    if(q.tick())++rises;
  }
  assert(q.reason==PulseEngine::DONE && q.position==1600 && rises==1600);
  puts("Motion tests passed: long moves beyond former caps, exact pulses, reverse moves, pulse width, stop, link timeout, counter bounds, ramp.");
}
