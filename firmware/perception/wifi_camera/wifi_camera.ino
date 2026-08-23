#include <WiFi.h>
#include <WebServer.h>
#include "esp_camera.h"

// =========================
// WiFi credentials
// =========================
const char* ssid = "TP-Link_F7A2";
const char* password = "46889225";

// =========================
// Camera pin definitions
// From the board schematic
// =========================
#define PWDN_GPIO_NUM   -1
#define RESET_GPIO_NUM  -1
#define XCLK_GPIO_NUM   15

#define SIOD_GPIO_NUM   4
#define SIOC_GPIO_NUM   5

#define Y9_GPIO_NUM     16
#define Y8_GPIO_NUM     17
#define Y7_GPIO_NUM     18
#define Y6_GPIO_NUM     12
#define Y5_GPIO_NUM     10
#define Y4_GPIO_NUM     8
#define Y3_GPIO_NUM     9
#define Y2_GPIO_NUM     11
#define VSYNC_GPIO_NUM  6
#define HREF_GPIO_NUM   7
#define PCLK_GPIO_NUM   13

WebServer server(80);

// Home page HTML
void handleRoot() {
  String html = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>ESP32-S3 Camera Preview</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      text-align: center;
      background: #f4f4f4;
      margin: 0;
      padding: 20px;
    }
    h1 {
      color: #333;
    }
    img {
      width: 640px;
      max-width: 95%;
      border: 3px solid #333;
      border-radius: 10px;
      background: #000;
    }
    .tip {
      color: #666;
      margin-top: 10px;
    }
  </style>
</head>
<body>
  <h1>ESP32-S3-CAM Live Preview</h1>
  <img id="cam" src="/capture?t=0" alt="camera frame">
  <div class="tip">Page auto-refreshes the image</div>

  <script>
    const img = document.getElementById('cam');
    setInterval(() => {
      img.src = '/capture?t=' + new Date().getTime();
    }, 300);
  </script>
</body>
</html>
)rawliteral";

  server.send(200, "text/html; charset=utf-8", html);
}

// Capture one frame and return JPEG
void handleCapture() {
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }

  server.send_P(200, "image/jpeg", (const char *)fb->buf, fb->len);

  esp_camera_fb_return(fb);
}

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;

  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;

  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    Serial.println("PSRAM detected");
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240
    config.jpeg_quality = 20;
    config.fb_count     = 1;
  } else {
    Serial.println("PSRAM not detected");
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240
    config.jpeg_quality = 15;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.print("Camera init failed, error code: 0x");
    Serial.println(err, HEX);
    return false;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s) {
    Serial.print("sensor PID = 0x");
    Serial.println(s->id.PID, HEX);
  }

  Serial.println("Camera init succeeded");
  return true;
}

void setup() {
  Serial.begin(115200);
  delay(2000);

  Serial.println();
  Serial.println("ESP32-S3-CAM web preview starting...");

  // Initialize camera
  if (!initCamera()) {
    while (true) {
      delay(1000);
    }
  }

  // Connect WiFi
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("Connecting WiFi");
  int retry = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    retry++;
    if (retry > 60) {
      Serial.println();
      Serial.println("WiFi connection failed");
      while (true) {
        delay(1000);
      }
    }
  }

  Serial.println();
  Serial.println("WiFi connected");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  // Register web routes
  server.on("/", handleRoot);
  server.on("/capture", HTTP_GET, handleCapture);

  server.begin();
  Serial.println("HTTP server started");
  Serial.println("Open the IP address above in a browser to view the stream");
}

void loop() {
  server.handleClient();
}
