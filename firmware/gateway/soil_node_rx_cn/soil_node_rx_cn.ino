/*
 * SoilNode BLE receiver — Chinese output (original-style, for A/B compare).
 *
 * Pair with soil_node_rx.ino (English output) on a second ESP32-S3 board.
 *
 * Arduino IDE:
 *   Board: ESP32S3 Dev Module
 *   USB CDC On Boot: Enabled
 *   Open: firmware/gateway/soil_node_rx_cn/soil_node_rx_cn.ino
 *
 * Serial @ 115200:
 *   [SOIL] 湿度: 0.0%   温度: 25.8℃   盐分: 24 μS/cm   电导率: 30 μS/cm
 *   [CSV] 0,0,0,0.0,25.8,0.0
 *
 * Same BLE connect logic as soil_node_rx (NUS notify, skip GAP/GATT hang).
 * Parses UTF-8 Chinese from TX when present; also decodes binary NUS frames
 * (same as soil_node_rx) and prints Chinese-formatted [SOIL] lines.
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEScan.h>
#include <BLEAdvertisedDevice.h>
#include <BLEClient.h>
#include <stdio.h>

static const char* TARGET_NAME = "SoilNode";
static const char* TARGET_MAC = "74:4d:bd:2a:39:81";

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
static uint32_t lastSoilPrintAt = 0;
static uint32_t connectedAt = 0;

static String textLineBuf;
static uint8_t rawBuf[64];
static size_t rawBufLen = 0;
static float lastKnownTemp = 0.0f;
static uint32_t notifyCount = 0;

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

struct PendingLine {
  char soil[160];
  char csv[64];
  char ec[64];
  bool ready;
};
static PendingLine pendingOut;
static volatile bool pendingOutReady = false;

static portMUX_TYPE serialMux = portMUX_INITIALIZER_UNLOCKED;

struct NotifySlot {
  uint8_t data[64];
  size_t len;
  volatile bool used;
};
static NotifySlot notifyRing[4];

static void scheduleRescan(uint32_t delayMs = 0) {
  scanCycleDone = true;
  if (delayMs >= RECONNECT_MS) {
    scanEndedAt = millis();
  } else {
    scanEndedAt = millis() - (RECONNECT_MS - delayMs);
  }
}

static bool isStandardBleUuid(const std::string& uuid) {
  return uuid.find("00001800-") == 0 || uuid.find("00001801-") == 0;
}

static void startScan();
static void onScanFinished();
static void onNotify(
    BLERemoteCharacteristic* characteristic,
    uint8_t* data,
    size_t length,
    bool isNotify);

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

static bool parseChineseSoilLine(
    const String& payload,
    float* moisture,
    float* temp,
    float* salinity,
    float* conductivity) {
  String m = extractValueAfterKey(payload, "湿度");
  String t = extractValueAfterKey(payload, "温度");
  String sal = extractValueAfterKey(payload, "盐分");
  String ec = extractValueAfterKey(payload, "电导率");

  if (m.length() == 0 && t.length() == 0 && sal.length() == 0 && ec.length() == 0) {
    return false;
  }

  *moisture = m.length() ? m.toFloat() : 0.0f;
  *temp = t.length() ? t.toFloat() : 0.0f;
  *salinity = sal.length() ? sal.toFloat() : 0.0f;
  *conductivity = ec.length() ? ec.toFloat() : 0.0f;
  return true;
}

static void queueSoilValues(float moisture, float temp, float salinity, float conductivity) {
  uint32_t now = millis();
  if (now - lastSoilPrintAt < 2000) return;

  float outTemp = temp;
  if (outTemp > 0.05f) {
    lastKnownTemp = outTemp;
  } else if (lastKnownTemp > 0.05f) {
    outTemp = lastKnownTemp;
  }

  lastSoilPrintAt = now;

  // Chinese line (same format as TX USB); built from text or binary parse.
  snprintf(pendingOut.soil, sizeof(pendingOut.soil),
           "湿度: %.1f%%   温度: %.1f℃   盐分: %.0f μS/cm   电导率: %.0f μS/cm",
           moisture, outTemp, salinity, conductivity);
  snprintf(pendingOut.csv, sizeof(pendingOut.csv), "[CSV] 0,0,0,%.1f,%.1f,%.1f",
           moisture, outTemp, moisture);
  snprintf(pendingOut.ec, sizeof(pendingOut.ec),
           "[EC] salinity_uS_cm=%.0f conductivity_uS_cm=%.0f", salinity, conductivity);
  pendingOutReady = true;
}

static void queueChineseOutput(const String& rawLine) {
  float moisture = 0.0f;
  float temp = 0.0f;
  float salinity = 0.0f;
  float conductivity = 0.0f;

  if (!parseChineseSoilLine(rawLine, &moisture, &temp, &salinity, &conductivity)) {
    return;
  }

  uint32_t now = millis();
  if (now - lastSoilPrintAt < 2000) return;
  lastSoilPrintAt = now;

  rawLine.toCharArray(pendingOut.soil, sizeof(pendingOut.soil));
  snprintf(pendingOut.csv, sizeof(pendingOut.csv), "[CSV] 0,0,0,%.1f,%.1f,%.1f",
           moisture, temp, moisture);
  snprintf(pendingOut.ec, sizeof(pendingOut.ec),
           "[EC] salinity_uS_cm=%.0f conductivity_uS_cm=%.0f", salinity, conductivity);
  pendingOutReady = true;
}

static void flushPendingOutput() {
  if (!pendingOutReady) return;
  pendingOutReady = false;

  char block[360];
  snprintf(block, sizeof(block), "[SOIL] %s\n%s\n%s\n", pendingOut.soil, pendingOut.csv,
           pendingOut.ec);

  portENTER_CRITICAL(&serialMux);
  Serial.print(block);
  Serial.flush();
  portEXIT_CRITICAL(&serialMux);
}

static String bytesToString(const uint8_t* data, size_t len) {
  String s;
  s.reserve(len);
  for (size_t i = 0; i < len; i++) {
    s += (char)data[i];
  }
  return s;
}

static bool match8ByteFrameAt(
    size_t i, float* moisture, float* temp, float* salinity, float* conductivity) {
  if (i + 8 > rawBufLen || rawBuf[i] != 0x01) return false;
  if (rawBuf[i + 4] != 0x00 || rawBuf[i + 6] != 0x00) return false;
  uint16_t temp_x10 = readBe16(rawBuf + i + 2);
  uint16_t sal_v = readBe16(rawBuf + i + 4);
  uint16_t ec_v = readBe16(rawBuf + i + 6);
  if (temp_x10 < 50 || temp_x10 > 450 || sal_v == 0 || ec_v == 0 || sal_v > 500 ||
      ec_v > 500) {
    return false;
  }
  *moisture = (float)rawBuf[i + 1];
  if (*moisture > 100.0f) *moisture = 0.0f;
  *temp = temp_x10 / 10.0f;
  *salinity = (float)sal_v;
  *conductivity = (float)ec_v;
  return true;
}

static bool match6ByteFrameAt(
    size_t i, float* moisture, float* temp, float* salinity, float* conductivity) {
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
      if (!match8ByteFrameAt(i, &moisture, &temp, &salinity, &conductivity)) continue;
      consumed = i + 8;
      break;
    }
  }
  if (consumed == 0 && rawBufLen >= 6) {
    for (size_t i = 0; i + 6 <= rawBufLen; i++) {
      if (!match6ByteFrameAt(i, &moisture, &temp, &salinity, &conductivity)) continue;
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

  queueSoilValues(moisture, temp, salinity, conductivity);
  memmove(rawBuf, rawBuf + consumed, rawBufLen - consumed);
  rawBufLen -= consumed;
  return true;
}

static void processNotifyPayload(const uint8_t* data, size_t length) {
  if (length == 0) return;

  notifyCount++;
  if (notifyCount == 1) {
    Serial.println("[BLE] First notify chunk received");
  }
  appendRawBytes(data, length);
  textLineBuf += bytesToString(data, length);

  if (textLineBuf.indexOf("湿度") >= 0 || textLineBuf.indexOf("温度") >= 0) {
    String line = textLineBuf;
    line.trim();
    queueChineseOutput(line);
    textLineBuf = "";
    rawBufLen = 0;
    return;
  }

  for (;;) {
    int nl = textLineBuf.indexOf('\n');
    if (nl < 0) break;
    String oneLine = textLineBuf.substring(0, nl);
    textLineBuf = textLineBuf.substring(nl + 1);
    oneLine.trim();
    if (oneLine.length() > 0) {
      queueChineseOutput(oneLine);
    }
  }

  if (tryBinaryFrames()) {
    textLineBuf = "";
    return;
  }

  if (textLineBuf.length() > 200) {
    textLineBuf.remove(0, textLineBuf.length() - 120);
  }
}

static bool enqueueNotifyChunk(const uint8_t* data, size_t length) {
  if (length > sizeof(notifyRing[0].data)) {
    length = sizeof(notifyRing[0].data);
  }
  for (size_t i = 0; i < 4; i++) {
    if (!notifyRing[i].used) {
      notifyRing[i].len = length;
      memcpy(notifyRing[i].data, data, length);
      notifyRing[i].used = true;
      return true;
    }
  }
  return false;
}

static void drainNotifyRing() {
  for (size_t i = 0; i < 4; i++) {
    if (!notifyRing[i].used) continue;
    NotifySlot slot;
    slot.len = notifyRing[i].len;
    memcpy(slot.data, notifyRing[i].data, slot.len);
    notifyRing[i].used = false;
    processNotifyPayload(slot.data, slot.len);
  }
}

static void onNotify(
    BLERemoteCharacteristic* characteristic,
    uint8_t* data,
    size_t length,
    bool isNotify) {
  (void)characteristic;
  (void)isNotify;
  lastNotifyAt = millis();
  enqueueNotifyChunk(data, length);
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
  if (characteristic == nullptr || !characteristic->canNotify()) return false;

  characteristic->registerForNotify(onNotify);

  BLERemoteDescriptor* cccd =
      characteristic->getDescriptor(BLEUUID((uint16_t)0x2902));
  if (cccd != nullptr) {
    uint8_t notifyOn[] = {0x01, 0x00};
    cccd->writeValue(notifyOn, 2, true);
  }

  Serial.print("[BLE] Notify on char ");
  Serial.println(characteristic->getUUID().toString().c_str());
  return true;
}

static bool registerNotifications(BLEClient* client) {
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
      Serial.println();
      if (subscribeCharacteristic(characteristic)) {
        any = true;
      }
    }
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
  Serial.print(targetDevice->haveName() ? targetDevice->getName().c_str() : "(no name)");
  Serial.print(" @ ");
  Serial.println(targetDevice->getAddress().toString().c_str());

  if (!bleClient->connect(targetDevice)) {
    Serial.println("[BLE] Connection failed");
    delete targetDevice;
    targetDevice = nullptr;
    scheduleRescan(RECONNECT_MS);
    return false;
  }

  delay(500);

  notifyRegistered = registerNotifications(bleClient);
  if (!notifyRegistered) {
    Serial.println("[BLE] No NOTIFY characteristics");
    bleClient->disconnect();
    delete bleClient;
    bleClient = nullptr;
    delete targetDevice;
    targetDevice = nullptr;
    scheduleRescan(RECONNECT_MS);
    return false;
  }

  connected = true;
  connectedAt = millis();
  notifyCount = 0;
  rawBufLen = 0;
  textLineBuf = "";
  for (size_t i = 0; i < 4; i++) notifyRing[i].used = false;
  Serial.println("[BLE] Ready — Chinese soil lines below");
  return true;
}

static void startScan() {
  lastScanDeviceCount = 0;
  scanCycleDone = false;
  scanStartedAt = millis();
  Serial.println("[SCAN] Scanning for SoilNode...");
  BLEDevice::getScan()->start(SCAN_SECONDS, false);
}

static bool scanDurationElapsed() {
  return scanStartedAt != 0 &&
         millis() - scanStartedAt >= (SCAN_SECONDS * 1000UL) + 300UL;
}

static void onScanFinished() {
  scanCycleDone = true;
  scanEndedAt = millis();
  if (targetDevice != nullptr || connected || doConnect) return;
  Serial.print("[SCAN] Done — saw ");
  Serial.print(lastScanDeviceCount);
  Serial.println(" device(s), SoilNode not found");
}

void setup() {
  Serial.begin(115200);
  delay(3000);

  Serial.println();
  Serial.println("BOOT-1: CPU running (soil_node_rx_cn)");
  Serial.println("===== soil_node_rx_cn (Chinese BLE output) =====");
  Serial.print("Looking for: ");
  Serial.println(TARGET_NAME);
  Serial.print("Or MAC: ");
  Serial.println(TARGET_MAC);

  BLEDevice::init("");

  BLEScan* scan = BLEDevice::getScan();
  scan->setAdvertisedDeviceCallbacks(new AdvertisedDeviceCallbacks());
  scan->setActiveScan(true);
  scan->setInterval(100);
  scan->setWindow(99);

  startScan();
}

void loop() {
  drainNotifyRing();
  flushPendingOutput();

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

  if (connected && notifyCount == 0 && connectedAt > 0 &&
      millis() - connectedAt >= 10000) {
    static uint32_t lastNoNotifyWarn = 0;
    if (millis() - lastNoNotifyWarn >= 10000) {
      lastNoNotifyWarn = millis();
      Serial.println("[BLE] Connected but no notify data yet — is TX running?");
    }
  }

  if (connected && millis() - lastNotifyAt >= 30000 && lastNotifyAt > 0) {
    static uint32_t lastWarn = 0;
    if (millis() - lastWarn >= 30000) {
      lastWarn = millis();
      Serial.print("[BLE] Connected, notify chunks: ");
      Serial.println((unsigned)notifyCount);
      if (notifyCount == 0) {
        Serial.println("[BLE] No notify yet — check TX is advertising and paired path");
      }
    }
  }

  delay(20);
}
