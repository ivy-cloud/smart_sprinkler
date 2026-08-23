
/*
  ESP32-S3 two-axis servo gimbal control.

  VOFA FireWater input:
    Y:<0-180>
    P:<0-180>
    M:<0-180>
    Y:<0-180>,P:<0-180>
    <0-180>,<0-180>

  Y controls yaw/pan, P controls pitch/tilt.
  M controls L298N motor speed, forward only.
*/

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>
// ===== BEGIN ADDED: fix Motor node MAC printing as 00:00:00:00:00:00 on ESP32-S3 =====
#include <esp_mac.h>
// ===== END ADDED =====
#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif
#include <ESP32Servo.h>

#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 2
#endif

const uint8_t PAN_SERVO_PIN = 17;
const uint8_t TILT_SERVO_PIN = 18;
const uint8_t MOTOR_PWM_PIN = 16;
const uint8_t MOTOR_DIR_PIN = 15;
const uint8_t PUMP_PIN = 14; // Change to your relay/MOSFET GPIO.

const bool SERVO_CENTER_TEST_MODE = false;

const uint32_t SERVO_PWM_FREQ_HZ = 50;
const uint32_t MOTOR_PWM_FREQ_HZ = 1000;
const uint8_t MOTOR_PWM_RES_BITS = 8;

const int SERVO_MIN_US = 1000;
const int SERVO_MAX_US = 2000;
const int MOTOR_MAX_DUTY = (1 << MOTOR_PWM_RES_BITS) - 1;

const int PAN_MIN_ANGLE = 0;
const int PAN_MAX_ANGLE = 180;
const int TILT_MIN_ANGLE = 0;
const int TILT_MAX_ANGLE = 180;

const int CENTER_ANGLE = 90;
const uint8_t SERIAL_FRAME_MAX_LEN = 48;
const uint32_t STATUS_PRINT_INTERVAL_MS = 80;

const int VOFA_MIN_VALUE = 0;
const int VOFA_MAX_VALUE = 180;
const int VOFA_CENTER_VALUE = 90;
const int VOFA_CENTER_DEADBAND = 2;
const int VOFA_CHANGE_THRESHOLD = 1;

int currentPanAngle = CENTER_ANGLE;
int currentTiltAngle = CENTER_ANGLE;
int currentYawValue = CENTER_ANGLE;
int currentPitchValue = CENTER_ANGLE;
int currentMotorValue = 0;
uint8_t currentPumpState = 0;

Servo panServo;
Servo tiltServo;

char serialFrame[SERIAL_FRAME_MAX_LEN + 1];
uint8_t serialFrameIndex = 0;
uint32_t lastStatusPrintMs = 0;

#pragma pack(push, 1)
struct MotorCommand {
  uint8_t device;
  uint8_t cmd;
  int servo_x;
  int servo_y;
  int motor_speed;
  uint8_t pump;
};
#pragma pack(pop)

static constexpr uint8_t MOTOR_DEVICE_ID = 1;
static constexpr uint8_t CMD_NONE = 0;
static constexpr uint8_t CMD_GIMBAL = 1;
static constexpr uint8_t CMD_MOTOR = 2;
static constexpr uint8_t CMD_PUMP = 3;

int angleToPulseUs(int angle) {
  return map(angle, 0, 180, SERVO_MIN_US, SERVO_MAX_US);
}

bool attachServo(Servo &servo, uint8_t pin) {
  servo.setPeriodHertz(SERVO_PWM_FREQ_HZ);
  return servo.attach(pin, SERVO_MIN_US, SERVO_MAX_US) != 0;
}

void writeServoAngle(Servo &servo, int angle) {
  servo.writeMicroseconds(angleToPulseUs(angle));
}

void setupMotorPwm() {
  pinMode(MOTOR_DIR_PIN, OUTPUT);
  digitalWrite(MOTOR_DIR_PIN, LOW);
  pinMode(PUMP_PIN, OUTPUT);
  digitalWrite(PUMP_PIN, LOW);

#if ESP_ARDUINO_VERSION_MAJOR >= 3
  analogWriteFrequency(MOTOR_PWM_PIN, MOTOR_PWM_FREQ_HZ);
  analogWriteResolution(MOTOR_PWM_PIN, MOTOR_PWM_RES_BITS);
#else
  analogWriteFrequency(MOTOR_PWM_FREQ_HZ);
  analogWriteResolution(MOTOR_PWM_RES_BITS);
#endif
  analogWrite(MOTOR_PWM_PIN, 0);
}

void writeMotorValue(int value) {
  currentMotorValue = constrain(value, VOFA_MIN_VALUE, VOFA_MAX_VALUE);
  const int duty = map(currentMotorValue, VOFA_MIN_VALUE, VOFA_MAX_VALUE, 0, MOTOR_MAX_DUTY);

  digitalWrite(MOTOR_DIR_PIN, LOW);
  analogWrite(MOTOR_PWM_PIN, duty);
}

void writePumpState(uint8_t state) {
  currentPumpState = state ? 1 : 0;
  digitalWrite(PUMP_PIN, currentPumpState ? HIGH : LOW);
}

void setGimbalTarget(int panAngle, int tiltAngle) {
  currentPanAngle = constrain(panAngle, PAN_MIN_ANGLE, PAN_MAX_ANGLE);
  currentTiltAngle = constrain(tiltAngle, TILT_MIN_ANGLE, TILT_MAX_ANGLE);
  writeServoAngle(panServo, currentPanAngle);
  writeServoAngle(tiltServo, currentTiltAngle);
}

void printHelp() {
  Serial.println();
  Serial.println("Two-axis servo gimbal ready.");
  Serial.println("ESP-NOW command receiver enabled.");
  Serial.println("VOFA FireWater input:");
  Serial.println("  Y:90");
  Serial.println("  P:90");
  Serial.println("  M:0");
  Serial.println("  pump:1");
  Serial.println("  Y:90,P:90");
  Serial.println("  90,90");
}

bool parseNamedChannel(const String &frame, char channelName, int &value) {
  const int nameIndex = frame.indexOf(channelName);
  if (nameIndex < 0) {
    return false;
  }

  int valueStart = nameIndex + 1;
  while (valueStart < frame.length()) {
    const char separator = frame.charAt(valueStart);
    if (separator == ':' || separator == '=' || separator == ' ') {
      valueStart++;
    } else {
      break;
    }
  }

  int valueEnd = valueStart;
  while (valueEnd < frame.length()) {
    const char c = frame.charAt(valueEnd);
    if ((c >= '0' && c <= '9') || c == '-' || c == '+') {
      valueEnd++;
    } else {
      break;
    }
  }

  if (valueEnd == valueStart) {
    return false;
  }

  value = frame.substring(valueStart, valueEnd).toInt();
  return true;
}

bool parseVofaFrame(String frame, int &yawValue, int &pitchValue) {
  frame.trim();
  frame.toUpperCase();

  if (frame.length() == 0) {
    return false;
  }

  const bool hasYaw = parseNamedChannel(frame, 'Y', yawValue);
  const bool hasPitch = parseNamedChannel(frame, 'P', pitchValue);
  if (hasYaw && hasPitch) {
    return true;
  }

  const int commaIndex = frame.indexOf(',');
  if (commaIndex > 0) {
    yawValue = frame.substring(0, commaIndex).toInt();
    pitchValue = frame.substring(commaIndex + 1).toInt();
    return true;
  }

  return false;
}

int vofaValueToAngle(int value, int minAngle, int maxAngle) {
  value = constrain(value, VOFA_MIN_VALUE, VOFA_MAX_VALUE);
  return map(value, VOFA_MIN_VALUE, VOFA_MAX_VALUE, minAngle, maxAngle);
}

int normalizeVofaValue(int value) {
  value = constrain(value, VOFA_MIN_VALUE, VOFA_MAX_VALUE);
  if (abs(value - VOFA_CENTER_VALUE) <= VOFA_CENTER_DEADBAND) {
    return VOFA_CENTER_VALUE;
  }
  return value;
}

bool isMeaningfulChange(int newValue, int oldValue) {
  return abs(newValue - oldValue) >= VOFA_CHANGE_THRESHOLD;
}

void printFireWaterFrame(int yawValue, int pitchValue, int motorValue) {
  const uint32_t now = millis();
  if (now - lastStatusPrintMs < STATUS_PRINT_INTERVAL_MS) {
    return;
  }
  lastStatusPrintMs = now;

  Serial.print("firewater:");
  Serial.print(yawValue);
  Serial.print(",");
  Serial.print(pitchValue);
  Serial.print(",");
  Serial.print(currentPanAngle);
  Serial.print(",");
  Serial.print(currentTiltAngle);
  Serial.print(",");
  Serial.print(motorValue);
  Serial.print(",");
  Serial.println(currentPumpState);
}

void applyMotorCommand(const MotorCommand &command) {
  if (command.device != MOTOR_DEVICE_ID || command.cmd == CMD_NONE) {
    return;
  }

  Serial.println("Received ESP-NOW MotorCommand:");
  Serial.print("  cmd=");
  Serial.println(command.cmd);
  Serial.print("  servo_x=");
  Serial.println(command.servo_x);
  Serial.print("  servo_y=");
  Serial.println(command.servo_y);
  Serial.print("  motor_speed=");
  Serial.println(command.motor_speed);
  Serial.print("  pump=");
  Serial.println(command.pump);

  if (command.cmd == CMD_GIMBAL) {
    currentYawValue = normalizeVofaValue(command.servo_x);
    currentPitchValue = normalizeVofaValue(command.servo_y);
    setGimbalTarget(
        vofaValueToAngle(currentYawValue, PAN_MIN_ANGLE, PAN_MAX_ANGLE),
        vofaValueToAngle(currentPitchValue, TILT_MIN_ANGLE, TILT_MAX_ANGLE));
  } else if (command.cmd == CMD_MOTOR) {
    writeMotorValue(command.motor_speed);
  } else if (command.cmd == CMD_PUMP) {
    writePumpState(command.pump);
  }

  printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
}

void onEspNowReceiveBytes(const uint8_t *data, int length) {
  if (length != sizeof(MotorCommand)) {
    return;
  }

  MotorCommand command = {};
  memcpy(&command, data, sizeof(command));
  applyMotorCommand(command);
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onEspNowReceive(const esp_now_recv_info_t *info, const uint8_t *data, int length) {
  (void)info;
  onEspNowReceiveBytes(data, length);
}
#else
void onEspNowReceive(const uint8_t *mac, const uint8_t *data, int length) {
  (void)mac;
  onEspNowReceiveBytes(data, length);
}
#endif

bool setupEspNow() {
  WiFi.mode(WIFI_STA);
  // ===== BEGIN ADDED: disconnect args + delay (MAC was 00:00:... if read too early) =====
  WiFi.disconnect(true, true);
  delay(200);
  // ===== END ADDED =====

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed.");
    return false;
  }

  if (esp_now_register_recv_cb(onEspNowReceive) != ESP_OK) {
    Serial.println("ESP-NOW receive callback registration failed.");
    return false;
  }

  // ===== BEGIN ADDED: print MAC via esp_read_mac (fixes 00:00:00:00:00:00 on ESP32-S3) =====
  uint8_t mac[6] = {};
  const esp_err_t macErr = esp_read_mac(mac, ESP_MAC_WIFI_STA);
  Serial.print("Motor node MAC: ");
  if (macErr == ESP_OK) {
    for (int i = 0; i < 6; ++i) {
      if (mac[i] < 0x10) {
        Serial.print('0');
      }
      Serial.print(mac[i], HEX);
      if (i < 5) {
        Serial.print(':');
      }
    }
    Serial.println();
  } else {
    delay(200);
    Serial.println(WiFi.macAddress());
  }
  // ===== END ADDED =====
  Serial.println("ESP-NOW motor receiver ready.");
  return true;
}

void handleVofaFrame(String frame) {
  int yawValue = 0;
  int pitchValue = 0;
  int motorValue = 0;

  if (parseVofaFrame(frame, yawValue, pitchValue)) {
    yawValue = normalizeVofaValue(yawValue);
    pitchValue = normalizeVofaValue(pitchValue);

    const bool yawChanged = isMeaningfulChange(yawValue, currentYawValue);
    const bool pitchChanged = isMeaningfulChange(pitchValue, currentPitchValue);
    if (!yawChanged && !pitchChanged) {
      return;
    }

    currentYawValue = yawValue;
    currentPitchValue = pitchValue;
    setGimbalTarget(
        vofaValueToAngle(currentYawValue, PAN_MIN_ANGLE, PAN_MAX_ANGLE),
        vofaValueToAngle(currentPitchValue, TILT_MIN_ANGLE, TILT_MAX_ANGLE));
    printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
    return;
  }

  frame.trim();
  frame.toUpperCase();

  if (parseNamedChannel(frame, 'Y', yawValue)) {
    yawValue = normalizeVofaValue(yawValue);
    if (!isMeaningfulChange(yawValue, currentYawValue)) {
      return;
    }
    currentYawValue = yawValue;
    currentPanAngle = vofaValueToAngle(currentYawValue, PAN_MIN_ANGLE, PAN_MAX_ANGLE);
    writeServoAngle(panServo, currentPanAngle);
    printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
    return;
  }

  if (parseNamedChannel(frame, 'P', pitchValue)) {
    pitchValue = normalizeVofaValue(pitchValue);
    if (!isMeaningfulChange(pitchValue, currentPitchValue)) {
      return;
    }
    currentPitchValue = pitchValue;
    currentTiltAngle = vofaValueToAngle(currentPitchValue, TILT_MIN_ANGLE, TILT_MAX_ANGLE);
    writeServoAngle(tiltServo, currentTiltAngle);
    printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
    return;
  }

  if (parseNamedChannel(frame, 'M', motorValue)) {
    motorValue = constrain(motorValue, VOFA_MIN_VALUE, VOFA_MAX_VALUE);
    if (!isMeaningfulChange(motorValue, currentMotorValue)) {
      return;
    }
    writeMotorValue(motorValue);
    printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
    return;
  }

  if (frame.startsWith("PUMP:") || frame.startsWith("PUMP=")) {
    const uint8_t pumpValue = frame.substring(5).toInt() ? 1 : 0;
    writePumpState(pumpValue);
    printFireWaterFrame(currentYawValue, currentPitchValue, currentMotorValue);
    return;
  }

  Serial.print("Invalid frame: ");
  Serial.println(frame);
  Serial.println("Use Y:90, P:90, M:90, or pump:1");
}

void pollSerialFrames() {
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      if (serialFrameIndex > 0) {
        serialFrame[serialFrameIndex] = '\0';
        handleVofaFrame(String(serialFrame));
        serialFrameIndex = 0;
      }
      continue;
    }

    if (serialFrameIndex < SERIAL_FRAME_MAX_LEN) {
      serialFrame[serialFrameIndex++] = c;
    } else {
      serialFrameIndex = 0;
      Serial.println("Serial frame too long.");
    }
  }
}

void setup() {
  Serial.begin(115200);
  setupMotorPwm();

  const bool panAttached = attachServo(panServo, PAN_SERVO_PIN);
  const bool tiltAttached = attachServo(tiltServo, TILT_SERVO_PIN);

  if (!panAttached || !tiltAttached) {
    Serial.println("Failed to attach servo PWM.");
    return;
  }

  writeServoAngle(panServo, currentPanAngle);
  writeServoAngle(tiltServo, currentTiltAngle);
  setupEspNow();

  if (SERVO_CENTER_TEST_MODE) {
    Serial.println("Servo center test mode: output fixed 1500us PWM only.");
    return;
  }

  printHelp();
}

void loop() {
  if (SERVO_CENTER_TEST_MODE) {
    return;
  }

  pollSerialFrames();
}
