#!/bin/bash
set -e

CURRENT_USER=$(whoami)
USER_HOME=$(eval echo ~$CURRENT_USER)
SCRIPT_DIR=$(pwd)
TARGET_DIR="$USER_HOME/smart_city_camera"

echo "=========================================================="
echo "  Smart City AI — Raspberry Pi Camera Node Installer"
echo "  Target User : $CURRENT_USER"
echo "  Target Dir  : $TARGET_DIR"
echo "=========================================================="

# 1. Install dependencies
echo "[1/4] Installing system dependencies..."
sudo apt update
sudo apt install -y python3-pip python3-requests python3-picamera2 || sudo apt install -y python3-opencv

# 2. Setup directory
echo "[2/4] Setting up directory: $TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp "$SCRIPT_DIR/camera_node.py" "$TARGET_DIR/camera_node.py"

# 3. Generate systemd service dynamically for current user
SERVICE_FILE="/etc/systemd/system/smart_city_camera.service"
echo "[3/4] Creating systemd service ($SERVICE_FILE)..."

sudo bash -c "cat <<EOF > $SERVICE_FILE
[Unit]
Description=Smart City AI Node 2 - Raspberry Pi Street Vision Camera
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$TARGET_DIR
ExecStart=/usr/bin/python3 $TARGET_DIR/camera_node.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF"

# 4. Enable and start service
echo "[4/4] Enabling service to auto-start on every boot..."
sudo systemctl daemon-reload
sudo systemctl enable smart_city_camera.service
sudo systemctl restart smart_city_camera.service

echo ""
echo "=========================================================="
echo "  🎉 Installation Successful!"
echo "  The camera will now AUTO-START on every boot!"
echo ""
echo "  Check Service Status:"
echo "    sudo systemctl status smart_city_camera.service"
echo ""
echo "  View Live Logs (Real-time):"
echo "    journalctl -u smart_city_camera.service -f"
echo "=========================================================="
