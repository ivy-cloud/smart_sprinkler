#pragma once

// TODO: shared CSV parser for heli_tx / heli_rc sensor format
// Format: voltage,current,flowRate,waterLevel,soilTemp,humidity
//
// Reference: pre_code/ESP32_TASK/heli_tx_blue/heli_tx_blue.ino (parseSensorData)

#include <Arduino.h>

struct SensorData {
  float voltage;
  float current;
  float flowRate;
  float waterLevel;
  float soilTemp;
  float humidity;
};

// Placeholder — implement when consolidating from pre_code/ESP32_TASK
inline bool parseSensorCsv(const String& data, SensorData* out) {
  (void)data;
  (void)out;
  return false;
}
