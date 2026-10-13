# Node 1: ESP32 + Sensirion SEN54 Environmental Sensor

## Overview
Node 1 is an IoT air quality and ambient climate station built on an **ESP32** microcontroller and a **Sensirion SEN54** all-in-one sensor.

### Parameters Monitored
- **PM1.0, PM2.5, PM4.0, PM10.0** particulate matter (µg/m³)
- **Ambient Temperature** (°C)
- **Relative Humidity** (% RH)
- **Volatile Organic Compounds (VOC Index)** (1 - 500)

### 10-Minute Clock Synchronization
The node synchronizes its system clock with NTP (`pool.ntp.org`). It aligns its transmissions to exact 10-minute boundaries: `:00`, `:10`, `:20`, `:30`, `:40`, `:50` of every hour. This guarantees that its telemetry arrives at the backend in exact synchronization with Node 2's camera images.

---

## Hardware Pinout & Wiring Table

| Sensirion SEN54 Pin | Wire Color | ESP32 Pin | Function |
|:---|:---|:---|:---|
| **Pin 1: VDD** | Red | **5V (VIN)** | 5V Power Supply |
| **Pin 2: GND** | Black | **GND** | Ground |
| **Pin 3: SDA** | Green / Blue | **GPIO 21** | I2C Data |
| **Pin 4: SCL** | Yellow | **GPIO 22** | I2C Clock |
| **Pin 5: SEL** | Tie to GND | **GND** | Selects I2C Mode |
| **Pin 6: NC** | - | *Not Connected* | Leave Floating |

> **Status LED**: ESP32 onboard LED on **GPIO 2** turns ON during active telemetry transmission.

---

## Required Arduino IDE Libraries
Install via **Arduino IDE Library Manager** (`Ctrl + Shift + I`):
1. **Sensirion I2C SEN5x** by Sensirion (Version 0.3.0 or higher)
2. **Sensirion Core** (automatically installed with SEN5x)
3. **ArduinoJson** by Benoît Blanchon (Version 6.x or 7.x)

---

## Configuration Settings in `node_1_esp32_sen54.ino`
```cpp
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// Replace with your Vercel URL
const char* BACKEND_URL   = "https://your-app.vercel.app/telemetry";
const char* DEVICE_TOKEN  = "SHRIDEV";

const char* LOCATION_NAME = "GANDHI_MAIDAN";
const float LATITUDE      = 25.618639;
const float LONGITUDE     = 85.148456;
```
