#pragma once

#include <Arduino.h>

#pragma pack(push, 1)
struct MotorCommand {
  uint8_t device;
  uint8_t cmd;
  int servo_x;
  int servo_y;
  int motor_speed;
  uint8_t pump;
};

struct SoilData {
  float temperature;
  float humidity;
  float ph;
};
#pragma pack(pop)

static constexpr uint8_t MOTOR_DEVICE_ID = 1;
static constexpr uint8_t CMD_NONE = 0;
static constexpr uint8_t CMD_GIMBAL = 1;
static constexpr uint8_t CMD_MOTOR = 2;
static constexpr uint8_t CMD_PUMP = 3;
// Gateway-only commands. These are handled locally and are never sent over
// ESP-NOW, so the MotorCommand packet layout remains compatible.
static constexpr uint8_t CMD_AUTO_WATER = 4;
static constexpr uint8_t CMD_MOISTURE_THRESHOLD = 5;

bool setupGatewayEspNow();
bool sendMotorCommand(const MotorCommand &command);
bool takeLatestSoilData(SoilData &data);
