"""
SHARP GP2Y0A41SK0F IR distance sensor via ADC.

Wiring:
  MCP3008 (SPI): VCC=3.3V, GND, CLK=GPIO11, MISO=GPIO9, MOSI=GPIO10, CS=GPIO8
  ADS1115 (I2C): VCC=3.3V, GND, SDA=GPIO2, SCL=GPIO3, ADDR=GND (addr 0x48)
  SHARP Vout → ADC channel 0 (or configured channel)
"""

import math
from config import Config


def _voltage_to_cm_sharp_0a41(voltage: float) -> float:
    """
    Convert SHARP GP2Y0A41SK0F output voltage to distance in cm.
    Valid range: 4–30 cm. Based on datasheet curve fit.
    Returns float('inf') when out of range / no object.
    """
    if voltage < 0.4:
        return float("inf")
    # Datasheet inverse curve: d = 27.728 * V^(-1.2045)  (empirical fit)
    try:
        cm = 27.728 * math.pow(voltage, -1.2045)
    except (ValueError, ZeroDivisionError):
        return float("inf")
    return cm


class DistanceSensor:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._adc = None
        self._init_adc()

    def _init_adc(self):
        if self._cfg.adc_type == "mcp3008":
            try:
                import spidev
                self._spi = spidev.SpiDev()
                self._spi.open(0, 0)
                self._spi.max_speed_hz = 1350000
                self._adc = "mcp3008"
            except Exception as e:
                raise RuntimeError(f"MCP3008 init failed: {e}")
        elif self._cfg.adc_type == "ads1115":
            try:
                import board
                import busio
                import adafruit_ads1x15.ads1115 as ADS
                from adafruit_ads1x15.analog_in import AnalogIn
                i2c = busio.I2C(board.SCL, board.SDA)
                ads = ADS.ADS1115(i2c)
                channel_map = [ADS.P0, ADS.P1, ADS.P2, ADS.P3]
                self._adc_channel = AnalogIn(ads, channel_map[self._cfg.adc_channel])
                self._adc = "ads1115"
            except Exception as e:
                raise RuntimeError(f"ADS1115 init failed: {e}")
        else:
            raise ValueError(f"Unknown adc_type: {self._cfg.adc_type}")

    def _read_mcp3008_voltage(self) -> float:
        ch = self._cfg.adc_channel
        r = self._spi.xfer2([1, (8 + ch) << 4, 0])
        raw = ((r[1] & 3) << 8) | r[2]     # 10-bit value (0–1023)
        return raw * 3.3 / 1023.0           # convert to volts (3.3V reference)

    def _read_ads1115_voltage(self) -> float:
        return self._adc_channel.voltage

    def read_cm(self) -> float:
        """Return distance in cm. Returns float('inf') if no object in range."""
        if self._adc == "mcp3008":
            voltage = self._read_mcp3008_voltage()
        else:
            voltage = self._read_ads1115_voltage()
        return _voltage_to_cm_sharp_0a41(voltage)
