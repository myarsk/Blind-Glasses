#!/usr/bin/env bash
# Blind Glasses — Raspberry Pi dependency installer
# Run: chmod +x install.sh && sudo ./install.sh

set -e

echo "=== Updating package lists ==="
sudo apt update

echo "=== Installing system packages ==="
sudo apt install -y \
    python3-pip \
    espeak \
    libatlas-base-dev \
    cmake \
    python3-spidev \
    python3-serial \
    libopencv-dev \
    python3-opencv

echo ""
echo "=== Installing Python packages ==="
echo "NOTE: face_recognition installs dlib which compiles from source."
echo "      This takes ~30 minutes on Raspberry Pi 4. Please be patient."
echo ""

pip3 install --upgrade pip

pip3 install \
    face_recognition \
    opencv-python-headless \
    pyttsx3 \
    "python-telegram-bot==20.*" \
    picamera2 \
    pynmea2 \
    pyserial \
    spidev \
    adafruit-circuitpython-ads1x15

echo ""
echo "=== Enabling SPI and Serial interfaces ==="
echo "If not already enabled, run: sudo raspi-config"
echo "  → Interface Options → SPI → Enable"
echo "  → Interface Options → Serial Port → Disable login shell, Enable hardware serial"
echo ""

echo "=== Installation complete ==="
echo "Next step: python3 setup.py"
