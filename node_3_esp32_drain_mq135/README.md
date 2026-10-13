# Node 3: ESP32 + Nala Drain Overflow Conductive Rods + MQ-135 Gas Sensor

## Overview
Node 3 is an urban storm drain & municipal sewer monitoring station built on an **ESP32** microcontroller.
It detects:
1. **Storm Drain / Nala Overflow**: Dual electric conductive rods detect rising water circuit completion.
2. **MQ-135 Gas Sensor**: Monitors toxic decomposition sewer gases (Ammonia, H2S, CO2).

---

## 2-Rod Electric Conductor Circuit
```
                  +3.3V
                    |
                 [Rod 1]
                    ~   <-- (Rising water bridges rods)
                 [Rod 2]
                    |
                    +----------> ESP32 GPIO 33
                    |
                 [ 10kΩ ] Resistor
                    |
                   GND
```

---

## Hardware Pinout
- **Conductive Rod 1**: Stainless Steel Rod -> **3.3V**
- **Conductive Rod 2**: Stainless Steel Rod -> **GPIO 33** (with 10kΩ pull-down to GND)
- **MQ-135 Gas Sensor VCC**: **5V (VIN)**
- **MQ-135 Gas Sensor GND**: **GND**
- **MQ-135 Gas Sensor AOUT**: **GPIO 34** (ADC1 Pin)
- **Status LED**: **GPIO 2**
