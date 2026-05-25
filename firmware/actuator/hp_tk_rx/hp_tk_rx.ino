/*
 * Sprinkler actuator (ESP32B): BLE server -> servo angle 0-180 on GPIO 13.
 * Source: pre_code/ESP32_TASK/sashuiji/hp_tk_rx/hp_tk_rx.ino
 *
 * Arduino libraries: BLE (built-in), ESP32Servo
 * BLE name: ESP32_Servo_Controller
 * Pair with: firmware/gateway/hp_tk_tx (or laptop serial -> tx -> BLE)
 */

#include <BLEDevice.h>
#include <BLEUtils.h>
#include <BLEServer.h>
#include <ESP32Servo.h>

// Servo pin
#define SERVO_PIN 13

// BLE UUID
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

Servo myServo;
BLECharacteristic *pCharacteristic;
bool deviceConnected = false;

// BLE server callbacks
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      Serial.println("Device connected");
    }

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      Serial.println("Device disconnected");
      // Restart advertising
      BLEDevice::startAdvertising();
    }
};
int angle=0;
// Characteristic write callback
class MyCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      std::string value = pCharacteristic->getValue();
      
      if (value.length() > 0) {
        angle = atoi(value.c_str());
        Serial.print("Received angle: ");
        Serial.println(angle);
        
        // Clamp angle range
        angle = constrain(angle, 0, 180);
        
        // Drive servo
        myServo.write(angle);
        delay(15);
        
        // Send acknowledgment
        pCharacteristic->setValue("OK");
        pCharacteristic->notify();
      }
    }
};

void setup() {
  Serial.begin(115200);
  Serial.println("ESP32B servo controller starting...");

  // Initialize servo
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  myServo.setPeriodHertz(50);
  pinMode(15,OUTPUT);
  pinMode(2,OUTPUT);
  myServo.attach(SERVO_PIN, 500, 2400);
  myServo.write(90);
  delay(1000);

  // Initialize BLE
  BLEDevice::init("ESP32_Servo_Controller");
  BLEServer *pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  
  BLEService *pService = pServer->createService(SERVICE_UUID);
  pCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ |
                      BLECharacteristic::PROPERTY_WRITE |
                      BLECharacteristic::PROPERTY_NOTIFY
                    );
  
  pCharacteristic->setCallbacks(new MyCallbacks());
  pCharacteristic->setValue("Ready");
  pService->start();
  
  // Start advertising
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(true);
  pAdvertising->setMinPreferred(0x06);
  pAdvertising->setMinPreferred(0x12);
  BLEDevice::startAdvertising();
  
  Serial.println("BLE started, waiting for connection...");
  Serial.print("Device name: ESP32_Servo_Controller");
}

void loop() {
  // Main loop is idle; BLE work runs in callbacks
  if(angle == 0)
  {
    digitalWrite(15,0);
    digitalWrite(2,0);
  }else
  {
    digitalWrite(15,0);
    digitalWrite(2,1);
  }
  delay(100);
}
