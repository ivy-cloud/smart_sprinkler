#include "espnow_gateway.h"

#include <WiFi.h>
#include <esp_now.h>
#if __has_include(<esp_arduino_version.h>)
#include <esp_arduino_version.h>
#endif

#ifndef ESP_ARDUINO_VERSION_MAJOR
#define ESP_ARDUINO_VERSION_MAJOR 2
#endif

// Replace with the WiFi STA MAC printed by the motor node at startup.
uint8_t MOTOR_NODE_MAC[] = {0x28, 0x84, 0x85, 0x48, 0x41, 0x2C};

static SoilData latestSoilData = {};
static volatile bool hasNewSoilData = false;

bool addEspNowPeer(const uint8_t *peerMac) {
  if (esp_now_is_peer_exist(peerMac)) {
    return true;
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, peerMac, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to add ESP-NOW motor peer.");
    return false;
  }

  return true;
}

void onEspNowReceiveBytes(const uint8_t *data, int length) {
  if (length != sizeof(SoilData)) {
    return;
  }

  memcpy(&latestSoilData, data, sizeof(latestSoilData));
  hasNewSoilData = true;
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

bool setupGatewayEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed.");
    return false;
  }

  if (!addEspNowPeer(MOTOR_NODE_MAC)) {
    return false;
  }

  if (esp_now_register_recv_cb(onEspNowReceive) != ESP_OK) {
    Serial.println("ESP-NOW receive callback registration failed.");
    return false;
  }

  Serial.print("Gateway node MAC: ");
  Serial.println(WiFi.macAddress());
  Serial.println("ESP-NOW gateway ready.");
  return true;
}

bool sendMotorCommand(const MotorCommand &command) {
  const esp_err_t result = esp_now_send(
      MOTOR_NODE_MAC,
      reinterpret_cast<const uint8_t *>(&command),
      sizeof(command));

  return result == ESP_OK;
}

bool takeLatestSoilData(SoilData &data) {
  if (!hasNewSoilData) {
    return false;
  }

  noInterrupts();
  data = latestSoilData;
  hasNewSoilData = false;
  interrupts();
  return true;
}
