"""
VL53L0X Time-of-Flight distance sensor via I2C.

Clone chips (e.g. CJUL53L0XV2) are functionally identical to genuine ST chips
but return different bytes in the model-ID registers, causing adafruit's library
to reject them. We patch _read_u8 at the INSTANCE level (not class level) so
the fake ID is only visible during __init__; all subsequent reads are real.

Wiring: VCC=3.3V, GND, SDA→GPIO2, SCL→GPIO3
"""

import types

import board
import busio
import adafruit_vl53l0x

_MODEL_ID_REG    = 0xC0   # adafruit expects 0xEE
_REVISION_ID_REG = 0xC2   # adafruit expects 0x10
_CLASS_READ      = adafruit_vl53l0x.VL53L0X._read_u8


def _lenient_read(self, reg: int) -> int:
    """Return spoofed values for ID registers; real values for everything else."""
    if reg == _MODEL_ID_REG:
        return 0xEE
    if reg == _REVISION_ID_REG:
        return 0x10
    return _CLASS_READ(self, reg)


class DistanceSensor:
    def __init__(self):
        i2c = busio.I2C(board.SCL, board.SDA)

        # Allocate instance without calling __init__ so we can pre-patch it
        sensor = adafruit_vl53l0x.VL53L0X.__new__(adafruit_vl53l0x.VL53L0X)

        # Bind the lenient reader to this specific instance (hides the class method)
        sensor._read_u8 = types.MethodType(_lenient_read, sensor)

        # Now run the real __init__ — the ID check sees our faked register values
        adafruit_vl53l0x.VL53L0X.__init__(sensor, i2c)

        # Remove instance override; subsequent reads go through the real class method
        del sensor._read_u8

        self._sensor = sensor

    def read_cm(self) -> float:
        """Return distance in cm. Returns float('inf') if out of range."""
        mm = self._sensor.range
        if mm <= 0 or mm >= 8190:
            return float("inf")
        return mm / 10.0
