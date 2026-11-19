// Nano Every: DRV8871 inputs D5/D6, 5 V encoder A/B D2/D3.
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>

constexpr byte IN1 = 5, IN2 = 6, ENCODER_A = 2, ENCODER_B = 3;
constexpr long MAX_MOVE_COUNTS = 2401; // 6 inches at 0.0634665 mm/count, rounded.
constexpr unsigned long LINK_TIMEOUT_MS = 450, STALL_MS = 700, MOVE_TIMEOUT_MS = 12000;
constexpr unsigned long STARTUP_MS = 150, BOOST_AFTER_MS = 450;
constexpr int APPROACH_PWM = 108, CRUISE_PWM = 210, STARTUP_PWM = 220, BOOST_PWM = 255;
volatile long encoderCount = 0;

long moveOrigin = 0, travel = 0, furthest = 0;
unsigned long moveId = 0, sequence = 0;
unsigned long lastPing = 0, began = 0, lastProgress = 0, lastReport = 0;
bool heardPing = false, referenceSet = false;
const char *state = "idle";
char line[64];
byte used = 0;
bool discard = false;

void countEdge() {
  encoderCount += digitalRead(ENCODER_B) == HIGH ? 1 : -1;
}

long readCount() {
  noInterrupts();
  const long value = encoderCount;
  interrupts();
  return value;
}

void stopMotor(const char *reason) {
  analogWrite(IN1, 0);
  analogWrite(IN2, 0);
  state = reason;
}

void command(char *message, unsigned long now) {
  if (strcmp(message, "PING") == 0) {
    heardPing = true;
    lastPing = now;
    return;
  }
  if (strcmp(message, "STOP") == 0) {
    stopMotor("stopped");
    return;
  }
  if (strncmp(message, "REFERENCE ", 10) == 0) {
    // Restore the previous encoder origin after an in-place firmware update.
    if (referenceSet || moveId || strcmp(state, "idle") != 0) return;
    char *tail;
    const long previous = strtol(message + 10, &tail, 10);
    if (tail == message + 10 || *tail != '\0' || previous < -100000000L || previous > 100000000L) return;
    noInterrupts();
    encoderCount = previous;
    interrupts();
    referenceSet = true;
    return;
  }
  if (strncmp(message, "MOVE ", 5) != 0 || !heardPing || now - lastPing >= LINK_TIMEOUT_MS
      || strcmp(state, "moving") == 0) return;
  char *end;
  const unsigned long id = strtoul(message + 5, &end, 10);
  if (!id || id == moveId || *end != ' ') return;
  char *tail;
  const long counts = strtol(end + 1, &tail, 10);
  if (*tail != '\0' || counts == 0 || counts < -MAX_MOVE_COUNTS || counts > MAX_MOVE_COUNTS) return;
  moveId = id;
  moveOrigin = readCount();
  travel = counts;
  furthest = 0;
  began = lastProgress = now;
  state = "moving";
}

void setup() {
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  stopMotor("idle");
  pinMode(ENCODER_A, INPUT_PULLUP);
  pinMode(ENCODER_B, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(ENCODER_A), countEdge, RISING);
  Serial.begin(115200);
}

void loop() {
  const unsigned long now = millis();
  for (byte processed = 0; processed < sizeof(line) && Serial.available(); ++processed) {
    const char ch = Serial.read();
    if (ch == '\n') {
      if (!discard) {
        line[used] = '\0';
        command(line, now);
      }
      used = 0;
      discard = false;
    } else if (ch != '\r' && !discard) {
      if (used < sizeof(line) - 1) line[used++] = ch;
      else discard = true;
    }
  }

  const long count = readCount();
  if (strcmp(state, "moving") == 0) {
    const int direction = travel > 0 ? 1 : -1;
    const long progress = direction * (count - moveOrigin);
    const long remaining = labs(travel) - progress;
    if (progress > furthest) {
      furthest = progress;
      lastProgress = now;
    }
    if (now - lastPing >= LINK_TIMEOUT_MS) stopMotor("link");
    else if (progress <= -4) stopMotor("reverse");
    else if (remaining <= 0) stopMotor("done");
    else if (now - lastProgress >= STALL_MS) stopMotor("stall");
    else if (now - began >= MOVE_TIMEOUT_MS) stopMotor("timeout");
    else {
      const int travelDuty = APPROACH_PWM + min(long(CRUISE_PWM - APPROACH_PWM), remaining / 2);
      const bool starting = furthest == 0 && now - began < STARTUP_MS;
      const bool needsBoost = now - lastProgress >= BOOST_AFTER_MS;
      const int duty = needsBoost ? BOOST_PWM : starting ? STARTUP_PWM : travelDuty;
      analogWrite(direction > 0 ? IN1 : IN2, duty);
    }
  }

  if (now - lastReport >= 50) {
    char report[62];
    const int size = snprintf(report, sizeof(report), "BM1 X %lu %ld %s %lu\n",
                              ++sequence, count, state, moveId);
    if (size > 0 && size < int(sizeof(report)) && Serial.availableForWrite() >= size) {
      Serial.write(reinterpret_cast<const uint8_t *>(report), size);
    }
    lastReport = now;
  }
}
