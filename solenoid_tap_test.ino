const byte DRIVER_PIN = 9;

// Adjustable settings
const unsigned long PULSE_MS = 50;
const float TAP_HZ = 2;

unsigned long periodMs;
unsigned long lastStart = 0;
unsigned long pulseStart = 0;

bool running = false;
bool pulseOn = false;
bool firstTap = true;

void stopTapping() {
  digitalWrite(DRIVER_PIN, LOW);
  pulseOn = false;
  running = false;
}

void setup() {
  digitalWrite(DRIVER_PIN, LOW);
  pinMode(DRIVER_PIN, OUTPUT);
  Serial.begin(115200);

  if (TAP_HZ <= 0) {
    Serial.println("Error: TAP_HZ must be positive.");
    return;
  }

  float requestedPeriod = 1000.0 / TAP_HZ;

  if (requestedPeriod <= PULSE_MS) {
    Serial.println("Error: frequency too high for pulse duration.");
    return;
  }

  periodMs = (unsigned long)(requestedPeriod + 0.5);
  Serial.println("Ready: s = start, x = stop.");
}

void loop() {
  while (Serial.available() > 0) {
    char command = Serial.read();

    if (command == 'x' || command == 'X') {
      stopTapping();
      Serial.println("Stopped.");
    }

    if ((command == 's' || command == 'S') &&
        periodMs > PULSE_MS && !running) {
      running = true;
      firstTap = true;
      Serial.println("Tapping started.");
    }
  }

  unsigned long now = millis();

  if (pulseOn && now - pulseStart >= PULSE_MS) {
    digitalWrite(DRIVER_PIN, LOW);
    pulseOn = false;
  }

  if (running && !pulseOn &&
      (firstTap || now - lastStart >= periodMs)) {
    digitalWrite(DRIVER_PIN, HIGH);
    pulseOn = true;
    pulseStart = now;
    lastStart = now;
    firstTap = false;
  }
}