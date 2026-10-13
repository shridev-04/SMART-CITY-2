#!/bin/bash
set -e

echo "=========================================================="
echo "  Smart City AI — Raspberry Pi Camera Node Installer"
echo "=========================================================="

# 1. Install system packages
echo "[1/4] Installing system dependencies..."
sudo apt update
sudo apt install -y python3-pip python3-requests python3-picamera2 || sudo apt install -y python3-opencv

# 2. Setup destination directory
TARGET_DIR="/home/pi/smart_city_camera"
echo "[2/4] Setting up directory: $TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp camera_node.py "$TARGET_DIR/"

# 3. Setup systemd service
echo "[3/4] Installing systemd auto-start service..."
sudo cp citymind_camera.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable citymind_camera.service

# 4. Start service
echo "[4/4] Starting citymind_camera service..."
sudo systemctl restart citymind_camera.service

echo ""
echo "=========================================================="
echo "  Installation complete! Check status with:"
echo "    sudo systemctl status citymind_camera.service"
echo "  View live logs with:"
echo "    journalctl -u citymind_camera.service -f"
echo "=========================================================="
