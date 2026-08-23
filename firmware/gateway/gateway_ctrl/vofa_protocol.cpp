#include "vofa_protocol.h"

static constexpr uint8_t SERIAL_FRAME_MAX_LEN = 64;
static char serialFrame[SERIAL_FRAME_MAX_LEN + 1];
static uint8_t serialFrameIndex = 0;

bool readVofaLine(Stream &serial, String &line) {
  while (serial.available() > 0) {
    const char c = static_cast<char>(serial.read());

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      if (serialFrameIndex == 0) {
        continue;
      }

      serialFrame[serialFrameIndex] = '\0';
      line = String(serialFrame);
      serialFrameIndex = 0;
      return true;
    }

    if (serialFrameIndex < SERIAL_FRAME_MAX_LEN) {
      serialFrame[serialFrameIndex++] = c;
    } else {
      serialFrameIndex = 0;
      serial.println("Serial frame too long.");
    }
  }

  return false;
}

bool parseKeyValue(String line, String &key, int &value) {
  line.trim();
  const int colonIndex = line.indexOf(':');
  const int equalIndex = line.indexOf('=');
  int separatorIndex = -1;

  if (colonIndex >= 0 && equalIndex >= 0) {
    separatorIndex = min(colonIndex, equalIndex);
  } else if (colonIndex >= 0) {
    separatorIndex = colonIndex;
  } else {
    separatorIndex = equalIndex;
  }

  if (separatorIndex < 0) {
    return false;
  }

  key = line.substring(0, separatorIndex);
  key.trim();
  key.toLowerCase();

  String valueText = line.substring(separatorIndex + 1);
  valueText.trim();
  if (key.length() == 0 || valueText.length() == 0) {
    return false;
  }

  value = valueText.toInt();
  return true;
}

bool parseVofaCommand(String line, MotorCommand &command) {
  String key;
  int value = 0;
  if (!parseKeyValue(line, key, value)) {
    return false;
  }

  command.device = MOTOR_DEVICE_ID;

  if (key == "yaw" || key == "y") {
    command.cmd = CMD_GIMBAL;
    command.servo_x = constrain(value, 0, 180);
    return true;
  }

  if (key == "pitch" || key == "p") {
    command.cmd = CMD_GIMBAL;
    command.servo_y = constrain(value, 0, 180);
    return true;
  }

  if (key == "motor" || key == "m") {
    command.cmd = CMD_MOTOR;
    command.motor_speed = constrain(value, 0, 180);
    return true;
  }

  if (key == "pump") {
    command.cmd = CMD_PUMP;
    command.pump = value ? 1 : 0;
    return true;
  }

  if (key == "c") {
    command.cmd = CMD_AUTO_WATER;
    command.pump = value ? 1 : 0;
    return true;
  }

  if (key == "b") {
    command.cmd = CMD_MOISTURE_THRESHOLD;
    command.motor_speed = constrain(value, 0, 180);
    return true;
  }

  return false;
}

void printSoilDataForVofa(Stream &serial, const SoilData &data) {
  serial.print("soil_temp:");
  serial.println(data.temperature, 1);
  serial.print("soil_hum:");
  serial.println(data.humidity, 1);
  serial.print("soil_ph:");
  serial.println(data.ph, 1);
}

void printMotorStateForVofa(Stream &serial, const MotorCommand &state) {
  serial.print("servo_x:");
  serial.println(state.servo_x);
  serial.print("servo_y:");
  serial.println(state.servo_y);
  serial.print("motor:");
  serial.println(state.motor_speed);
  serial.print("pump:");
  serial.println(state.pump);
}

void printFireWaterFrame(Stream &serial, const SoilData &data, const MotorCommand &state) {
  serial.print("firewater:");
  serial.print(data.temperature, 1);
  serial.print(",");
  serial.print(data.humidity, 1);
  serial.print(",");
  serial.print(data.ph, 1);
  serial.print(",");
  serial.print(state.servo_x);
  serial.print(",");
  serial.print(state.servo_y);
  serial.print(",");
  serial.print(state.motor_speed);
  serial.print(",");
  serial.println(state.pump);
}
