#pragma once

#include <Arduino.h>
#include "espnow_gateway.h"

bool readVofaLine(Stream &serial, String &line);
bool parseVofaCommand(String line, MotorCommand &command);
void printSoilDataForVofa(Stream &serial, const SoilData &data);
void printMotorStateForVofa(Stream &serial, const MotorCommand &state);
void printFireWaterFrame(Stream &serial, const SoilData &data, const MotorCommand &state);
