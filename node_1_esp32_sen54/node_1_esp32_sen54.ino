/*
 ============================================================================
  SMART CITY AI — NODE 1 (AIR QUALITY & WEATHER MONITORING NODE)
  Hardware: ESP32 Microcontroller + Sensirion SEN54 All-in-One Sensor
  
  Parameters Monitored:
    - PM1.0, PM2.5, PM4.0, PM10.0 Particulate Matter (µg/m³)
    - Ambient Temperature (°C)
    - Ambient Humidity (% RH)
    - Volatile Organic Compounds (VOC Index)
  
  Synchronization:
    - Aligned with clock via NTP to transmit strictly every 10 minutes
      (:00, :10, :20, :30, :40, :50) in sync with Node 2 Camera.
 ============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <SensirionI2CSen5x.h>
#include <ArduinoJson.h>
#include <time.h>

// ============================================================
// 1. NETWORK & BACKEND CONFIGURATION
// ============================================================
const char* WIFI_SSID     = "SHRIDEV";
const char* WIFI_PASSWORD = "1234567898";

// Replace with your Vercel deployment URL (e.g., https://your-project.vercel.app/telemetry)
const char* BACKEND_URL   = "https://smart-city-kappa-three.vercel.app/telemetry";
const char* DEVICE_TOKEN  = "SHRIDEV";

// ============================================================
// 2. NODE IDENTITY & LOCATION
// ============================================================
const char* NODE_ID       = "node_1_sen54";
const char* SENSOR_TYPE   = "SEN54";
const char* LOCATION_NAME = "GANDHI_MAIDAN";
const float LATITUDE      = 25.618639;
const float LONGITUDE     = 85.148456;

// 10-Minute interval in seconds
const int CYCLE_INTERVAL_SEC = 600;

// NTP Settings for India Standard Time (IST: UTC + 5:30 = 19800 seconds)
const char* NTP_SERVER_1    = "pool.ntp.org";
const char* NTP_SERVER_2    = "time.nist.gov";
const long  GMT_OFFSET_SEC  = 19800;
const int   DAYLIGHT_OFFSET = 0;

// Hardware Pins (ESP32 standard I2C)
#define I2C_SDA_PIN 21
#define I2C_SCL_PIN 22
#define STATUS_LED  2

SensirionI2CSen5x sen5x;
bool sensorInitialized = false;

// ============================================================
// HELPER: Connect to WiFi
// ============================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\n[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected! IP Address: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WiFi] Connection timeout. Retrying in background.");
  }
}

// ============================================================
// HELPER: Synchronize Time with NTP
// ============================================================
void syncNTP() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET, NTP_SERVER_1, NTP_SERVER_2);
  struct tm timeinfo;
  int retry = 0;
  Serial.print("[NTP] Synchronizing system clock...");
  while (!getLocalTime(&timeinfo) && retry < 12) {
    delay(500);
    Serial.print(".");
    retry++;
  }
  if (retry < 12) {
    Serial.printf("\n[NTP] Time Synchronized: %02d:%02d:%02d IST\n",
                  timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
  } else {
    Serial.println("\n[NTP] Warning: Clock synchronization failed. Will fallback to periodic timer.");
  }
}

// ============================================================
// HELPER: Calculate seconds until next 10-minute slot boundary (:00, :10, :20, :30, :40, :50)
// ============================================================
int getSecondsUntilNext10MinBoundary() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    // If NTP not locked, default to 600 seconds
    return CYCLE_INTERVAL_SEC;
  }
  int minute = timeinfo.tm_min;
  int second = timeinfo.tm_sec;
  int secondsPastSlot = (minute % 10) * 60 + second;
  int remaining = 600 - secondsPastSlot;
  if (remaining < 5) {
    remaining += 600; // Skip if boundary is less than 5s away to avoid double firing
  }
  return remaining;
}

// ============================================================
// HELPER: Initialize SEN54 Sensor
// ============================================================
bool initSEN54() {
  pinMode(I2C_SDA_PIN, INPUT_PULLUP);
  pinMode(I2C_SCL_PIN, INPUT_PULLUP);

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(100000); // 100 kHz standard I2C
  sen5x.begin(Wire);

  uint16_t error;
  char errorMessage[256];

  error = sen5x.deviceReset();
  if (error) {
    errorToString(error, errorMessage, sizeof(errorMessage));
    Serial.printf("[SEN54] Reset error: %s\n", errorMessage);
    return false;
  }
  delay(100);

  // Start continuous measurement
  error = sen5x.startMeasurement();
  if (error) {
    errorToString(error, errorMessage, sizeof(errorMessage));
    Serial.printf("[SEN54] Start measurement error: %s\n", errorMessage);
    return false;
  }

  Serial.println("[SEN54] Sensor ready & continuous measurement started.");
  return true;
}

// ============================================================
// READ SENSOR & UPLOAD TELEMETRY
// ============================================================
bool readAndUploadTelemetry() {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] No WiFi connection. Upload aborted.");
    return false;
  }

  digitalWrite(STATUS_LED, HIGH);

  float pm1p0 = 0.0, pm2p5 = 0.0, pm4p0 = 0.0, pm10p0 = 0.0;
  float ambientHumidity = 0.0, ambientTemperature = 0.0;
  float vocIndex = 100.0, noxIndex = 0.0;

  if (sensorInitialized) {
    uint16_t error = sen5x.readMeasuredValues(
      pm1p0, pm2p5, pm4p0, pm10p0,
      ambientHumidity, ambientTemperature,
      vocIndex, noxIndex
    );
    if (error) {
      char errorMessage[256];
      errorToString(error, errorMessage, sizeof(errorMessage));
      Serial.printf("[SEN54] Read failed: %s\n", errorMessage);
    }
  }

  // Handle NaNs
  if (isnan(pm1p0)) pm1p0 = 0.0;
  if (isnan(pm2p5)) pm2p5 = 0.0;
  if (isnan(pm4p0)) pm4p0 = 0.0;
  if (isnan(pm10p0)) pm10p0 = 0.0;
  if (isnan(ambientHumidity)) ambientHumidity = 50.0;
  if (isnan(ambientTemperature)) ambientTemperature = 28.0;
  if (isnan(vocIndex)) vocIndex = 100.0;

  Serial.println("\n-------------------------------------------");
  Serial.println("  NODE 1 TELEMETRY READINGS (SEN54)");
  Serial.println("-------------------------------------------");
  Serial.printf("PM1.0 : %.1f µg/m³\n", pm1p0);
  Serial.printf("PM2.5 : %.1f µg/m³\n", pm2p5);
  Serial.printf("PM4.0 : %.1f µg/m³\n", pm4p0);
  Serial.printf("PM10.0: %.1f µg/m³\n", pm10p0);
  Serial.printf("Temp  : %.1f °C\n", ambientTemperature);
  Serial.printf("RH    : %.1f %%\n", ambientHumidity);
  Serial.printf("VOC   : %.1f (Index)\n", vocIndex);
  Serial.println("-------------------------------------------");

  // Build JSON Document
  StaticJsonDocument<768> doc;
  doc["node_id"]       = NODE_ID;
  doc["location_name"] = LOCATION_NAME;
  doc["latitude"]      = LATITUDE;
  doc["longitude"]     = LONGITUDE;
  doc["sensor_type"]   = SENSOR_TYPE;

  JsonObject data = doc.createNestedObject("data");
  data["pm1_0"]            = pm1p0;
  data["pm2_5"]            = pm2p5;
  data["pm4_0"]            = pm4p0;
  data["pm10_0"]           = pm10p0;
  data["temperature_c"]    = ambientTemperature;
  data["humidity_percent"] = ambientHumidity;
  data["voc_index"]        = vocIndex;

  String jsonPayload;
  serializeJson(doc, jsonPayload);

  // Send HTTPS POST to Backend
  unsigned long retryStart = millis();
  int attempt = 1;
  bool success = false;

  while ((millis() - retryStart) < 15000) {
    connectWiFi();
    if (WiFi.status() == WL_CONNECTED) {
      HTTPClient http;
      http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
      http.setTimeout(12000);

      bool began = false;
      if (String(BACKEND_URL).startsWith("https://")) {
        WiFiClientSecure secureClient;
        secureClient.setInsecure(); // Skip TLS certificate check
        began = http.begin(secureClient, BACKEND_URL);
      } else {
        WiFiClient plainClient;
        began = http.begin(plainClient, BACKEND_URL);
      }

      if (began) {
        http.addHeader("Content-Type", "application/json");
        http.addHeader("x-device-token", DEVICE_TOKEN);

        int httpCode = http.POST(jsonPayload);
        if (httpCode == HTTP_CODE_OK || httpCode == 201) {
          String response = http.getString();
          Serial.printf("[HTTP] Success (HTTP %d)! Backend Response: %s\n", httpCode, response.c_str());
          http.end();
          success = true;
          break;
        }

        Serial.printf("[HTTP ERROR] Attempt %d failed with code %d. Retrying...\n", attempt, httpCode);
      }
    }
    attempt++;
    delay(1000);
  }

  digitalWrite(STATUS_LED, LOW);
  return success;
}

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, LOW);

  Serial.println("\n==========================================");
  Serial.println("  Smart City AI - Node 1 (SEN54)");
  Serial.println("  10-Minute Synchronized Urban Cadence");
  Serial.println("==========================================");

  connectWiFi();
  syncNTP();

  sensorInitialized = initSEN54();
  if (!sensorInitialized) {
    Serial.println("[ERROR] SEN54 sensor initialization failed. Check I2C wiring (SDA=21, SCL=22).");
  }

  // Initial read and upload on startup
  Serial.println("[Node 1] Performing initial bootstrap telemetry transmission...");
  readAndUploadTelemetry();
}

// ============================================================
// MAIN LOOP: Synchronized 10-Minute Boundary Execution
// ============================================================
void loop() {
  int sleepSeconds = getSecondsUntilNext10MinBoundary();
  Serial.printf("[Node 1] Sleeping for %d seconds (~%d mins) until next 10-minute sync boundary...\n",
                sleepSeconds, sleepSeconds / 60);

  // Sleep in chunks while maintaining WiFi connection
  unsigned long startSleep = millis();
  unsigned long durationMs = (unsigned long)sleepSeconds * 1000UL;

  while ((millis() - startSleep) < durationMs) {
    delay(1000);
    // Periodic NTP re-sync every 6 hours if needed
  }

  Serial.println("[Node 1] 10-Minute boundary reached! Capturing SEN54 data...");
  readAndUploadTelemetry();
}
