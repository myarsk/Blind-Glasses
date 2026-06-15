"""
GPIO switch handler for two physical buttons.

Wiring (active-low, internal pull-up enabled):
  Camera switch: one leg → GND, other leg → GPIO pin (switch_camera_pin)
  GPS switch:    one leg → GND, other leg → GPIO pin (switch_gps_pin)

Camera switch: short press → on_camera
               long press  → on_camera_long   (agent mode toggle)
GPS switch:    short press → on_gps_short
               long press  → on_gps_long

Uses polling rather than add_event_detect because GPIO.BOTH edge detection
is broken on newer Raspberry Pi OS kernels with RPi.GPIO.
"""

import threading
import time
from typing import Callable, Optional

try:
    import RPi.GPIO as GPIO
    _GPIO_AVAILABLE = True
except ImportError:
    _GPIO_AVAILABLE = False

_POLL_HZ = 50          # 50 samples/second → 20 ms resolution
_DEBOUNCE_S = 0.02     # ignore transitions shorter than 20 ms


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
        self._camera_pin = camera_pin
        self._gps_pin = gps_pin
        self._running = False

        if not _GPIO_AVAILABLE:
            print("[Switches] RPi.GPIO not available — buttons disabled.")
            return

        GPIO.setwarnings(False)
        GPIO.cleanup()
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(camera_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(gps_pin,    GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        print(f"[Switches] Ready — camera GPIO{camera_pin}, GPS GPIO{gps_pin}")

    def _fire(self, fn: Optional[Callable]) -> None:
        if fn:
            threading.Thread(target=fn, daemon=True).start()

    def _poll_loop(self) -> None:
        """
        Poll both pins at _POLL_HZ. Track HIGH→LOW (press) and LOW→HIGH
        (release) transitions with a simple debounce. On release, decide
        short vs long press by elapsed duration.
        """
        pins = [self._camera_pin, self._gps_pin]
        # last confirmed state (HIGH = not pressed, LOW = pressed)
        state       = {p: GPIO.HIGH for p in pins}
        press_time  = {p: None       for p in pins}
        # time of the last raw transition (for debounce)
        raw_change  = {p: 0.0        for p in pins}
        raw_state   = {p: GPIO.HIGH  for p in pins}

        interval = 1.0 / _POLL_HZ

        while self._running:
            now = time.monotonic()
            for pin in pins:
                reading = GPIO.input(pin)

                # Debounce: only accept stable readings
                if reading != raw_state[pin]:
                    raw_state[pin]  = reading
                    raw_change[pin] = now
                    continue

                if now - raw_change[pin] < _DEBOUNCE_S:
                    continue   # still within debounce window

                if reading == state[pin]:
                    continue   # no confirmed transition

                # Confirmed transition
                state[pin] = reading
                if reading == GPIO.LOW:          # pressed
                    press_time[pin] = now
                else:                            # released
                    if press_time[pin] is None:
                        continue
                    duration = now - press_time[pin]
                    press_time[pin] = None
                    self._dispatch(pin, duration)

            time.sleep(interval)

    def _dispatch(self, pin: int, duration: float) -> None:
        long = duration >= self._long_press_s
        if pin == self._camera_pin:
            self._fire(self._on_camera_long if long else self._on_camera)
        else:
            self._fire(self._on_gps_long if long else self._on_gps_short)

    def cleanup(self) -> None:
        self._running = False
        if _GPIO_AVAILABLE:
            GPIO.cleanup()
