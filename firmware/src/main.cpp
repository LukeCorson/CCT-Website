// Computer-first CCT bridge. New source only; original CCT firmware is untouched.
// USB JSONL v1 owns experiment commands. S3 UART remains CRC-framed v1.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <M5Core2.h>
#include <Adafruit_BMP085.h>
#include <SPI.h>
#include <Wire.h>
#include "stable_uart.h"

namespace {
constexpr uint8_t HEATER = 0x40;
constexpr uint32_t HOST_TIMEOUT_MS = 3000, LINK_TIMEOUT_MS = 1500;
constexpr uint8_t ROUTE_COUNT = 7, REQUIRED_ROUTES = 5, MAX_STEPS = 8;
const char *ROUTE_NAMES[] = {"bypass", "column", "vent", "purge", "closed", "zero", "adsorption"};
const char *VALVE_NAMES[] = {"exhaust", "check", "bypass", "mfm_selector", "nitrogen", "compressed_air"};
struct Step { uint8_t mask; uint16_t wait; };
struct Route { Step steps[MAX_STEPS]; uint8_t count = 0; };
Route routes[ROUTE_COUNT];
HardwareSerial s3(2);
Uart::Parser parser;
Adafruit_BMP085 ambient;
char bootId[17], owner[33] = {}, pendingId[49] = {}, input[4096] = {};
size_t inputSize = 0;
bool droppingInput = false, configured = false, s3Known = false, outputActive = false;
bool thermocoupleOk = false, ambientOk = false, ambientStarted = false;
uint8_t desired = 0, actual = 0, safeVent = 0, sequence = 0, sentSequence = 0;
bool confirmed = false;
uint32_t lastS3 = 0, lastHost = 0, lastSend = 0, lastTelemetry = 0;
uint32_t lastTC = 0, lastAmbient = 0, stepAppliedMs = 0, transactionStart = 0;
float columnC = NAN, ambientC = NAN, pressureKpa = NAN, targetC = NAN, maxC = 175;
String routeName = "unknown", fault = "";
int8_t pendingRoute = -1;
uint8_t stepIndex = 0, heatStage = 0;
uint32_t heatHold = 0;
float heatPeak = 0, peakSamples[7] = {};
uint8_t peakCount = 0, peakIndex = 0;
struct Completed { char id[49] = {}; bool ok = false; };
Completed recent[16]; uint8_t recentIndex = 0;

bool linkReady() { return s3Known && millis() - lastS3 < LINK_TIMEOUT_MS; }

void envelope(JsonDocument &doc, const char *type) {
  doc["protocol"] = "cct-usb";
  doc["version"] = 1;
  doc["type"] = type;
  doc["boot_id"] = bootId;
}
void transmit(JsonDocument &doc) { serializeJson(doc, Serial); Serial.println(); }
void reply(const char *id, bool ok, const char *message, bool remember = true) {
  StaticJsonDocument<384> doc;
  envelope(doc, ok ? "ack" : "error");
  doc["id"] = id; doc["complete"] = true; doc["message"] = message;
  transmit(doc);
  if (remember && id[0]) {
    strlcpy(recent[recentIndex].id, id, sizeof(recent[recentIndex].id));
    recent[recentIndex].ok = ok;
    recentIndex = (recentIndex + 1) % 16;
  }
}
void event(const char *message) {
  StaticJsonDocument<320> doc; envelope(doc, "event"); doc["message"] = message; transmit(doc);
}
void cancelPending(const char *message) {
  if (pendingId[0]) reply(pendingId, false, message);
  pendingId[0] = 0; pendingRoute = -1;
}
void sendOutputs() {
  const uint8_t payload[] = {desired};
  sentSequence = sequence++;
  Uart::send(s3, Uart::Type::SetOutputs, sentSequence, payload, 1);
  lastSend = millis();
}
void setDesired(uint8_t value) {
  if (desired != value || !outputActive) confirmed = false;
  desired = value & 0x7F; outputActive = true; sendOutputs();
}
void stopHeating() { targetC = NAN; desired &= ~HEATER; heatStage = 0; }
void enterSafe(const char *why, bool latch) {
  cancelPending(why); stopHeating();
  // With a commissioned session, use the reviewed vent mask. Before any claim,
  // local STOP may only remove the heater bit from last electrical readback.
  if (configured) { setDesired(safeVent); routeName = "vent"; }
  else if (s3Known) setDesired(actual & ~HEATER);
  if (latch) fault = why;
  event(why);
}
void pollS3() {
  while (s3.available()) {
    Uart::Frame frame;
    if (parser.push(s3.read(), frame) != Uart::Result::Ready ||
        frame.type != Uart::Type::Status || frame.length != 12) continue;
    actual = frame.payload[0] & 0x7F;
    s3Known = true; lastS3 = millis();
    if (!outputActive) desired = actual;
    if (outputActive && frame.sequence == sentSequence) {
      confirmed = actual == desired && !frame.payload[1];
      if (actual != desired) enterSafe("S3 output readback mismatch", true);
    }
  }
}
void pollTC() {
  SPI.beginTransaction(SPISettings(4000000, MSBFIRST, SPI_MODE0));
  digitalWrite(26, LOW); delayMicroseconds(2);
  const uint16_t raw = SPI.transfer16(0);
  digitalWrite(26, HIGH); SPI.endTransaction();
  thermocoupleOk = raw != 0 && raw != 0xFFFF && !(raw & 4);
  columnC = thermocoupleOk ? ((raw >> 3) & 0xFFF) * 0.25f : NAN;
  if (!thermocoupleOk && isfinite(targetC)) enterSafe("Thermocouple fault", true);
  if (thermocoupleOk && columnC >= maxC && isfinite(targetC)) enterSafe("Maximum column temperature exceeded", true);
}
float peakMedian() {
  float sorted[7]; memcpy(sorted, peakSamples, sizeof(sorted));
  for (uint8_t i = 0; i < 6; ++i)
    for (uint8_t j = i + 1; j < 7; ++j)
      if (sorted[j] < sorted[i]) { float t = sorted[i]; sorted[i] = sorted[j]; sorted[j] = t; }
  return sorted[3];
}
void regulateHeater() {
  bool on = false;
  if (isfinite(targetC) && thermocoupleOk && linkReady() && owner[0] && !fault.length()) {
    // Preserve the established local heating profiles. 150 C uses 130 C ramp,
    // coast to a seven-sample median decline, then maintain 150 C.
    if (fabsf(targetC - 150.0f) < 0.1f) {
      if (heatStage == 0) {
        on = columnC < 130;
        if (!on) { heatStage = 1; heatPeak = columnC; peakCount = peakIndex = 0; }
      } else if (heatStage == 1) {
        peakSamples[peakIndex] = columnC; peakIndex = (peakIndex + 1) % 7;
        if (peakCount < 7) ++peakCount;
        if (columnC > heatPeak && columnC <= heatPeak + 5) heatPeak = columnC;
        if (peakCount == 7 && peakMedian() <= heatPeak - 2) heatStage = 2;
      } else on = columnC < targetC;
    } else {
      const float preheat = targetC - 15;
      if (heatStage == 0) {
        on = columnC < preheat;
        if (!on) { heatStage = 1; heatHold = millis(); }
      } else if (heatStage == 1) {
        on = columnC < preheat;
        if (millis() - heatHold >= 60000) heatStage = 2;
      } else on = columnC < targetC;
    }
  }
  const uint8_t updated = (desired & ~HEATER) | (on ? HEATER : 0);
  if (updated != desired) setDesired(updated);
}
void completePending() {
  if (!pendingId[0] || !confirmed) return;
  if (pendingRoute >= 0) {
    const Route &route = routes[pendingRoute];
    if (!stepAppliedMs) stepAppliedMs = millis();
    if (millis() - stepAppliedMs < route.steps[stepIndex].wait) return;
    if (++stepIndex < route.count) {
      stepAppliedMs = 0;
      setDesired(route.steps[stepIndex].mask | (desired & 0x70));
      return;
    }
    routeName = ROUTE_NAMES[pendingRoute]; pendingRoute = -1;
  }
  reply(pendingId, true, "Completed; S3 electrical output readback confirmed");
  pendingId[0] = 0;
}
bool parseRoutes(JsonObjectConst object, Route *candidate) {
  for (uint8_t i = 0; i < ROUTE_COUNT; ++i) {
    JsonArrayConst steps = object[ROUTE_NAMES[i]].as<JsonArrayConst>();
    if (i >= REQUIRED_ROUTES && (steps.isNull() || !steps.size())) { candidate[i].count = 0; continue; }
    if (steps.isNull() || steps.size() < 1 || steps.size() > MAX_STEPS) return false;
    candidate[i].count = steps.size();
    for (uint8_t j = 0; j < steps.size(); ++j) {
      if (!steps[j]["mask"].is<int>() || !steps[j]["wait_ms"].is<int>()) return false;
      const int mask = steps[j]["mask"], wait = steps[j]["wait_ms"];
      if (mask < 0 || mask > 15 || wait < 0 || wait > 10000) return false;
      candidate[i].steps[j] = {uint8_t(mask), uint16_t(wait)};
    }
    if ((i == 2 || i == 4) && (candidate[i].steps[candidate[i].count - 1].mask & 0x30)) return false;
  }
  return true;
}
void hello() {
  StaticJsonDocument<384> doc; envelope(doc, "hello");
  doc["firmware"] = "cct-core2-usb-1";
  doc["ownership_required"] = true; doc["host_timeout_ms"] = HOST_TIMEOUT_MS;
  transmit(doc);
}
void command(JsonObjectConst obj) {
  const char *id = obj["id"] | "";
  const char *op = obj["op"] | "";
  const char *session = obj["session"] | "";
  if (strcmp(obj["protocol"] | "", "cct-usb") || obj["version"] != 1 || strlen(id) < 1 || strlen(id) > 48) {
    reply(id, false, "Invalid protocol/version/command ID", false); return;
  }
  if (!strcmp(op, "hello")) { hello(); return; }
  if (!strcmp(op, "status")) { lastTelemetry = 0; return; }
  if (strlen(session) != 32) { reply(id, false, "Invalid session", false); return; }
  if (!strcmp(op, "claim")) {
    if (owner[0] && strcmp(owner, session)) { reply(id, false, "Another computer owns control", false); return; }
    if (owner[0]) { reply(id, true, "Session already owned", false); return; }
    if (!linkReady() || actual & HEATER) { reply(id, false, "S3 readback unavailable or heater already active", false); return; }
    Route candidate[ROUTE_COUNT];
    const float limit = obj["max_temperature_c"] | NAN;
    if (!obj["safe_vent_mask"].is<int>() || !isfinite(limit) || limit < 50 || limit > 175 ||
        !parseRoutes(obj["routes"].as<JsonObjectConst>(), candidate)) {
      reply(id, false, "Invalid commissioned route/safety configuration", false); return;
    }
    const int vent = obj["safe_vent_mask"];
    if (vent < 0 || vent > 15 || candidate[2].steps[candidate[2].count - 1].mask != vent) {
      reply(id, false, "Vent mask must close sources and match vent route", false); return;
    }
    memcpy(routes, candidate, sizeof(routes)); safeVent = vent; maxC = limit;
    configured = true; strlcpy(owner, session, sizeof(owner)); lastHost = millis();
    desired = actual & ~HEATER; fault = "";
    memset(recent, 0, sizeof(recent));
    reply(id, true, "Control claimed; no actuator command applied", false); return;
  }
  if (!owner[0] || strcmp(owner, session)) { reply(id, false, "Control ownership required", false); return; }
  if (!strcmp(op, "heartbeat")) { lastHost = millis(); return; }
  for (const Completed &old : recent) {
    if (!strcmp(id, old.id)) { reply(id, old.ok, "Duplicate ID; previous result, no repeated action", false); return; }
  }
  if (!strcmp(op, "release")) {
    enterSafe("Computer released control", false); owner[0] = 0;
    reply(id, true, "Released; local shutdown requested"); return;
  }
  const bool safety = !strcmp(op, "safe_state");
  if (!safety && fault.length()) { reply(id, false, fault.c_str()); return; }
  if (!linkReady()) { reply(id, false, "S3 link not ready"); return; }
  if (pendingId[0]) {
    if (!strcmp(id, pendingId)) return;
    if (safety) cancelPending("Superseded by safe state");
    else { reply(id, false, "Another command is still applying"); return; }
  }
  if (safety) {
    stopHeating(); routeName = "vent"; setDesired(safeVent);
  } else if (!strcmp(op, "route")) {
    const char *name = obj["name"] | "";
    int8_t index = -1;
    for (uint8_t i = 0; i < ROUTE_COUNT; ++i) if (!strcmp(name, ROUTE_NAMES[i])) index = i;
    if (index < 0) { reply(id, false, "Unknown named route"); return; }
    if (!routes[index].count) { reply(id, false, "Named route is not commissioned"); return; }
    pendingRoute = index; stepIndex = 0; stepAppliedMs = 0;
    setDesired(routes[index].steps[0].mask | (desired & 0x70));
    routeName = String(name) + " (applying)";
  } else if (!strcmp(op, "gas")) {
    const char *name = obj["name"] | "";
    uint8_t source = 0;
    if (!strcmp(name, "air")) source = 0x20;
    else if (!strcmp(name, "n2")) source = 0x10;
    else if (strcmp(name, "off")) { reply(id, false, "Unknown gas source"); return; }
    // Host stages gas changes: close both -> verified 146C/meter settings -> open.
    if (source && desired & 0x30) { reply(id, false, "Close sources before gas change"); return; }
    setDesired((desired & ~0x30) | source);
  } else if (!strcmp(op, "heater")) {
    if (!obj.containsKey("target_c")) { reply(id, false, "Missing heater target"); return; }
    if (obj["target_c"].isNull()) { stopHeating(); setDesired(desired & ~HEATER); }
    else {
      if (!obj["target_c"].is<float>() && !obj["target_c"].is<int>()) { reply(id, false, "Invalid heater target"); return; }
      const float target = obj["target_c"];
      if (!isfinite(target) || target < 50 || target >= maxC || !thermocoupleOk || columnC >= maxC) {
        reply(id, false, "Invalid heater target or thermocouple"); return;
      }
      targetC = target; heatStage = 0; peakCount = peakIndex = 0;
      regulateHeater(); setDesired(desired);
    }
  } else if (!strcmp(op, "valve")) {
    const char *name = obj["name"] | "";
    if (!obj["on"].is<bool>()) { reply(id, false, "Named valve requires an on/off state"); return; }
    int8_t bit = -1;
    for (uint8_t i = 0; i < 6; ++i) if (!strcmp(name, VALVE_NAMES[i])) bit = i;
    if (bit < 0) { reply(id, false, "Unknown named valve"); return; }
    const bool on = obj["on"];
    // Nitrogen and compressed air may never be opened together.
    if (on && ((bit == 4 && desired & 0x20) || (bit == 5 && desired & 0x10))) {
      reply(id, false, "Close the other source valve first"); return;
    }
    setDesired(on ? desired | (1U << bit) : desired & ~(1U << bit));
    routeName = "manual valves";
  } else { reply(id, false, "Unknown operation"); return; }
  strlcpy(pendingId, id, sizeof(pendingId)); transactionStart = millis();
}
void pollUSB() {
  // A bounded input budget prevents a noisy host from starving local control.
  for (uint16_t budget = 0; budget < 512 && Serial.available(); ++budget) {
    const char byte = Serial.read();
    if (byte == '\n') {
      if (!droppingInput && inputSize) {
        input[inputSize] = 0;
        DynamicJsonDocument doc(8192);
        const auto error = deserializeJson(doc, input, inputSize, DeserializationOption::NestingLimit(8));
        if (!error && doc.is<JsonObject>()) command(doc.as<JsonObjectConst>());
        else reply("", false, "Malformed JSON command", false);
      }
      inputSize = 0; droppingInput = false;
    } else if (byte != '\r' && !droppingInput) {
      if (inputSize >= sizeof(input) - 1) { inputSize = 0; droppingInput = true; }
      else input[inputSize++] = byte;
    }
  }
}
void telemetry() {
  StaticJsonDocument<1536> doc; envelope(doc, "status");
  doc["owner"] = owner; doc["fault"] = fault;
  JsonObject values = doc.createNestedObject("values");
  if (thermocoupleOk) values["column_temp_c"] = columnC; else values["column_temp_c"] = nullptr;
  if (ambientOk) { values["ambient_temp_c"] = ambientC; values["ambient_pressure_kpa"] = pressureKpa; }
  else { values["ambient_temp_c"] = nullptr; values["ambient_pressure_kpa"] = nullptr; }
  JsonObject ages = doc.createNestedObject("ages_ms");
  ages["column_temp_c"] = millis() - lastTC;
  ages["ambient_temp_c"] = millis() - lastAmbient;
  ages["ambient_pressure_kpa"] = millis() - lastAmbient;
  JsonObject out = doc.createNestedObject("outputs");
  out["route"] = routeName; out["heater_on"] = bool(actual & HEATER);
  out["source_valves"] = actual & 0x30;
  JsonObject valves = out.createNestedObject("valves");
  for (uint8_t i = 0; i < 6; ++i) valves[VALVE_NAMES[i]] = bool(actual & (1U << i));
  out["desired_mask"] = desired; out["applied_mask"] = actual;
  out["s3_online"] = linkReady(); out["pending"] = bool(pendingId[0]);
  if (isfinite(targetC)) out["heater_target_c"] = targetC; else out["heater_target_c"] = nullptr;
  transmit(doc);
}
void displayStatus() {
  M5.Lcd.fillScreen(fault.length() ? MAROON : NAVY);
  M5.Lcd.setTextSize(2); M5.Lcd.setTextColor(WHITE); M5.Lcd.setCursor(12, 16);
  M5.Lcd.println("CCT computer bridge"); M5.Lcd.println();
  M5.Lcd.printf("Column: %.1f C\n", columnC);
  M5.Lcd.printf("Computer: %s\n", owner[0] ? "connected" : "read only");
  M5.Lcd.printf("S3: %s\n", linkReady() ? "online" : "OFFLINE");
  M5.Lcd.println(routeName); M5.Lcd.println(fault);
  M5.Lcd.setCursor(20, 210); M5.Lcd.println("A / touch bottom: STOP");
}
}

void setup() {
  M5.begin(true, false, true, false); Serial.begin(115200);
  snprintf(bootId, sizeof(bootId), "%08lx%08lx", (unsigned long)esp_random(), (unsigned long)esp_random());
  s3.begin(115200, SERIAL_8N1, 13, 14);
  pinMode(26, OUTPUT); digitalWrite(26, HIGH);
  SPI.begin(18, 38, 23, 26); Wire.begin(32, 33);
  ambientStarted = ambient.begin(BMP085_STANDARD, &Wire);
  // Start with read-only S3 PINGs; never overwrite valves on USB/boot connection.
  hello(); displayStatus();
}

void loop() {
  const uint32_t now = millis(); M5.update(); pollS3(); pollUSB();
  if (M5.BtnA.wasPressed() || M5.BtnC.wasPressed()) {
    enterSafe("Local STOP", true); owner[0] = 0;
  }
  if (now - lastTC >= 250) { lastTC = now; pollTC(); regulateHeater(); }
  if (now - lastAmbient >= 1500) {
    lastAmbient = now;
    if (!ambientStarted) ambientStarted = ambient.begin(BMP085_STANDARD, &Wire);
    if (ambientStarted) {
      ambientC = ambient.readTemperature(); pressureKpa = ambient.readPressure() / 1000.0f;
      ambientOk = isfinite(ambientC) && pressureKpa > 10 && pressureKpa < 120;
    }
  }
  if (owner[0] && now - lastHost > HOST_TIMEOUT_MS) {
    enterSafe("Computer heartbeat lost", true); owner[0] = 0;
  }
  if (outputActive && !linkReady() && (desired & HEATER || isfinite(targetC))) enterSafe("Core2–S3 link lost", true);
  if (pendingId[0] && now - transactionStart > 85000) enterSafe("Output transaction timed out", true);
  completePending();
  if (now - lastSend >= 500) {
    if (outputActive) sendOutputs();
    else { Uart::send(s3, Uart::Type::Ping, sequence++, nullptr, 0); lastSend = now; }
  }
  if (now - lastTelemetry >= 500) { lastTelemetry = now; telemetry(); }
  static uint32_t lastDraw = 0;
  if (now - lastDraw >= 1000) { lastDraw = now; displayStatus(); }
}
