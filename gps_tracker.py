"""
NEO-6M GPS module over UART.

Wiring: GPS TX → Pi GPIO15 (RXD), GPS RX → Pi GPIO14 (TXD), VCC=3.3V, GND.
Enable serial port in raspi-config → Interface Options → Serial.
Disable serial console (keep hardware serial enabled).
"""

import threading
import time
from typing import Optional, Tuple

import serial
import pynmea2

from config import Config


class GPSTracker:
    def __init__(self, cfg: Config):
        self._port = cfg.gps_serial_port
        self._baud = cfg.gps_baud
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self):
        while True:
            try:
                with serial.Serial(self._port, self._baud, timeout=1) as ser:
                    while True:
                        line = ser.readline().decode("ascii", errors="replace").strip()
                        self._parse(line)
            except serial.SerialException as e:
                print(f"[GPS] Serial error: {e} — retrying in 5s")
                time.sleep(5)

    def _parse(self, line: str):
        try:
            msg = pynmea2.parse(line)
            if hasattr(msg, "latitude") and hasattr(msg, "longitude"):
                if msg.latitude and msg.longitude:
                    with self._lock:
                        self._lat = msg.latitude
                        self._lon = msg.longitude
        except pynmea2.ParseError:
            pass

    def get_location(self) -> Optional[Tuple[float, float]]:
        """Returns (lat, lon) or None if no GPS fix yet."""
        with self._lock:
            if self._lat is not None and self._lon is not None:
                return (self._lat, self._lon)
            return None
