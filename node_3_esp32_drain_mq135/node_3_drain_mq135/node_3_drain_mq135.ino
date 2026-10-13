/*
 ============================================================================
  SMART CITY AI — NODE 3 (NALA DRAIN OVERFLOW & SEWER GAS NODE)
  Hardware: ESP32 + MQ-135 Gas Sensor + Dual Electric Conductive Rods
  
  Monitors:
    - Nala / Storm Drain Overflow Level: 2 Conductive metal rods detect rising
      water circuit closure (Sadak / Nala flood danger mark).
    - MQ-135 Gas Sensor: Detects sewer gases (Ammonia NH3, H2S, decomposing
      organic sewage gases) in choked municipal drains.
  
  Cadence & Logic:
    - Periodic synchronized upload every 10 minutes (:00, :10, :20, :30, :40, :50).
    - Instant emergency interrupt: If water touches the conductive rods mid-cycle,
      dispatches an immediate emergency packet to the backend without waiting!
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

// Live Render Backend URL
const char* BACKEND_URL   = "https://smart-city-r3hp.onrender.com/telemetry";
const char* DEVICE_TOKEN  = "SHRIDEV";

// ============================================================
// 2. NODE IDENTITY & LOCATION
// ============================================================
const char* NODE_ID       = "node_3_drain_mq135";
const char* SENSOR_TYPE   = "MQ135_DRAIN";
const char* LOCATION_NAME = "GANDHI_MAIDAN";
const float LATITUDE      = 25.618639;
const float LONGITUDE     = 85.148456;

// NTP Settings for India Standard Time (IST: UTC + 5:30 = 19800 seconds)
const char* NTP_SERVER_1    = "pool.ntp.org";
const char* NTP_SERVER_2    = "time.nist.gov";
const long  GMT_OFFSET_SEC  = 19800;
const int   DAYLIGHT_OFFSET = 0;

// ============================================================
// 3. HARDWARE PIN DEFINITIONS & CALIBRATION
// ============================================================
// Conductor Rod 2 connected to GPIO 33 (with 10k pull-down to GND). Rod 1 goes to 3.3V.
#define DRAIN_PROBE_PIN   33   // Digital Input (reads HIGH when water conducts)
#define MQ135_ANALOG_PIN  34   // ADC1 Pin (reads MQ-135 AOUT, 0 - 4095)
#define STATUS_LED        2    // Onboard Status LED

const int SAMPLE_COUNT          = 15;
const int MQ135_ELEVATED_RAW    = 1200; // Above this is elevated sewer gas
const int MQ135_HAZARDOUS_RAW   = 2200; // Above this is hazardous toxic sewer gas

bool lastReportedOverflowState = false;
unsigned long lastUploadSuccessMillis = 0;

// ============================================================
// HELPER: Connect to WiFi
// ============================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\n[WiFi] Node 3 Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 25) {
    delay(500);
    Serial.print(".");
    retries++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected! IP Address: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WiFi] Connection failed. Will retry in loop.");
  }
}

// ============================================================
// HELPER: Sync Time with NTP
// ============================================================
void syncNTP() {
  configTime(GMT_OFFSET_SEC, DAYLIGHT_OFFSET, NTP_SERVER_1, NTP_SERVER_2);
  struct tm timeinfo;
  int retry = 0;
  Serial.print("[NTP] Node 3 Syncing Clock...");
  while (!getLocalTime(&timeinfo) && retry < 10) {
    delay(500);
    Serial.print(".");
    retry++;
  }
  if (retry < 10) {
    Serial.printf("\n[NTP] Time Locked: %02d:%02d:%02d IST\n",
                  timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec);
  }
}

// ============================================================
// SENSOR READING FUNCTIONS
// ============================================================

// Reads water conductive rods with multi-sample debouncing
bool isDrainOverflowing() {
  int highCount = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    if (digitalRead(DRAIN_PROBE_PIN) == HIGH) {
      highCount++;
    }
    delay(15);
  }
  // If > 70% samples are HIGH, water has solidly bridged the rods
  return (highCount > (SAMPLE_COUNT * 0.7));
}

// Reads MQ-135 Gas Sensor and determines status & PPM estimate
void readMQ135(int &rawVal, float &ppmVal, String &statusStr) {
  long sum = 0;
  for (int i = 0; i < SAMPLE_COUNT; i++) {
    sum += analogRead(MQ135_ANALOG_PIN);
    delay(15);
  }
  rawVal = sum / SAMPLE_COUNT;

  // Approximate PPM mapping: ESP32 12-bit ADC (0 - 4095)
  float voltage = (rawVal / 4095.0) * 3.3;
  ppmVal = max(10.0f, (voltage * 180.0f));

  if (rawVal >= MQ135_HAZARDOUS_RAW) {
    statusStr = "HAZARDOUS";
  } else if (rawVal >= MQ135_ELEVATED_RAW) {
    statusStr = "ELEVATED";
  } else {
    statusStr = "NORMAL";
  }
}

// ============================================================
// READ SENSORS & UPLOAD TELEMETRY
// ============================================================
bool readAndUploadTelemetry(bool isEmergencyInstantTrigger = false) {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] No WiFi connection. Upload aborted.");
    return false;
  }

  // Cooldown guard to avoid duplicate spamming
  if (!isEmergencyInstantTrigger && lastUploadSuccessMillis > 0 && (millis() - lastUploadSuccessMillis < 8000)) {
    return false;
  }

  digitalWrite(STATUS_LED, HIGH);

  bool overflowDetected = isDrainOverflowing();
  int mq135Raw = 0;
  float approxPpm = 0.0;
  String gasStatus = "NORMAL";
  readMQ135(mq135Raw, approxPpm, gasStatus);

  lastReportedOverflowState = overflowDetected;

  Serial.println("\n-------------------------------------------");
  Serial.println(isEmergencyInstantTrigger ? "  🚨 NODE 3 INSTANT NALA EMERGENCY ALERT" : "  NODE 3 TELEMETRY (NALA & SEWER GAS)");
  Serial.println("-------------------------------------------");
  Serial.printf("Drain Overflow   : %s\n", overflowDetected ? ">>> YES (RODS CONDUCTING - FLOOD MARK) <<<" : "NO (DRY / NORMAL FLOW)");
  Serial.printf("MQ-135 Raw ADC   : %d\n", mq135Raw);
  Serial.printf("Approx Gas PPM   : %.1f ppm\n", approxPpm);
  Serial.printf("Sewer Gas Status : %s\n", gasStatus.c_str());
  Serial.println("-------------------------------------------");

  // Build JSON Document
  StaticJsonDocument<768> doc;
  doc["node_id"]       = NODE_ID;
  doc["location_name"] = LOCATION_NAME;
  doc["latitude"]      = LATITUDE;
  doc["longitude"]     = LONGITUDE;
  doc["sensor_type"]   = SENSOR_TYPE;

  JsonObject data = doc.createNestedObject("data");
  data["drain_overflow_detected"] = overflowDetected;
  data["drain_sensor_raw"]        = digitalRead(DRAIN_PROBE_PIN);
  data["mq135_raw"]               = mq135Raw;
  data["air_quality_ppm"]         = approxPpm;
  data["gas_level_status"]        = gasStatus;
  data["instant_emergency"]       = isEmergencyInstantTrigger;

  String jsonPayload;
  serializeJson(doc, jsonPayload);

  // Send HTTPS POST with properly scoped clients (Zero-Drop Lifetime)
  unsigned long retryStart = millis();
  int attempt = 1;
  bool success = false;

  while ((millis() - retryStart) < 15000) {
    connectWiFi();
    if (WiFi.status() == WL_CONNECTED) {
      WiFiClientSecure secureClient;
      WiFiClient plainClient;
      HTTPClient http;
      http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
      http.setTimeout(12000);

      bool began = false;
      if (String(BACKEND_URL).startsWith("https://")) {
        secureClient.setInsecure(); // Skip TLS certificate validation
        began = http.begin(secureClient, BACKEND_URL);
      } else {
        began = http.begin(plainClient, BACKEND_URL);
      }

      if (began) {
        http.addHeader("Content-Type", "application/json");
        http.addHeader("x-device-token", DEVICE_TOKEN);

        int httpCode = http.POST(jsonPayload);
        if (httpCode == HTTP_CODE_OK || httpCode == 201) {
          String response = http.getString();
          Serial.printf("[HTTP] Node 3 Success (HTTP %d)! Server Response: %s\n", httpCode, response.c_str());
          http.end();
          success = true;
          lastUploadSuccessMillis = millis();
          break;
        }

        String errResponse = http.getString();
        Serial.printf("[HTTP ERROR] Attempt %d failed with code %d. Server: %s\n", attempt, httpCode, errResponse.c_str());
        http.end();
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

  // Drain probe pin: Pulled-down to prevent floating false triggers
  pinMode(DRAIN_PROBE_PIN, INPUT_PULLDOWN);

  // MQ135 analog input
  pinMode(MQ135_ANALOG_PIN, INPUT);
  analogReadResolution(12);

  Serial.println("\n==========================================");
  Serial.println("  Smart City AI — Node 3 (Nala Drain & MQ-135)");
  Serial.println("  Dual Mode: 10m Periodic Sync + 0s Emergency");
  Serial.println("==========================================");

  connectWiFi();
  syncNTP();

  // Initial read and upload on startup
  Serial.println("[Node 3] Performing initial startup telemetry...");
  readAndUploadTelemetry(false);
}

// ============================================================
// MAIN LOOP: 10-Minute Schedule + 0s Instant Overflow Interrupt
// ============================================================
void loop() {
  unsigned long startWait = millis();
  bool emergencyTriggered = false;

  // Poll drain probes every 200ms during the 10-minute cycle (600 seconds)
  while ((millis() - startWait) < 600000UL) {
    delay(200);

    // Instant Interrupt: Did Nala overflow just start mid-cycle?
    if (!lastReportedOverflowState && isDrainOverflowing()) {
      Serial.println("\n🚨 [EMERGENCY] Nala water level reached conductive rods! Uploading immediately...");
      readAndUploadTelemetry(true);
      emergencyTriggered = true;
      break;
    }
  }

  // Periodic 10-minute routine transmission
  if (!emergencyTriggered) {
    Serial.println("\n[Node 3] 10-Minute cycle mark reached! Sending routine drain & gas telemetry...");
    readAndUploadTelemetry(false);
  }
}
