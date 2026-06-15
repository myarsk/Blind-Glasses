"""
VL53L0X Time-of-Flight distance sensor via I2C.

Uses the VL53L0X library by John Bryan which works with clone chips.
The adafruit library rejects clones due to a strict model-ID register check.

Wiring: VCC=3.3V, GND, SDA→GPIO2, SCL→GPIO3
Library: pip install smbus2 VL53L0X
"""

import VL53L0X


class DistanceSensor:
    def __init__(self):
        self._tof = VL53L0X.VL53L0X(i2c_bus=1, i2c_address=0x29)
        self._tof.open()
        self._tof.start_ranging(VL53L0X.Vl53l0xAccuracyMode.BETTER)

    def read_cm(self) -> float:
        """Return distance in cm. Returns float('inf') if out of range."""
        mm = self._tof.get_distance()
        if mm <= 0 or mm >= 8190:
            return float("inf")
        return mm / 10.0

    def __del__(self):
        try:
            self._tof.stop_ranging()
            self._tof.close()
        except Exception:
            pass
