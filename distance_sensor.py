"""
VL53L0X Time-of-Flight distance sensor via I2C.

Wiring: VCC=3.3V, GND, SDA→GPIO2, SCL→GPIO3

If the sensor fails to initialise (not wired, wrong ID, etc.) the class stays
in a disabled state and read_cm() returns float('inf') so the detection loop
never fires. It retries silently every 30 s so the system self-heals once the
hardware issue is fixed — no restart needed.
"""

import time
import types

_RETRY_INTERVAL_S = 30


class DistanceSensor:
    def __init__(self):
        self._sensor = None
        self._last_retry: float = 0
        self._try_init()

    def _try_init(self) -> None:
        self._last_retry = time.time()
        try:
            import board
            import busio
            import adafruit_vl53l0x

            _MODEL_ID_REG    = 0xC0
            _REVISION_ID_REG = 0xC2
            _class_read      = adafruit_vl53l0x.VL53L0X._read_u8

            def _lenient_read(self_s, reg: int) -> int:
                if reg == _MODEL_ID_REG:
                    return 0xEE
                if reg == _REVISION_ID_REG:
                    return 0x10
                return _class_read(self_s, reg)

            i2c    = busio.I2C(board.SCL, board.SDA)
            sensor = adafruit_vl53l0x.VL53L0X.__new__(adafruit_vl53l0x.VL53L0X)
            sensor._read_u8 = types.MethodType(_lenient_read, sensor)
            adafruit_vl53l0x.VL53L0X.__init__(sensor, i2c)
            del sensor._read_u8

            self._sensor = sensor
            print("[Sensor] VL53L0X ready.")

        except Exception as e:
            self._sensor = None
            print(f"[Sensor] VL53L0X unavailable ({e}) — auto-detection disabled, retrying in {_RETRY_INTERVAL_S}s.")

    def read_cm(self) -> float:
        """Return distance in cm, or float('inf') if sensor is unavailable."""
        if self._sensor is None:
            if time.time() - self._last_retry >= _RETRY_INTERVAL_S:
                self._try_init()
            return float("inf")

        try:
            mm = self._sensor.range
            if mm <= 0 or mm >= 8190:
                return float("inf")
            return mm / 10.0
        except Exception as e:
            print(f"[Sensor] Read error ({e}) — disabling until next retry.")
            self._sensor = None
            return float("inf")
