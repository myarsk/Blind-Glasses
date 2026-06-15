"""
VL53L0X Time-of-Flight distance sensor via I2C.

Wiring: VCC=3.3V, GND, SDA→GPIO2, SCL→GPIO3
Library: pip install adafruit-circuitpython-vl53l0x
"""

import math


class DistanceSensor:
    def __init__(self):
        import board
        import busio
        import adafruit_vl53l0x
        i2c = busio.I2C(board.SCL, board.SDA)
        self._sensor = adafruit_vl53l0x.VL53L0X(i2c)

    def read_cm(self) -> float:
        """Return distance in cm. Returns float('inf') if out of range."""
        mm = self._sensor.range
        if mm <= 0 or mm >= 8190:   # 8190 mm = sensor's out-of-range sentinel
            return float("inf")
        return mm / 10.0
