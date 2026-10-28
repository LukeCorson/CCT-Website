#include <Arduino.h>

// Direct USB replacement for the supplied Core-to-relay firmware.
// The web workstation sends:  A5 state (A5 XOR state)
// The controller replies:     5A applied counter (5A XOR applied XOR counter)

constexpr byte RELAY_COUNT = 6;
constexpr byte COMMAND_HEADER = 0xA5;
constexpr byte REPLY_HEADER = 0x5A;
constexpr unsigned long USB_BAUD = 115200;
constexpr unsigned long HOST_TIMEOUT_MS = 2500;

// Supplied relay mapping:
// Valve 6 = D2 = bit 0
// Valve 5 = D3 = bit 1
// Valve 4 = D4 = bit 2
// Valve 3 = D5 = bit 3
// Valve 2 = D8 = bit 4
// Valve 1 = D7 = bit 5
const byte RELAY_PINS[RELAY_COUNT] = {2, 3, 4, 5, 8, 7};

byte appliedRelayState = 0;
byte replyCounter = 0;
byte parserIndex = 0;
byte commandState = 0;
bool hostSessionActive = false;
unsigned long lastCommandMs = 0;
unsigned long lastBlinkMs = 0;

void applyRelayState(byte state) {
  state &= 0x3F;
  for (byte i = 0; i < RELAY_COUNT; i++) {
    digitalWrite(RELAY_PINS[i], state & (1U << i) ? HIGH : LOW);
  }
  appliedRelayState = state;
}

void sendReply() {
  replyCounter++;
  const byte reply[4] = {
    REPLY_HEADER,
    appliedRelayState,
    replyCounter,
    static_cast<byte>(REPLY_HEADER ^ appliedRelayState ^ replyCounter)
  };
  Serial.write(reply, sizeof(reply));
}

void processUsbByte(byte value) {
  if (parserIndex == 0) {
    if (value == COMMAND_HEADER) parserIndex = 1;
    return;
  }
  if (parserIndex == 1) {
    commandState = value & 0x3F;
    parserIndex = 2;
    return;
  }
  const byte expectedChecksum = COMMAND_HEADER ^ commandState;
  parserIndex = 0;
  if (value != expectedChecksum) return;

  applyRelayState(commandState);
  hostSessionActive = true;
  lastCommandMs = millis();
  digitalWrite(LED_BUILTIN, HIGH);
  sendReply();
}

void setup() {
  Serial.begin(USB_BAUD);
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  for (byte i = 0; i < RELAY_COUNT; i++) {
    pinMode(RELAY_PINS[i], OUTPUT);
    digitalWrite(RELAY_PINS[i], LOW);
  }
  appliedRelayState = 0;
}

void loop() {
  while (Serial.available()) processUsbByte(Serial.read());

  const unsigned long now = millis();
  if (hostSessionActive && now - lastCommandMs >= HOST_TIMEOUT_MS) {
    applyRelayState(0); // Host heartbeat lost: every relay off.
    hostSessionActive = false;
    digitalWrite(LED_BUILTIN, LOW);
  }

  if (!hostSessionActive && now - lastBlinkMs >= 500) {
    lastBlinkMs = now;
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }
}
