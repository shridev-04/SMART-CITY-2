# Node 2: Raspberry Pi Street Vision Camera Node

## Overview
Node 2 is an autonomous urban street monitoring unit deployed on a **Raspberry Pi** (supports Pi 3B, 4B, 5, or Zero 2W).
It watches urban streets and roads for:
1. **Sadak Jal Bharaw / Waterlogging**: Accumulation of rain water, flooded street puddles.
2. **Kuda / Garbage Dumping**: Garbage heaps, overflowing bins, and trash blockages on streets and drains.

---

## Power-Saving Camera Operation (Open -> Capture -> Close)
As requested:
- The camera is **NOT kept open continuously** (saving power, CPU temperature, and camera sensor life).
- At exact 10-minute intervals (`:00`, `:10`, `:20`, `:30`, `:40`, `:50`):
  1. The script wakes up and opens the camera hardware interface (`Picamera2` or USB Webcam).
  2. Waits 1.5 seconds for auto-exposure and lighting adjustment.
  3. Captures a crisp, high-resolution street photo.
  4. **Immediately stops, closes, and powers off the camera interface**.
  5. Uploads the photo to the FastAPI backend `/upload` endpoint with a 30-second retry loop.
  6. Deletes the local frame from the SD card to prevent disk fill-up.
  7. Sleeps peacefully until the next 10-minute clock boundary.

---

## Quick Setup on Raspberry Pi

### Option 1: Automatic 1-Click Installer
Run the installer script:
```bash
chmod +x install_service.sh
./install_service.sh
```

### Option 2: Manual Setup
1. Update Raspberry Pi OS and install dependencies:
```bash
sudo apt update
sudo apt install -y python3-pip python3-requests python3-picamera2
```
*(If using a USB webcam instead of Pi Camera Ribbon: `sudo apt install -y python3-opencv`)*

2. Test manually:
```bash
python3 camera_node.py
```

3. Check logs:
```bash
sudo systemctl status citymind_camera.service
journalctl -u citymind_camera.service -f
```

---

## Configuration Settings in `camera_node.py`
```python
BACKEND_URL = "https://your-app.vercel.app"
DEVICE_TOKEN = "SHRIDEV"
NODE_ID = "node_2_camera"
LOCATION_NAME = "GANDHI_MAIDAN"
LATITUDE = "25.618639"
LONGITUDE = "85.148456"
```
