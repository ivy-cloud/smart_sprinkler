/*
  ESP32-S3 smart garden gateway.

  VOFA input examples:
    yaw:90
    pitch:45
    motor:100
    pump:1
    C:0
    B:60

  The gateway forwards MotorCommand packets to the motor node over ESP-NOW,
  receives SoilData packets from the soil node, and prints VOFA-friendly lines.
*/

#include <Arduino.h>
#include "espnow_gateway.h"
#include "vofa_protocol.h"

MotorCommand currentMotorState = {
  MOTOR_DEVICE_ID,
  CMD_NONE,
  90,
  90,
  0,
  0
};

static constexpr int MAX_WATER_SPEED = 180;
int manualMotorSpeed = 0;
int moistureThreshold = 0;
bool automaticWatering = false;
bool hasSoilHumidity = false;
float latestSoilHumidity = 0.0f;

void printHelp() {
  Serial.println();
  Serial.println("ESP32-S3 smart garden gateway ready.");
  Serial.println("VOFA commands:");
  Serial.println("  yaw:90");
  Serial.println("  pitch:45");
  Serial.println("  motor:100");
  Serial.println("  pump:1");
  Serial.println("  C:0  (0=manual M, 1=automatic watering)");
  Serial.println("  B:60 (automatic moisture threshold, 0..180)");
  Serial.println();
}

void sendMotorSpeed(int speed) {
  speed = constrain(speed, 0, MAX_WATER_SPEED);
  if (currentMotorState.motor_speed == speed && currentMotorState.cmd == CMD_MOTOR) {
    return;
  }

  currentMotorState.device = MOTOR_DEVICE_ID;
  currentMotorState.cmd = CMD_MOTOR;
  currentMotorState.motor_speed = speed;
  sendMotorCommand(currentMotorState);
}

void updateAutomaticWatering() {
  if (!automaticWatering) {
    return;
  }

  // Stop safely until the first valid soil reading arrives.
  const int targetSpeed = hasSoilHumidity && latestSoilHumidity < moistureThreshold
      ? MAX_WATER_SPEED
      : 0;
  sendMotorSpeed(targetSpeed);
}

void mergeCommandIntoState(const MotorCommand &command) {
  currentMotorState.device = MOTOR_DEVICE_ID;
  currentMotorState.cmd = command.cmd;

  if (command.cmd == CMD_GIMBAL) {
    currentMotorState.servo_x = command.servo_x;
    currentMotorState.servo_y = command.servo_y;
  } else if (command.cmd == CMD_MOTOR) {
    currentMotorState.motor_speed = command.motor_speed;
  } else if (command.cmd == CMD_PUMP) {
    currentMotorState.pump = command.pump;
  }
}

void handleVofaInput() {
  String line;
  if (!readVofaLine(Serial, line)) {
    return;
  }

  MotorCommand command = currentMotorState;
  if (!parseVofaCommand(line, command)) {
    Serial.print("Invalid VOFA command: ");
    Serial.println(line);
    Serial.println("Use yaw:90, pitch:45, motor:100, pump:1, C:0, or B:60");
    return;
  }

  if (command.cmd == CMD_AUTO_WATER) {
    automaticWatering = command.pump != 0;
    if (automaticWatering) {
      updateAutomaticWatering();
    } else {
      sendMotorSpeed(manualMotorSpeed);
    }
    return;
  }

  if (command.cmd == CMD_MOISTURE_THRESHOLD) {
    moistureThreshold = command.motor_speed;
    updateAutomaticWatering();
    return;
  }

  if (command.cmd == CMD_MOTOR) {
    manualMotorSpeed = command.motor_speed;
    if (automaticWatering) {
      return;
    }
  }

  mergeCommandIntoState(command);
  sendMotorCommand(currentMotorState);
}

void handleSoilInput() {
  SoilData data = {};
  if (!takeLatestSoilData(data)) {
    return;
  }

  latestSoilHumidity = data.humidity;
  hasSoilHumidity = true;
  updateAutomaticWatering();
  printFireWaterFrame(Serial, data, currentMotorState);
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  setupGatewayEspNow();
  printHelp();
}

void loop() {
  handleVofaInput();
  handleSoilInput();
}
