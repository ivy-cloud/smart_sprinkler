/*
  ESP32-S3 reads soil moisture/temperature sensor over Modbus RTU.

  Manual protocol summary:
  - Default slave address: 0x01
  - Sensor serial used here: 115200 bps, 8 data bits, no parity, 1 stop bit
  - Function code: 0x03, read holding registers
  - Temperature/moisture transmitter:
    Register 0x0000: soil moisture, value x10
    Register 0x0001: soil temperature, signed value x10
  - pH transmitter:
    Register 0x0000: soil pH, value x10
    Request frame: 01 03 00 00 00 01 84 0A

  Wiring notes:
  - Change SENSOR_RX_PIN and SENSOR_TX_PIN to match your ESP32-S3 wiring.
  - If your RS485 module has DE/RE direction control, connect them together
    to RS485_DE_RE_PIN and set that pin number below.
  - If the module has automatic direction control, keep RS485_DE_RE_PIN = -1.
*/

#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>

static constexpr uint8_t TEMP_HUM_SENSOR_ADDR = 0x01;
static constexpr uint8_t PH_SENSOR_ADDR = 0x01; // Keep this at 0x01 to match the verified working sensor setup.
static constexpr uint32_t SENSOR_BAUD = 115200;
// [ADDED] Slowed poll so Serial logs are readable (was 200).
static constexpr uint32_t SOIL_SEND_INTERVAL_MS = 2000;
// [ADDED] Heartbeat interval to prove firmware keeps running.
static constexpr uint32_t HEARTBEAT_INTERVAL_MS = 2000;
// [ADDED] Shorter Modbus timeout for faster scan/fail logs (was 1000).
static constexpr uint32_t MODBUS_RESPONSE_TIMEOUT_MS = 500;

static constexpr int SENSOR_RX_PIN = 18;  // ESP32-S3 RX  <- module TX/RO
static constexpr int SENSOR_TX_PIN = 17;  // ESP32-S3 TX  -> module RX/DI
static constexpr int RS485_DE_RE_PIN = -1; // Set to a GPIO if DE/RE is needed.
// [ADDED] Enable baud/address scan at boot.
static constexpr bool SCAN_ON_STARTUP = true;
// [ADDED] Print Modbus TX/RX hex frames.
static constexpr bool LOG_MODBUS_FRAMES = true;

// Replace with the WiFi STA MAC printed by the gateway node at startup.
uint8_t GATEWAY_MAC[] =  {0x94, 0xA9, 0x90, 0x11, 0xF1, 0x04}; // original {0x44, 0xB1, 0x76, 0xCD, 0x6A, 0xC8};

HardwareSerial SensorSerial(1);
uint32_t lastSoilSendMs = 0;
// ===== BEGIN ADDED: heartbeat / poll counters =====
uint32_t lastHeartbeatMs = 0;
uint32_t loopCount = 0;
uint32_t pollOkCount = 0;
uint32_t pollFailCount = 0;
// ===== END ADDED: heartbeat / poll counters =====

#pragma pack(push, 1)
struct SoilData {
  float temperature;
  float humidity;
  float ph;
};
#pragma pack(pop)

uint16_t modbusCrc16(const uint8_t *data, size_t length) {
  uint16_t crc = 0xFFFF;

  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      if (crc & 0x0001) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }

  return crc;
}

void setRs485Transmit(bool enabled) {
  if (RS485_DE_RE_PIN < 0) {
    return;
  }

  digitalWrite(RS485_DE_RE_PIN, enabled ? HIGH : LOW);
  delayMicroseconds(200);
}

void printHexByte(uint8_t value) {
  if (value < 0x10) {
    Serial.print('0');
  }
  Serial.print(value, HEX);
}

// ===== BEGIN ADDED: printFrame now actually logs (was disabled / early-return) =====
void printFrame(const char *label, const uint8_t *data, size_t length) {
  if (!LOG_MODBUS_FRAMES) {
    return;
  }

  Serial.print(label);
  Serial.print(": ");
  if (length == 0) {
    Serial.println("(empty)");
    return;
  }

  for (size_t i = 0; i < length; ++i) {
    printHexByte(data[i]);
    Serial.print(' ');
  }
  Serial.print("(");
  Serial.print(length);
  Serial.println(" bytes)");
}
// ===== END ADDED: printFrame =====

bool addEspNowPeer(const uint8_t *peerMac) {
  if (esp_now_is_peer_exist(peerMac)) {
    return true;
  }

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, peerMac, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;

  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    Serial.println("Failed to add ESP-NOW gateway peer.");
    return false;
  }

  return true;
}

bool setupEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();

  if (esp_now_init() != ESP_OK) {
    Serial.println("ESP-NOW init failed.");
    return false;
  }

  if (!addEspNowPeer(GATEWAY_MAC)) {
    return false;
  }

  Serial.print("Soil node MAC: ");
  Serial.println(WiFi.macAddress());
  Serial.println("ESP-NOW soil sender ready.");
  return true;
}

void sendSoilData(float temperature, float moisture, float ph) {
  SoilData data = {};
  data.temperature = temperature;
  data.humidity = moisture;
  data.ph = ph;

  const esp_err_t result = esp_now_send(GATEWAY_MAC, reinterpret_cast<const uint8_t *>(&data), sizeof(data));
  Serial.print("Send ESP-NOW soil data: temp=");
  Serial.print(data.temperature, 1);
  Serial.print(", hum=");
  Serial.print(data.humidity, 1);
  Serial.print(", ph=");
  Serial.print(data.ph, 1);
  Serial.print(", result=");
  Serial.println(result == ESP_OK ? "OK" : "FAIL");
}

bool readRegisters(uint8_t slaveAddr, uint16_t startReg, uint16_t regCount,
                   uint8_t *response, size_t responseSize, size_t &responseLen,
                   const char *tag) {  // [ADDED] tag for failure logs
  const size_t expectedLen = 5 + (regCount * 2);
  // ===== BEGIN ADDED: failure logging =====
  if (responseSize < expectedLen) {
    Serial.print(tag);
    Serial.println(" FAIL: response buffer too small");
    return false;
  }
  // ===== END ADDED: failure logging =====

  uint8_t request[8] = {
    slaveAddr,
    0x03,
    static_cast<uint8_t>(startReg >> 8),
    static_cast<uint8_t>(startReg & 0xFF),
    static_cast<uint8_t>(regCount >> 8),
    static_cast<uint8_t>(regCount & 0xFF),
    0x00,
    0x00
  };

  const uint16_t requestCrc = modbusCrc16(request, 6);
  request[6] = requestCrc & 0xFF;        // CRC low byte first
  request[7] = (requestCrc >> 8) & 0xFF; // CRC high byte second

  while (SensorSerial.available()) {
    SensorSerial.read();
  }

  printFrame("TX", request, sizeof(request));  // [ADDED] was commented out

  setRs485Transmit(true);
  SensorSerial.write(request, sizeof(request));
  SensorSerial.flush();
  setRs485Transmit(false);

  responseLen = 0;
  const uint32_t startMs = millis();
  while ((millis() - startMs) < MODBUS_RESPONSE_TIMEOUT_MS && responseLen < expectedLen) {
    if (SensorSerial.available()) {
      response[responseLen++] = SensorSerial.read();
    }
  }

  printFrame("RX", response, responseLen);  // [ADDED] was commented out

  // ===== BEGIN ADDED: detailed Modbus failure reasons =====
  if (responseLen == 0) {
    Serial.print(tag);
    Serial.println(" FAIL: no bytes received (timeout)");
    return false;
  }

  if (responseLen != expectedLen) {
    Serial.print(tag);
    Serial.print(" FAIL: got ");
    Serial.print(responseLen);
    Serial.print(" bytes, expected ");
    Serial.println(expectedLen);
    return false;
  }

  const uint16_t receivedCrc = response[responseLen - 2] |
                               (static_cast<uint16_t>(response[responseLen - 1]) << 8);
  const uint16_t calculatedCrc = modbusCrc16(response, responseLen - 2);
  if (receivedCrc != calculatedCrc) {
    Serial.print(tag);
    Serial.print(" FAIL: CRC mismatch rx=0x");
    Serial.print(receivedCrc, HEX);
    Serial.print(" calc=0x");
    Serial.println(calculatedCrc, HEX);
    return false;
  }

  if (response[0] != slaveAddr || response[1] != 0x03 || response[2] != regCount * 2) {
    Serial.print(tag);
    Serial.println(" FAIL: unexpected Modbus header");
    return false;
  }
  // ===== END ADDED: detailed Modbus failure reasons =====

  return true;
}

bool readTempHumidity(uint8_t slaveAddr, float &temperature, float &moisture) {
  uint8_t thResponse[16] = {};
  size_t thResponseLen = 0;

  // [ADDED] pass "TempHum" tag into readRegisters for logs
  if (!readRegisters(slaveAddr, 0x0000, 2, thResponse, sizeof(thResponse), thResponseLen, "TempHum")) {
    return false;
  }

  const uint16_t moistureRaw = (static_cast<uint16_t>(thResponse[3]) << 8) | thResponse[4];
  const int16_t temperatureRaw = static_cast<int16_t>(
    (static_cast<uint16_t>(thResponse[5]) << 8) | thResponse[6]
  );

  moisture = moistureRaw / 10.0f;
  temperature = temperatureRaw / 10.0f;
  return true;
}

bool readPh(uint8_t slaveAddr, float &ph) {
  uint8_t phResponse[8] = {};
  size_t phResponseLen = 0;
  // [ADDED] pass "pH" tag into readRegisters for logs
  if (!readRegisters(slaveAddr, 0x0000, 1, phResponse, sizeof(phResponse), phResponseLen, "pH")) {
    return false;
  }

  const uint16_t phRaw = (static_cast<uint16_t>(phResponse[3]) << 8) | phResponse[4];
  ph = phRaw / 10.0f;
  return true;
}

bool pollSensor() {
  float temperature = 0.0f;
  float moisture = 0.0f;
  float ph = 0.0f;

  if (!readTempHumidity(TEMP_HUM_SENSOR_ADDR, temperature, moisture)) {
    pollFailCount++;  // [ADDED]
    return false;
  }

  if (!readPh(PH_SENSOR_ADDR, ph)) {
    pollFailCount++;  // [ADDED]
    return false;
  }

  pollOkCount++;  // [ADDED]
  Serial.print("Temperature: ");
  Serial.print(temperature, 1);
  Serial.print(" C, Humidity: ");
  Serial.print(moisture, 1);
  const float displayedPh = ph / 10.0f;

  Serial.print(" %, PH: ");
  Serial.println(displayedPh, 1);
  sendSoilData(temperature, moisture, displayedPh);
  return true;
}

// ===== BEGIN ADDED: improved startup scan (includes 115200 + temp/hum try) =====
void scanSensor() {
  const uint32_t baudList[] = {115200, 9600, 4800, 2400};

  Serial.println();
  Serial.println("=== Startup Modbus scan ===");
  Serial.println("Trying baud rates and slave addresses 1..10...");

  for (uint32_t baud : baudList) {
    SensorSerial.updateBaudRate(baud);
    delay(200);

    Serial.print("--- Scan baud ");
    Serial.print(baud);
    Serial.println(" ---");

    for (uint8_t addr = 1; addr <= 10; ++addr) {
      Serial.print("Try addr 0x");
      printHexByte(addr);
      Serial.print(" @ ");
      Serial.println(baud);

      float temperature = 0.0f;
      float moisture = 0.0f;
      float ph = 0.0f;

      if (readTempHumidity(addr, temperature, moisture)) {
        Serial.print("FOUND temp/hum: addr=0x");
        printHexByte(addr);
        Serial.print(", baud=");
        Serial.print(baud);
        Serial.print(", temp=");
        Serial.print(temperature, 1);
        Serial.print(", hum=");
        Serial.println(moisture, 1);
        SensorSerial.updateBaudRate(SENSOR_BAUD);
        return;
      }

      if (readPh(addr, ph)) {
        Serial.print("FOUND pH: addr=0x");
        printHexByte(addr);
        Serial.print(", baud=");
        Serial.print(baud);
        Serial.print(", ph=");
        Serial.println(ph / 10.0f, 1);
        SensorSerial.updateBaudRate(SENSOR_BAUD);
        return;
      }
      delay(50);
    }
  }

  SensorSerial.updateBaudRate(SENSOR_BAUD);
  Serial.println("Scan finished: no Modbus response.");
  Serial.println("If every RX is 0 bytes: check TX/RX cross, A/B polarity, sensor power, shared GND.");
  Serial.println();
}
// ===== END ADDED: improved startup scan =====

void setup() {
  Serial.begin(115200);
  delay(1500);  // [ADDED] wait so Serial Monitor can catch boot logs

  // ===== BEGIN ADDED: clear "firmware is running" banner =====
  Serial.println();
  Serial.println("========================================");
  Serial.println("soil_tem_hum_get: firmware is RUNNING");
  Serial.print("Build pins: RX=");
  Serial.print(SENSOR_RX_PIN);
  Serial.print(" TX=");
  Serial.print(SENSOR_TX_PIN);
  Serial.print(" baud=");
  Serial.println(SENSOR_BAUD);
  // ===== END ADDED: banner =====

  Serial.print("Temp/Humidity Modbus address: 0x");
  printHexByte(TEMP_HUM_SENSOR_ADDR);
  Serial.print(", pH Modbus address: 0x");
  printHexByte(PH_SENSOR_ADDR);
  Serial.println();
  Serial.println("========================================");  // [ADDED]

  if (RS485_DE_RE_PIN >= 0) {
    pinMode(RS485_DE_RE_PIN, OUTPUT);
    setRs485Transmit(false);
  }

  SensorSerial.begin(SENSOR_BAUD, SERIAL_8N1, SENSOR_RX_PIN, SENSOR_TX_PIN);
  setupEspNow();

  if (SCAN_ON_STARTUP) {
    scanSensor();
  }

  // [ADDED]
  Serial.println("Entering poll loop. Heartbeat every 2s proves code keeps running.");
}

void loop() {
  loopCount++;  // [ADDED]
  const uint32_t now = millis();

  // ===== BEGIN ADDED: heartbeat proves loop is alive =====
  if (now - lastHeartbeatMs >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    Serial.print("[HEARTBEAT] uptime_ms=");
    Serial.print(now);
    Serial.print(" loops=");
    Serial.print(loopCount);
    Serial.print(" poll_ok=");
    Serial.print(pollOkCount);
    Serial.print(" poll_fail=");
    Serial.println(pollFailCount);
  }
  // ===== END ADDED: heartbeat =====

  if (now - lastSoilSendMs >= SOIL_SEND_INTERVAL_MS) {
    lastSoilSendMs = now;
    pollSensor();
  }
}
