"""
VL53L0X Time-of-Flight distance sensor via I2C.

The adafruit library rejects clone chips (e.g. CJUL53L0XV2) because their
model-ID registers differ from genuine ST chips. This wrapper patches only
those two register reads during __init__ so the rest of the calibration
sequence runs normally.

Wiring: VCC=3.3V, GND, SDA→GPIO2, SCL→GPIO3
"""

import board
import busio
import adafruit_vl53l0x

# Registers the adafruit lib checks against fixed expected values
_MODEL_ID_REG    = 0xC0   # adafruit expects 0xEE
_REVISION_ID_REG = 0xC2   # adafruit expects 0x10


class DistanceSensor:
    def __init__(self):
        original_read = adafruit_vl53l0x.VL53L0X._read_u8

        def _lenient_read(self_sensor, reg):
            # Return the values adafruit expects for ID registers so clones pass
            if reg == _MODEL_ID_REG:
                return 0xEE
            if reg == _REVISION_ID_REG:
                return 0x10
            return original_read(self_sensor, reg)

        adafruit_vl53l0x.VL53L0X._read_u8 = _lenient_read
        try:
            i2c = busio.I2C(board.SCL, board.SDA)
            self._sensor = adafruit_vl53l0x.VL53L0X(i2c)
        finally:
            # Always restore so normal reads work correctly after init
            adafruit_vl53l0x.VL53L0X._read_u8 = original_read

    def read_cm(self) -> float:
        """Return distance in cm. Returns float('inf') if out of range."""
        mm = self._sensor.range
        if mm <= 0 or mm >= 8190:
            return float("inf")
        return mm / 10.0
