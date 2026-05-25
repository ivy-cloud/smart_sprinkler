/*
 * Angle relay (ESP32A): USB serial line (0-180) -> BLE write to actuator.
 * Source: pre_code/ESP32_TASK/sashuiji/hp_tk_tx/hp_tk_tx.ino
 *
 * Arduino libraries: BLE (built-in)
 * Scans for BLE name: ESP32_Servo_Controller (hp_tk_rx)
 * Usage: Serial Monitor sends e.g. "90" then newline
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEClient.h>

// BLE UUID (same as peripheral)
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

static BLEAdvertisedDevice* myDevice;
static boolean doConnect = false;
static boolean connected = false;
static BLERemoteCharacteristic* pRemoteCharacteristic;
String inputString = "";
bool stringComplete = false;

// Notify callback
static void notifyCallback(
  BLERemoteCharacteristic* pBLERemoteCharacteristic,
  uint8_t* pData,
  size_t length,
  bool isNotify) {
    Serial.print("Response received: ");
    for (int i = 0; i < length; i++) {
      Serial.print((char)pData[i]);
    }
    Serial.println();
}

// BLE client callbacks
class MyClientCallback : public BLEClientCallbacks {
  void onConnect(BLEClient* pclient) {
    Serial.println("Connected to ESP32B");
  }

  void onDisconnect(BLEClient* pclient) {
    connected = false;
    Serial.println("Disconnected from ESP32B");
  }
};

// Scan callback
class MyAdvertisedDeviceCallbacks: public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice advertisedDevice) {
    // Look for target peripheral
    if (advertisedDevice.haveName() && 
        advertisedDevice.getName() == "ESP32_Servo_Controller") {
      BLEDevice::getScan()->stop();
      myDevice = new BLEAdvertisedDevice(advertisedDevice);
      doConnect = true;
      Serial.println("Target device found");
    }
  }
};

bool connectToServer() {
  Serial.print("Connecting to address: ");
  Serial.println(myDevice->getAddress().toString().c_str());
  
  BLEClient* pClient = BLEDevice::createClient();
  pClient->setClientCallbacks(new MyClientCallback());
  
  if (!pClient->connect(myDevice)) {
    Serial.println("Connection failed");
    return false;
  }
  
  // Get service
  BLERemoteService* pRemoteService = pClient->getService(SERVICE_UUID);
  if (pRemoteService == nullptr) {
    Serial.println("Service not found");
    pClient->disconnect();
    return false;
  }
  
  // Get characteristic
  pRemoteCharacteristic = pRemoteService->getCharacteristic(CHARACTERISTIC_UUID);
  if (pRemoteCharacteristic == nullptr) {
    Serial.println("Characteristic not found");
    pClient->disconnect();
    return false;
  }
  
  // Register for notifications
  if(pRemoteCharacteristic->canNotify())
    pRemoteCharacteristic->registerForNotify(notifyCallback);
  
  connected = true;
  Serial.println("Connected; ready to send data");
  return true;
}

void setup() {
  Serial.begin(115200);
  Serial.println("ESP32A serial-to-BLE relay starting...");
  
  // Initialize BLE
  BLEDevice::init("");
  
  // Start scanning
  BLEScan* pBLEScan = BLEDevice::getScan();
  pBLEScan->setAdvertisedDeviceCallbacks(new MyAdvertisedDeviceCallbacks());
  pBLEScan->setActiveScan(true);
  pBLEScan->start(30);
  Serial.println("Scanning for devices...");
}

void loop() {
  // Handle pending connection
  if (doConnect) {
    if (connectToServer()) {
      Serial.println("Connected");
    } else {
      Serial.println("Connection failed");
    }
    doConnect = false;
  }
  
  // Read serial input
  while (Serial.available()) {
    char inChar = (char)Serial.read();
    if (inChar == '\n') {
      stringComplete = true;
    } else {
      inputString += inChar;
    }
  }
  
  // Process complete line
  if (stringComplete) {
    inputString.trim(); // Trim whitespace
    
    if (connected && inputString.length() > 0) {
      // Validate numeric input
      bool isNumber = true;
      for (int i = 0; i < inputString.length(); i++) {
        if (!isDigit(inputString[i])) {
          isNumber = false;
          break;
        }
      }
      
      if (isNumber) {
        int angle = inputString.toInt();
        if (angle >= 0 && angle <= 180) {
          Serial.print("Sending angle: ");
          Serial.println(angle);
          
          // Write to BLE characteristic
          pRemoteCharacteristic->writeValue(inputString.c_str(), inputString.length());
        } else {
          Serial.println("Error: angle must be between 0 and 180");
        }
      } else {
        Serial.println("Error: enter a number");
      }
    } else if (!connected) {
      Serial.println("Error: not connected to ESP32B");
    }
    
    inputString = "";
    stringComplete = false;
  }
  
  delay(10);
}
