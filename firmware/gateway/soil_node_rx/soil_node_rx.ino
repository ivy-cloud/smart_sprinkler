/*
 * SoilNode BLE receiver (ESP32-S3): connect to SoilNode TX, dump soil data on USB Serial.
 *
 * Pair with: SoilNode firmware (RS485 + BLE Notify on the field ESP32-S3).
 *
 * Arduino IDE:
 *   Board: ESP32S3 Dev Module
 *   USB CDC On Boot: Enabled (native USB) or Disabled (UART port) — match your cable
 *   Port: connect receiver board to laptop USB
 *
 * Laptop output (Serial Monitor @ 115200):
 *   [SOIL] Humidity: ... Temperature: ... (English)
 *   [CSV]  voltage,current,flow,waterLevel,soilTemp,humidity   (for analyze_soil.py)
 *
 * Libraries: ESP32 BLE (built-in)
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLEClient.h>
#include <stdio.h>
#include <string.h>

// Match SoilNode advertiser name (boot log: "SoilNode: RS485 + BLE Notify")
static const char* TARGET_NAME = "SoilNode";
// Optional: set TX MAC if the name is missing from adverts (e.g. "74:4d:bd:2a:39:81")
static const char* TARGET_MAC = "74:4d:bd:2a:39:81";

// Set 1 to log every BLE device (deferred to loop() — safe). Keep 0 normally.
#define DEBUG_LIST_ALL_DEVICES 0

// Optional: set after nRF Connect discovery (leave empty to auto-subscribe all NOTIFY chars)
static const char* TARGET_SERVICE_UUID = "";
static const char* TARGET_CHAR_UUID = "";

static const uint32_t SCAN_SECONDS = 15;
static const uint32_t RECONNECT_MS = 5000;

static BLEAdvertisedDevice* targetDevice = nullptr;
static BLEClient* bleClient = nullptr;
static bool doConnect = false;
static bool connected = false;
static bool notifyRegistered = false;
static volatile bool scanCycleDone = false;
static uint16_t lastScanDeviceCount = 0;
static uint32_t scanStartedAt = 0;
static uint32_t scanEndedAt = 0;
static bool pendingBleCleanup = false;
static uint32_t lastNotifyAt = 0;
static bool noDataWarned = false;
static uint32_t lastSoilPrintAt = 0;
static float lastPrintedSal = -1.0f;
static float lastPrintedEc = -1.0f;
static float lastPrintedTemp = -1.0f;
static float lastKnownTemp = 0.0f;
static String textLineBuf;
static uint8_t rawBuf[64];
static size_t rawBufLen = 0;
static uint32_t notifyCount = 0;
static uint32_t lastChunkLogAt = 0;

struct PendingSoil {
  float moisture;
  float temp;
  float salinity;
  float conductivity;
};
static volatile bool pendingSoilOut = false;
static PendingSoil pendingSoil;

static void handleNotifyPayload(const uint8_t* data, size_t length);
static void startScan();
static void onScanFinished();
// Set 1 to log raw BLE chunks (debug only — extra serial traffic).
#define DEBUG_BLE_CHUNKS 0

static portMUX_TYPE serialMux = portMUX_INITIALIZER_UNLOCKED;

struct NotifySlot {
  uint8_t data[32];
  size_t len;
  volatile bool used;
};
static NotifySlot notifyRing[4];
static volatile uint8_t notifyRingCount = 0;

static void serialPrintLocked(const char* msg) {
  portENTER_CRITICAL(&serialMux);
  Serial.print(msg);
  Serial.flush();
  portEXIT_CRITICAL(&serialMux);
}

static void queueSoilReadings(
    float moisture, float temp, float salinity, float conductivity) {
  uint32_t now = millis();
  float outTemp = temp;
  if (outTemp <= 0.05f && lastKnownTemp > 0.05f) {
    outTemp = lastKnownTemp;
  }
  if (temp > 0.05f) {
    lastKnownTemp = temp;
  }

  bool same = salinity == lastPrintedSal && conductivity == lastPrintedEc &&
              outTemp == lastPrintedTemp && (now - lastSoilPrintAt) < 3000;
  if (same) return;

  lastSoilPrintAt = now;
  lastPrintedSal = salinity;
  lastPrintedEc = conductivity;
  lastPrintedTemp = outTemp;

  pendingSoil.moisture = moisture;
  pendingSoil.temp = outTemp;
  pendingSoil.salinity = salinity;
  pendingSoil.conductivity = conductivity;
  pendingSoilOut = true;
}

static void flushPendingSoilOutput() {
  if (!pendingSoilOut) return;

  PendingSoil out = pendingSoil;
  pendingSoilOut = false;

  float temp = out.temp;
  if (temp > 0.05f) {
    lastKnownTemp = temp;
  } else if (lastKnownTemp > 0.05f) {
    temp = lastKnownTemp;
  }

  char block[320];
  snprintf(block, sizeof(block),
           "[SOIL] Humidity: %.1f%%   Temperature: %.1f C   Salinity: %.0f uS/cm   "
           "Conductivity: %.0f uS/cm\n"
           "[CSV] 0,0,0,%.1f,%.1f,%.1f\n"
           "[EC] salinity_uS_cm=%.0f conductivity_uS_cm=%.0f\n",
           out.moisture, temp, out.salinity, out.conductivity, out.moisture, temp,
           out.moisture, out.salinity, out.conductivity);
  serialPrintLocked(block);
}

static bool enqueueNotifyChunk(const uint8_t* data, size_t length) {
  if (length > sizeof(notifyRing[0].data)) {
    length = sizeof(notifyRing[0].data);
  }
  portENTER_CRITICAL(&serialMux);
  bool queued = false;
  for (size_t i = 0; i < 4; i++) {
    if (!notifyRing[i].used) {
      notifyRing[i].len = length;
      memcpy(notifyRing[i].data, data, length);
      notifyRing[i].used = true;
      notifyRingCount++;
      queued = true;
      break;
    }
  }
  portEXIT_CRITICAL(&serialMux);
  return queued;
}

static void drainNotifyRing() {
  for (size_t i = 0; i < 4; i++) {
    if (!notifyRing[i].used) continue;

    NotifySlot slot;
    portENTER_CRITICAL(&serialMux);
    if (!notifyRing[i].used) {
      portEXIT_CRITICAL(&serialMux);
      continue;
    }
    slot = notifyRing[i];
    notifyRing[i].used = false;
    if (notifyRingCount > 0) notifyRingCount--;
    portEXIT_CRITICAL(&serialMux);

    handleNotifyPayload(slot.data, slot.len);
  }
}

static uint16_t readBe16(const uint8_t* p) {
  return ((uint16_t)p[0] << 8) | (uint16_t)p[1];
}

static void appendRawBytes(const uint8_t* data, size_t len) {
  if (len == 0) return;
  if (rawBufLen + len > sizeof(rawBuf)) {
    size_t drop = rawBufLen / 2;
    if (drop == 0) drop = 1;
    memmove(rawBuf, rawBuf + drop, rawBufLen - drop);
    rawBufLen -= drop;
  }
  memcpy(rawBuf + rawBufLen, data, len);
  rawBufLen += len;
}

static String bytesToString(const uint8_t* data, size_t len) {
  String s;
  s.reserve(len);
  for (size_t i = 0; i < len; i++) {
    s += (char)data[i];
  }
  return s;
}

static void scheduleRescan(uint32_t delayMs = 0) {
  scanCycleDone = true;
  if (delayMs >= RECONNECT_MS) {
    scanEndedAt = millis();
  } else {
    scanEndedAt = millis() - (RECONNECT_MS - delayMs);
  }
}

static bool isStandardBleUuid(const std::string& uuid) {
  // Skip GAP / GATT — subscribing to 0x2a05 (indicate) can hang the stack.
  return uuid.find("00001800-") == 0 || uuid.find("00001801-") == 0;
}

#if DEBUG_LIST_ALL_DEVICES
static String pendingBleLog;
#endif

static String extractValueAfterKey(const String& s, const char* key) {
  int i = s.indexOf(key);
  if (i < 0) return "";
  int colon = s.indexOf(':', i);
  if (colon < 0) return "";
  int start = colon + 1;
  while (start < (int)s.length() && (s[start] == ' ' || s[start] == '\t')) start++;
  int end = start;
  while (end < (int)s.length() && s[end] != ' ' && s[end] != '%' && s[end] != 'C' &&
         s[end] != 'c' && s[end] != '℃' && s[end] != 'μ' && s[end] != '\r' &&
         s[end] != '\n') {
    end++;
  }
  return s.substring(start, end);
}

static bool parseSoilPayload(
    float* moisture,
    float* temp,
    float* salinity,
    float* conductivity,
    const String& payload) {
  String m = extractValueAfterKey(payload, "湿度");
  if (m.length() == 0) m = extractValueAfterKey(payload, "Humidity");
  if (m.length() == 0) m = extractValueAfterKey(payload, "Moisture");

  String t = extractValueAfterKey(payload, "温度");
  if (t.length() == 0) t = extractValueAfterKey(payload, "Temperature");

  String sal = extractValueAfterKey(payload, "盐分");
  if (sal.length() == 0) sal = extractValueAfterKey(payload, "Salinity");

  String ec = extractValueAfterKey(payload, "电导率");
  if (ec.length() == 0) ec = extractValueAfterKey(payload, "Conductivity");

  if (m.length() == 0 && t.length() == 0 && sal.length() == 0 && ec.length() == 0) {
    return false;
  }

  *moisture = m.length() ? m.toFloat() : 0.0f;
  *temp = t.length() ? t.toFloat() : 0.0f;
  *salinity = sal.length() ? sal.toFloat() : 0.0f;
  *conductivity = ec.length() ? ec.toFloat() : 0.0f;
  return true;
}

static bool match8ByteFrameAt(
    size_t i,
    float* moisture,
    float* temp,
    float* salinity,
    float* conductivity) {
  if (i + 8 > rawBufLen || rawBuf[i] != 0x01) return false;
  if (rawBuf[i + 4] != 0x00 || rawBuf[i + 6] != 0x00) return false;

  uint16_t temp_x10 = readBe16(rawBuf + i + 2);
  uint16_t sal_v = readBe16(rawBuf + i + 4);
  uint16_t ec_v = readBe16(rawBuf + i + 6);
  if (temp_x10 < 50 || temp_x10 > 450) return false;
  if (sal_v == 0 || ec_v == 0 || sal_v > 500 || ec_v > 500) return false;

  *moisture = (float)rawBuf[i + 1];
  if (*moisture > 100.0f) *moisture = 0.0f;
  *temp = temp_x10 / 10.0f;
  *salinity = (float)sal_v;
  *conductivity = (float)ec_v;
  return true;
}

static bool match6ByteFrameAt(
    size_t i,
    float* moisture,
    float* temp,
    float* salinity,
    float* conductivity) {
  if (i + 6 > rawBufLen || rawBuf[i] != 0x01) return false;
  if (rawBuf[i + 2] != 0x00 || rawBuf[i + 4] != 0x00) return false;

  uint16_t sal_v = readBe16(rawBuf + i + 2);
  uint16_t ec_v = readBe16(rawBuf + i + 4);
  if (sal_v == 0 || ec_v == 0 || sal_v > 500 || ec_v > 500) return false;

  *moisture = 0.0f;
  *temp = 0.0f;
  *salinity = (float)sal_v;
  *conductivity = (float)ec_v;
  return true;
}

static bool tryBinaryFrames() {
  float moisture = 0.0f;
  float temp = 0.0f;
  float salinity = 0.0f;
  float conductivity = 0.0f;
  size_t consumed = 0;

  if (rawBufLen >= 8) {
    for (size_t i = 0; i + 8 <= rawBufLen; i++) {
      if (!match8ByteFrameAt(i, &moisture, &temp, &salinity, &conductivity)) {
        continue;
      }
      consumed = i + 8;
      break;
    }
  }

  if (consumed == 0 && rawBufLen >= 6) {
    for (size_t i = 0; i + 6 <= rawBufLen; i++) {
      if (!match6ByteFrameAt(i, &moisture, &temp, &salinity, &conductivity)) {
        continue;
      }
      consumed = i + 6;
      break;
    }
  }

  if (consumed == 0) {
    if (rawBufLen > 32) {
      memmove(rawBuf, rawBuf + 1, rawBufLen - 1);
      rawBufLen--;
    }
    return false;
  }

  queueSoilReadings(moisture, temp, salinity, conductivity);
  memmove(rawBuf, rawBuf + consumed, rawBufLen - consumed);
  rawBufLen -= consumed;
  return true;
}

static void trimTextBuffer() {
  if (textLineBuf.length() <= 200) return;
  int key = textLineBuf.indexOf("湿度");
  if (key < 0) key = textLineBuf.indexOf("Humidity");
  if (key > 0) {
    textLineBuf = textLineBuf.substring(key);
    return;
  }
  textLineBuf.remove(0, textLineBuf.length() - 120);
}

// TX sends UTF-8 text (Chinese keys) or binary Modbus-style frames over NUS.
static bool tryEmitParsedLine(const String& line) {
  String payload = line;
  payload.trim();
  if (payload.length() == 0) return false;

  float moisture = 0.0f;
  float temp = 0.0f;
  float salinity = 0.0f;
  float conductivity = 0.0f;
  if (!parseSoilPayload(&moisture, &temp, &salinity, &conductivity, payload)) {
    return false;
  }

  queueSoilReadings(moisture, temp, salinity, conductivity);
  return true;
}

static void handleNotifyPayload(const uint8_t* data, size_t length) {
  if (length == 0) return;

  notifyCount++;

  appendRawBytes(data, length);
  textLineBuf += bytesToString(data, length);
  trimTextBuffer();

  if (textLineBuf.indexOf("温度") >= 0 || textLineBuf.indexOf("Temperature") >= 0) {
    float m = 0.0f;
    float t = 0.0f;
    float s = 0.0f;
    float e = 0.0f;
    if (parseSoilPayload(&m, &t, &s, &e, textLineBuf) && t > 0.05f) {
      lastKnownTemp = t;
    }
  }

  if (tryEmitParsedLine(textLineBuf)) {
    textLineBuf = "";
    rawBufLen = 0;
    return;
  }

  for (;;) {
    int nl = textLineBuf.indexOf('\n');
    if (nl < 0) break;
    String oneLine = textLineBuf.substring(0, nl);
    textLineBuf = textLineBuf.substring(nl + 1);
    oneLine.replace("\r", "");
    if (tryEmitParsedLine(oneLine)) {
      textLineBuf = "";
      rawBufLen = 0;
      return;
    }
  }

  if (tryBinaryFrames()) {
    textLineBuf = "";
    return;
  }

#if DEBUG_BLE_CHUNKS
  if (notifyCount <= 5 || millis() - lastChunkLogAt >= 2000) {
    lastChunkLogAt = millis();
    char hex[96];
    size_t n = length < 12 ? length : 12;
    int pos = snprintf(hex, sizeof(hex), "[SOIL RX chunk %u] ", (unsigned)length);
    for (size_t i = 0; i < n && pos < (int)sizeof(hex) - 4; i++) {
      pos += snprintf(hex + pos, sizeof(hex) - pos, "%02X ", data[i]);
    }
    strcat(hex, "\n");
    serialPrintLocked(hex);
  }
#endif
}

static void onNotify(
    BLERemoteCharacteristic* characteristic,
    uint8_t* data,
    size_t length,
    bool isNotify) {
  (void)characteristic;
  (void)isNotify;
  lastNotifyAt = millis();
  noDataWarned = false;
  if (!enqueueNotifyChunk(data, length)) {
    notifyCount++;
  }
}

static bool addressMatches(BLEAdvertisedDevice& device) {
  if (TARGET_MAC[0] == '\0') return false;
  String mac = device.getAddress().toString().c_str();
  mac.toLowerCase();
  String want = TARGET_MAC;
  want.toLowerCase();
  return mac == want;
}

static bool targetMatches(BLEAdvertisedDevice& device) {
  if (addressMatches(device)) return true;
  if (!device.haveName()) return false;
  return device.getName() == TARGET_NAME;
}

static void pickTarget(BLEAdvertisedDevice& advertisedDevice) {
  if (targetDevice != nullptr) return;

  BLEDevice::getScan()->stop();
  targetDevice = new BLEAdvertisedDevice(advertisedDevice);
  doConnect = true;
}

class AdvertisedDeviceCallbacks : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) override {
    lastScanDeviceCount++;

#if DEBUG_LIST_ALL_DEVICES
    pendingBleLog += "[BLE?] ";
    if (advertisedDevice.haveName()) {
      pendingBleLog += advertisedDevice.getName().c_str();
    } else {
      pendingBleLog += "(no name)";
    }
    pendingBleLog += " rssi=";
    pendingBleLog += String(advertisedDevice.getRSSI());
    pendingBleLog += " addr=";
    pendingBleLog += advertisedDevice.getAddress().toString().c_str();
    pendingBleLog += "\n";
#endif

    if (!targetMatches(advertisedDevice)) return;
    pickTarget(advertisedDevice);
  }
};

class ClientCallbacks : public BLEClientCallbacks {
  void onConnect(BLEClient* client) override {
    (void)client;
    Serial.println("[BLE] Connected to SoilNode");
  }

  void onDisconnect(BLEClient* client) override {
    (void)client;
    connected = false;
    notifyRegistered = false;
    pendingBleCleanup = true;
    scheduleRescan(0);
    Serial.println("[BLE] Disconnected — will retry scan");
  }
};

static bool subscribeCharacteristic(BLERemoteCharacteristic* characteristic) {
  if (characteristic == nullptr) return false;
  // Notify only — indicate-only chars (e.g. 0x2a05) can block registerForNotify().
  if (!characteristic->canNotify()) return false;

  characteristic->registerForNotify(onNotify);

  BLERemoteDescriptor* cccd =
      characteristic->getDescriptor(BLEUUID((uint16_t)0x2902));
  if (cccd != nullptr) {
    uint8_t notifyOn[] = {0x01, 0x00};
    cccd->writeValue(notifyOn, 2, true);
    Serial.println("[BLE] CCCD notify enabled");
  }

  Serial.print("[BLE] Notify on char ");
  Serial.println(characteristic->getUUID().toString().c_str());
  return true;
}

static bool registerNotifications(BLEClient* client) {
  if (TARGET_SERVICE_UUID[0] != '\0' && TARGET_CHAR_UUID[0] != '\0') {
    BLERemoteService* service = client->getService(BLEUUID(TARGET_SERVICE_UUID));
    if (service == nullptr) {
      Serial.println("[BLE] Target service not found");
      return false;
    }
    BLERemoteCharacteristic* characteristic =
        service->getCharacteristic(BLEUUID(TARGET_CHAR_UUID));
    return subscribeCharacteristic(characteristic);
  }

  bool any = false;
  std::map<std::string, BLERemoteService*>* services = client->getServices();
  if (services == nullptr) {
    Serial.println("[BLE] No services exposed");
    return false;
  }

  Serial.println("[BLE] Services / characteristics:");
  for (auto& serviceEntry : *services) {
    BLERemoteService* service = serviceEntry.second;
    const std::string serviceUuid = service->getUUID().toString();
    Serial.print("  service ");
    Serial.println(serviceUuid.c_str());

    if (isStandardBleUuid(serviceUuid)) {
      Serial.println("    (standard — skipped)");
      continue;
    }

    std::map<std::string, BLERemoteCharacteristic*>* characteristics =
        service->getCharacteristics();
    if (characteristics == nullptr) continue;

    for (auto& charEntry : *characteristics) {
      BLERemoteCharacteristic* characteristic = charEntry.second;
      Serial.print("    char ");
      Serial.print(characteristic->getUUID().toString().c_str());
      if (characteristic->canNotify()) Serial.print(" notify");
      if (characteristic->canIndicate()) Serial.print(" indicate");
      Serial.println();

      if (subscribeCharacteristic(characteristic)) {
        any = true;
      }
    }
  }

  if (!any) {
    Serial.println("[BLE] No custom NOTIFY service on TX (only standard GAP/GATT)");
    Serial.println("[BLE] Soil data is on TX USB serial — not over BLE yet");
  }
  return any;
}

static bool connectToSoilNode() {
  if (targetDevice == nullptr) return false;

  if (bleClient != nullptr) {
    bleClient->disconnect();
    delete bleClient;
    bleClient = nullptr;
  }

  bleClient = BLEDevice::createClient();
  bleClient->setClientCallbacks(new ClientCallbacks());

  Serial.print("[SCAN] Found ");
  if (targetDevice->haveName()) {
    Serial.print(targetDevice->getName().c_str());
  } else {
    Serial.print("(no name, matched MAC)");
  }
  Serial.print(" @ ");
  Serial.println(targetDevice->getAddress().toString().c_str());

  Serial.print("[BLE] Connecting to ");
  Serial.println(targetDevice->getAddress().toString().c_str());

  if (!bleClient->connect(targetDevice)) {
    Serial.println("[BLE] Connection failed");
    delete targetDevice;
    targetDevice = nullptr;
    scheduleRescan(RECONNECT_MS);
    return false;
  }

  delay(500);  // allow GATT service discovery

  notifyRegistered = registerNotifications(bleClient);
  if (!notifyRegistered) {
    Serial.println("[BLE] No NOTIFY characteristics — check SoilNode firmware");
    bleClient->disconnect();
    delete bleClient;
    bleClient = nullptr;
    delete targetDevice;
    targetDevice = nullptr;
    scheduleRescan(RECONNECT_MS);
    return false;
  }

  connected = true;
  lastNotifyAt = millis();
  noDataWarned = false;
  textLineBuf = "";
  rawBufLen = 0;
  notifyCount = 0;
  notifyRingCount = 0;
  for (size_t i = 0; i < 4; i++) notifyRing[i].used = false;
  Serial.println("[BLE] Ready — soil data will appear below");
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.flush();

  Serial.println();
  Serial.println("BOOT-1: CPU running (soil_node_rx)");
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.println("BOOT-2: USB CDC On Boot = ENABLED (Serial on usbmodem OK)");
#else
  Serial.println("BOOT-2: USB CDC On Boot = DISABLED");
  Serial.println("       App Serial is on UART0 pins — NOT this usbmodem window!");
  Serial.println("       Fix: Tools -> USB CDC On Boot -> Enabled, then re-upload.");
#endif
  Serial.println("===== soil_node_rx (ESP32-S3 BLE client) =====");
  Serial.print("Looking for BLE name: ");
  Serial.println(TARGET_NAME);
  if (TARGET_MAC[0] != '\0') {
    Serial.print("Or MAC: ");
    Serial.println(TARGET_MAC);
  }
#if DEBUG_LIST_ALL_DEVICES
  Serial.println("DEBUG: listing all BLE devices during scan");
#else
  Serial.println("DEBUG off — only SoilNode connect messages");
#endif
  Serial.flush();

  Serial.println("BOOT-3: starting BLE...");
  Serial.flush();

  BLEDevice::init("");

  Serial.println("BOOT-4: BLE OK, starting scan");
  Serial.flush();

  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new AdvertisedDeviceCallbacks());
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(99);

  startScan();
}

static void startScan() {
  lastScanDeviceCount = 0;
  scanCycleDone = false;
  scanStartedAt = millis();
#if DEBUG_LIST_ALL_DEVICES
  pendingBleLog = "";
#endif
  Serial.println("[SCAN] Scanning for SoilNode...");
  BLEDevice::getScan()->start(SCAN_SECONDS, false);
}

static bool scanDurationElapsed() {
  if (scanStartedAt == 0) return false;
  return millis() - scanStartedAt >= (SCAN_SECONDS * 1000UL) + 300UL;
}

static void onScanFinished() {
  scanCycleDone = true;
  scanEndedAt = millis();
#if DEBUG_LIST_ALL_DEVICES
  if (pendingBleLog.length() > 0) {
    Serial.print(pendingBleLog);
    pendingBleLog = "";
  }
#endif
  if (targetDevice != nullptr || connected || doConnect) return;

  Serial.print("[SCAN] Done — saw ");
  Serial.print(lastScanDeviceCount);
  Serial.print(" device(s), SoilNode not found");
  if (TARGET_MAC[0] != '\0') {
    Serial.print(" (expected MAC ");
    Serial.print(TARGET_MAC);
    Serial.print(" or name ");
    Serial.print(TARGET_NAME);
  }
  Serial.println(")");
  Serial.println("[SCAN] Is TX powered? TX serial must show 'BLE advertising started.'");
}

void loop() {
  drainNotifyRing();
  flushPendingSoilOutput();

  if (pendingBleCleanup) {
    pendingBleCleanup = false;
    if (targetDevice != nullptr) {
      delete targetDevice;
      targetDevice = nullptr;
    }
    if (bleClient != nullptr) {
      delete bleClient;
      bleClient = nullptr;
    }
  }

  if (doConnect) {
    doConnect = false;
    connectToSoilNode();
  }

  if (!connected && !doConnect && !scanCycleDone && scanDurationElapsed()) {
    onScanFinished();
  }

  if (!connected && !doConnect && scanCycleDone &&
      millis() - scanEndedAt >= RECONNECT_MS) {
    startScan();
  }

  static uint32_t lastHeartbeat = 0;
  if (!connected && millis() - lastHeartbeat >= 10000) {
    lastHeartbeat = millis();
    Serial.println("[RX] alive — waiting for SoilNode BLE advert...");
  }

  if (connected && !noDataWarned && millis() - lastNotifyAt >= 30000) {
    noDataWarned = true;
    Serial.println("[BLE] Connected but no NOTIFY data yet.");
    Serial.println("[BLE] Waiting for chunks — should see [SOIL RX chunk N] lines.");
  }

  delay(20);
}
