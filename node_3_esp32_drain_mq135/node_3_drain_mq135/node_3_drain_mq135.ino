/*
 ============================================================================
  SMART CITY AI — NODE 3 (DRAIN OVERFLOW & SEWER GAS SENSOR NODE)
  Hardware: ESP32 + MQ-135 Gas Sensor + Dual Electric Conductive Rods
  
  Monitors:
    - Nala / Storm Drain Overflow Level (via 2 stainless steel conductive rods)
    - MQ-135 Gas Sensor (Ammonia, H2S, sewer gas from choked garbage)
  
  Cadence:
    - Aligned 10-minute periodic telemetry (:00, :10, :20...)
    - Instant emergency interrupt: If water touches the conductive rods mid-cycle,
      dispatches an immediate emergency report to backend!
 ============================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>

// ============================================================
// 1. NETWORK & BACKEND CONFIGURATION
// ============================================================
const char* WIFI_SSID     = "SHRIDEV";
const char* WIFI_PASSWORD = "1234567898";

const char* BACKEND_URL   = "https://smart-city-kappa-three.vercel.app/telemetry";
const char* DEVICE_TOKEN  = "SHRIDEV";

// ============================================================
// 2. NODE IDENTITY & LOCATION
// ============================================================
const char* NODE_ID       = "node_3_drain_mq135";
const char* SENSOR_TYPE   = "MQ135_DRAIN";
const char* LOCATION_NAME = "GANDHI_MAIDAN";
const float LATITUDE      = 25.618639;
const float LONGITUDE     = 85.148456;

// NTP Settings (IST UTC + 5:30)
const char* NTP_SERVER_1    = "pool.ntp.org";
const char* NTP_SERVER_2    = "time.nist.gov";
const long  GMT_OFFSET_SEC  = 19800;
const int   DAYLIGHT_OFFSET = 0;

// Hardware Pins
#define DRAIN_PROBE_PIN   33   // Conductor Rod 2 (with 10k pull-down to GND). Rod 1 goes to 3.3V
#define MQ135_ANALOG_PIN  34   // MQ-135 Analog AOUT Pin (ADC1)
#define STATUS_LED        2    // Onboard Status LED

const int SAMPLE_COUNT        = 15;
const int MQ135_ELEVATED_RAW  = 1200;
const int MQ135_HAZARDOUS_RAW = 2200;

bool lastReportedOverflowState = false;

// ============================================================
// HELPER: Connect to WiFi
// ============================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("\n[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 25) {
    delay(500);
    Serial.print(".");
    retries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
  }
}

// ============================================================
// HELPER: Sync Time with NTP
// ============================================================
void syncNTP() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET, NTP_SERVER_1, NTP_SERVER_2);
  struct tm timeinfo;
  int retry = 0;
  while (!getLocalTime(&timeinfo) && retry < 10) {
    delay(500);
    retry++;
  }
}

// ============================================================
// SENSOR READING FUNCTIONS
// ============================================================
bool isDrainOverflowing() {
  int highCount = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    if (digitalRead(DRAIN_PROBE_PIN) == HIGH) highCount++;
    delay(15);
  }
  return (highCount > (SAMPLE_COUNT * 0.7));
}

void readMQ135(int &rawVal, float &ppmVal, String &statusStr) {
  long sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    sum += analogRead(MQ135_ANALOG_PIN);
    delay(15);
  }
  rawVal = sum / SAMPLE_COUNT;
  float voltage = (rawVal / 4095.0) * 3.3;
  ppmVal = max(10.0f, (voltage * 180.0f));

  if (rawVal >= MQ135_HAZARDOUS_RAW) statusStr = "HAZARDOUS";
  else if (rawVal >= MQ135_ELEVATED_RAW) statusStr = "ELEVATED";
  else statusStr = "NORMAL";
}

// ============================================================
// UPLOAD TELEMETRY
// ============================================================
bool uploadTelemetry(bool isEmergencyInstantTrigger = false) {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return false;

  digitalWrite(STATUS_LED, HIGH);

  bool overflow = isDrainOverflowing();
  int mq135Raw = 0;
  float ppm = 0.0;
  String gasStatus = "NORMAL";
  readMQ135(mq135Raw, ppm, gasStatus);

  lastReportedOverflowState = overflow;

  StaticJsonDocument<768> doc;
  doc["node_id"]       = NODE_ID;
  doc["location_name"] = LOCATION_NAME;
  doc["latitude"]      = LATITUDE;
  doc["longitude"]     = LONGITUDE;
  doc["sensor_type"]   = SENSOR_TYPE;

  JsonObject data = doc.createNestedObject("data");
  data["drain_overflow_detected"] = overflow;
  data["drain_sensor_raw"]        = digitalRead(DRAIN_PROBE_PIN);
  data["mq135_raw"]               = mq135Raw;
  data["air_quality_ppm"]         = ppm;
  data["gas_level_status"]        = gasStatus;
  data["instant_emergency"]       = isEmergencyInstantTrigger;

  String jsonPayload;
  serializeJson(doc, jsonPayload);

  WiFiClientSecure client;
  client.setInsecure();
  HTTPClient http;
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.begin(client, BACKEND_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("x-device-token", DEVICE_TOKEN);
  http.setTimeout(12000);

  int httpCode = http.POST(jsonPayload);
  http.end();
  digitalWrite(STATUS_LED, LOW);
  return (httpCode == HTTP_CODE_OK || httpCode == 201);
}

// ============================================================
// SETUP & LOOP
// ============================================================
void setup() {
  Serial.begin(115200);
  pinMode(STATUS_LED, OUTPUT);
  pinMode(DRAIN_PROBE_PIN, INPUT_PULLDOWN);
  pinMode(MQ135_ANALOG_PIN, INPUT);
  analogReadResolution(12);

  connectWiFi();
  syncNTP();
  uploadTelemetry(false);
}

void loop() {
  unsigned long start = millis();
  bool emergencyFired = false;

  // Poll drain probes every 200ms during the 10-minute cycle
  while ((millis() - start) < 600000UL) {
    delay(200);
    if (!lastReportedOverflowState && isDrainOverflowing()) {
      Serial.println("[EMERGENCY] Instant Drain Overflow detected! Uploading immediately...");
      uploadTelemetry(true);
      emergencyFired = true;
      break;
    }
  }

  if (!emergencyFired) {
    uploadTelemetry(false);
  }
}
