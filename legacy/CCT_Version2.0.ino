#include <max6675.h>
#include <LiquidCrystal_I2C.h>
#include <Wire.h>

MAX6675 tc(4, 5, 6);
LiquidCrystal_I2C lcd(0x27, 16, 2);

int heatStage = 3;
unsigned long holdStart = 0;
unsigned long holdStart2 = 0;

const unsigned long HOLD_5_MIN_MS = 300000UL;
const unsigned long HOLD_2_MIN_MS = 120000UL;

const int controlBtnPin = 53;
const int desorbBtnPin = 51;
const int zeroBtnPin = 50;
const int adsorbBtnPin = 52;
const int breakBtnPin = 49;
const int switchPin = 48;

double tempC = 0;
bool lastSw = LOW;

void runControl();
void runDesorption();
void runAdsorption();
void runZero();
void runBreak();
void temp();
void heater();

void setup() {
  Serial.begin(9600);
  lcd.init();
  lcd.backlight();
  pinMode(2, OUTPUT);
  pinMode(23, OUTPUT); // gas valve
  pinMode(25, OUTPUT); // zero valve
  pinMode(27, OUTPUT); // flush valve
  pinMode(29, OUTPUT); // carrier valve
  pinMode(31, OUTPUT); // release valve
  pinMode(controlBtnPin, INPUT_PULLUP);
  pinMode(desorbBtnPin, INPUT_PULLUP);
  pinMode(adsorbBtnPin, INPUT_PULLUP);
  pinMode(zeroBtnPin, INPUT_PULLUP);
  pinMode(breakBtnPin, INPUT_PULLUP);
  pinMode(switchPin, INPUT_PULLUP);
}

void loop() {
  temp();
  heater();
  if (digitalRead(controlBtnPin) == LOW) {
    lcd.setCursor(7, 0); lcd.print("Idle     "); runControl();
  }
  if (digitalRead(desorbBtnPin) == LOW) {
    lcd.setCursor(7, 0); lcd.print(" Desorb"); runDesorption();
  }
  if (digitalRead(adsorbBtnPin) == LOW) {
    lcd.setCursor(7, 0); lcd.print(" Adsorb"); runAdsorption();
  }
  if (digitalRead(zeroBtnPin) == LOW) {
    lcd.setCursor(7, 0); lcd.print(" Zero      "); runZero();
  }
  if (digitalRead(breakBtnPin) == LOW) {
    lcd.setCursor(7, 0); lcd.print(" Break      "); runBreak();
  }
  bool sw = digitalRead(switchPin);
  if (lastSw == HIGH && sw == LOW) {
    Serial.println("Switch turned ON"); heatStage = 0;
  }
  if (lastSw == LOW && sw == HIGH) {
    Serial.println("Switch turned OFF"); heatStage = 3;
  }
  lastSw = sw;
}

void runZero() {
  digitalWrite(27, LOW);
  Serial.print("working");
  digitalWrite(29, LOW); delay(3000);
  digitalWrite(23, LOW); delay(1000);
  digitalWrite(31, LOW); delay(1000);
  digitalWrite(25, LOW); delay(1000);
  digitalWrite(25, HIGH); delay(1000);
  digitalWrite(31, HIGH); delay(2000);
  digitalWrite(27, HIGH);
}

void runControl() {
  digitalWrite(27, LOW);
  digitalWrite(29, LOW); delay(3000);
  digitalWrite(23, LOW); delay(1000);
  digitalWrite(31, LOW); delay(1000);
  digitalWrite(25, LOW);
}

void runAdsorption() {
  digitalWrite(27, LOW);
  Serial.print("working");
  digitalWrite(29, LOW); delay(3000);
  digitalWrite(23, LOW); delay(1000);
  digitalWrite(31, LOW); delay(1000);
  digitalWrite(25, LOW); delay(1000);
  digitalWrite(23, HIGH); delay(200);
}

void runDesorption() {
  digitalWrite(27, LOW);
  digitalWrite(29, LOW); delay(3000);
  digitalWrite(23, LOW); delay(1000);
  digitalWrite(31, LOW); delay(1000);
  digitalWrite(25, LOW); delay(1000);
  digitalWrite(31, HIGH); delay(2000);
  digitalWrite(29, HIGH);
}

void runBreak() {
  digitalWrite(27, LOW);
  digitalWrite(29, LOW); delay(3000);
  digitalWrite(23, LOW); delay(1000);
  digitalWrite(31, LOW); delay(1000);
  digitalWrite(23, HIGH); delay(1000);
  digitalWrite(31, HIGH); delay(200);
}

void temp() {
  tempC = tc.readCelsius();
  delay(250);
  lcd.setCursor(0, 0);
  if (heatStage == 0) lcd.print("PreHeat:");
  else if (heatStage == 1) lcd.print("Waiting:");
  else if (heatStage == 2) lcd.print("->140C :");
  else if (heatStage == 4) lcd.print("130C :");
  else lcd.print("       ");
  lcd.setCursor(0, 1); lcd.print("Temp C:       ");
  lcd.setCursor(8, 1); lcd.print(tempC, 1);
}

void heater() {
  if (heatStage == 0) {
    if (tempC < 115) digitalWrite(2, HIGH);
    else { digitalWrite(2, LOW); heatStage = 1; holdStart = millis(); }
  } else if (heatStage == 1) {
    if (tempC < 115) digitalWrite(2, HIGH);
    else digitalWrite(2, LOW);
    if (millis() - holdStart >= HOLD_5_MIN_MS) heatStage = 2;
    if (tempC > 140) heatStage = 2;
  } else if (heatStage == 2) {
    if (tempC < 140) digitalWrite(2, HIGH);
    else { digitalWrite(2, LOW); heatStage = 4; }
  } else if (heatStage == 4) {
    if (tempC < 130) digitalWrite(2, HIGH);
    else digitalWrite(2, LOW);
  } else if (heatStage == 3) {
    digitalWrite(2, LOW);
  }
}
