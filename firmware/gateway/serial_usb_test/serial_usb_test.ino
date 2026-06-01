/*
 * Minimal ESP32-S3 USB serial test — flash this first if soil_node_rx shows only ROM text.
 *
 * Tools -> USB CDC On Boot -> Enabled
 * Port -> /dev/cu.usbmodem*
 * Open Serial Monitor @ 115200, press RST, wait 3 s.
 *
 * Expected: "HELLO ESP32-S3 USB SERIAL" every second.
 * If you only see ESP-ROM lines, CDC is off or wrong port.
 */

void setup() {
  Serial.begin(115200);
  delay(3000);
  Serial.println("HELLO ESP32-S3 USB SERIAL");
}

void loop() {
  Serial.println(millis());
  delay(1000);
}
