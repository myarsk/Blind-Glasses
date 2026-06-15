"""
GPIO switch handler for two physical buttons.

Wiring (active-low, internal pull-up enabled):
  Camera switch: one leg → GND, other leg → GPIO pin (switch_camera_pin)
  GPS switch:    one leg → GND, other leg → GPIO pin (switch_gps_pin)

Camera switch: short press → on_camera
               long press  → on_camera_long   (agent mode toggle)
GPS switch:    short press → on_gps_short
               long press  → on_gps_long
"""

import threading
import time
from typing import Callable, Optional

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False


class SwitchHandler:
    def __init__(
        self,
        camera_pin: int,
        gps_pin: int,
        long_press_seconds: float = 1.0,
        on_camera: Optional[Callable] = None,
        on_camera_long: Optional[Callable] = None,
        on_gps_short: Optional[Callable] = None,
        on_gps_long: Optional[Callable] = None,
    ):
        self._long_press_s = long_press_seconds
        self._on_camera = on_camera
        self._on_camera_long = on_camera_long
        self._on_gps_short = on_gps_short
        self._on_gps_long = on_gps_long
        self._camera_press_time: float | None = None
        self._gps_press_time: float | None = None
        self._camera_pin = camera_pin
        self._gps_pin = gps_pin

        if not _GPIO_AVAILABLE:
            print("[Switches] RPi.GPIO not available — buttons disabled.")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(camera_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(gps_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Both pins use BOTH edges to measure press duration
        GPIO.add_event_detect(
            camera_pin, GPIO.BOTH,
            callback=self._on_camera_edge, bouncetime=50,
        )
        GPIO.add_event_detect(
            gps_pin, GPIO.BOTH,
            callback=self._on_gps_edge, bouncetime=50,
        )
        print(f"[Switches] Ready — camera GPIO{camera_pin}, GPS GPIO{gps_pin}")

    def _fire(self, fn: Optional[Callable]) -> None:
        if fn:
            threading.Thread(target=fn, daemon=True).start()

    def _on_camera_edge(self, channel: int) -> None:
        if not _GPIO_AVAILABLE:
            return
        if GPIO.input(channel) == GPIO.LOW:   # pressed
            self._camera_press_time = time.time()
        else:                                  # released
            if self._camera_press_time is None:
                return
            duration = time.time() - self._camera_press_time
            self._camera_press_time = None
            if duration >= self._long_press_s:
                self._fire(self._on_camera_long)
            else:
                self._fire(self._on_camera)

    def _on_gps_edge(self, channel: int) -> None:
        if not _GPIO_AVAILABLE:
            return
        if GPIO.input(channel) == GPIO.LOW:   # pressed
            self._gps_press_time = time.time()
        else:                                  # released
            if self._gps_press_time is None:
                return
            duration = time.time() - self._gps_press_time
            self._gps_press_time = None
            if duration >= self._long_press_s:
                self._fire(self._on_gps_long)
            else:
                self._fire(self._on_gps_short)

    def cleanup(self) -> None:
        if _GPIO_AVAILABLE:
            GPIO.cleanup()
