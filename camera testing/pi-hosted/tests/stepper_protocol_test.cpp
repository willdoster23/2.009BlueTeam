#include <assert.h>
#include <stdio.h>
#define ARDUINO_AVR_NANO_EVERY 1
#define MILLIS_USE_TIMERB3 1
#define F_CPU 16000000UL
#include "../firmware/brickmapper_stepper/brickmapper_stepper.ino"
void send(const char* text) { char packet[120]; strcpy(packet,text); command(packet); }
void exactPulses(uint32_t rate) {
  PulseEngine pulses; pulses.ping(); assert(pulses.arm()); assert(pulses.begin(3200));
  pulses.rate=rate;
  uint32_t rises=0, highRun=0;
  bool previous=false;
  for (uint32_t tick=0; tick<200000 && (pulses.moving || pulses.high); ++tick) {
    if (tick%2000==0) pulses.ping();
    const bool high=pulses.tick();
    if (high) { if (!previous) ++rises; ++highRun; }
    else if (previous) { assert(highRun == 2); highRun = 0; }
    previous=high;
  }
  assert(pulses.reason==PulseEngine::DONE && pulses.position==3200 && rises==3200);
}
int main() {
  exactPulses(PulseEngine::MAX_RATE);
  if (IS_Y) exactPulses(5333333); // Required pulse rate at 200 RPM / 1600 pulses per rev.
  setup(); assert(!engine.armed && !engine.moving);
  const std::string identityText = std::string("SBH1 BRICKMAPPER_STEPPER ") + MOTOR_ROLE + " 1 STEP=9 DIR=8";
  assert(Serial.output.find(identityText) != std::string::npos);
  fakeMillis = 50; loop();
  assert(Serial.output.find(std::string("SB1 ") + MOTOR_ROLE + " 1 0 0 0 0 0 idle") != std::string::npos);
  send("ARM"); assert(!engine.armed); // Heartbeat required before arming.
  send("PING"); send("ARM"); send("MOVE 1 100 80000 400000");
  assert(engine.moving && pinLevels[8] == HIGH);
  send("STOP"); assert(!engine.moving && !engine.armed);
  send("PING"); send("ARM"); send("MOVE 2 -100 80000 400000");
  assert(engine.moving && pinLevels[8] == LOW);
  send("STOP"); send("PING"); send("ARM");
  send("MOVE 2 100 80000 400000"); assert(!engine.moving); // Duplicate ID.
  send("MOVE 3 2147483648 80000 400000"); assert(!engine.moving);
  send("MOVE 3 -2147483648 80000 400000"); assert(!engine.moving);
  char aboveRate[100];
  snprintf(aboveRate,sizeof(aboveRate),"MOVE 3 100 %lu 400000",static_cast<unsigned long>(PulseEngine::MAX_RATE+1));
  send(aboveRate); assert(!engine.moving);
  send("MOVE 3 2000001 80000 400000"); assert(engine.moving); // No old bench cap.
  VPORTB.OUT = 0x80; engine.rate = PulseEngine::MAX_RATE;
  for (int i=0; i<4; ++i) TCB2_INT_vect();
  assert(engine.position == 1 && (VPORTB.OUT & 0x80) != 0 && (VPORTB.OUT & PIN0_bm) != 0);
  TCB2_INT_vect(); assert((VPORTB.OUT & PIN0_bm) != 0);
  TCB2_INT_vect(); assert((VPORTB.OUT & PIN0_bm) == 0); // STEP high for 100 us.
  send("STOP"); assert((VPORTB.OUT & PIN0_bm) == 0);
  send("PING"); send("ARM");
  for (uint32_t i=0; i<PulseEngine::LINK_TICKS; ++i) TCB2_INT_vect();
  assert(!engine.armed && engine.reason == PulseEngine::LINK_LOST);
  send("ALARM 1");
  if (IS_Y) {
    assert(alarmMode == 1);
    VPORTA.IN = PIN0_bm;
    send("PING"); send("ARM"); assert(!engine.armed);
    VPORTA.IN = 0; send("ARM"); assert(engine.armed);
    VPORTA.IN = PIN0_bm;
    for (int i=0; i<200; ++i) TCB2_INT_vect();
    assert(!engine.armed && engine.reason == PulseEngine::ALARM);
  } else {
    assert(alarmMode == 0);
    VPORTA.IN = PIN0_bm; send("PING"); send("ARM"); assert(engine.armed);
  }
  VPORTA.IN = 0;
  send("STOP"); send("PING"); send("ARM");
  send("MOVE 4 80 80000 0");
  assert(engine.moving && fixedSpeed && engine.rate == 80000);
  const int32_t startPosition = engine.position;
  fakeMillis += 5; loop(); assert(engine.rate == 80000); // No ramp.
  for (int i=0; i<20002; ++i) {
    if (i%2000 == 0) send("PING");
    TCB2_INT_vect();
  }
  assert(engine.position == startPosition + 80 && !engine.moving);
  assert((VPORTB.OUT & PIN0_bm) == 0);
  send("STOP");
  printf("%c firmware passed: dedicated identity, D9/D8, reports, bounds, STOP, heartbeat and alarm behavior.\n",MOTOR_ROLE);
}
