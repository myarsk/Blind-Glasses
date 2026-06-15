"""
First-run setup wizard.
Run once on the Pi: python setup.py

Hardware this covers:
  - VL53L0X distance sensor  (I2C: SDA→GPIO2, SCL→GPIO3)
  - Camera switch             (GPIO17)
  - GPS switch                (GPIO27)
  - NEO-6M GPS                (UART: /dev/ttyAMA0)
  - Telegram bot
  - Lightning.ai / proxy API
  - pyttsx3 voice output
"""

import asyncio
import os
import sys
import time

import config as cfg_module
from config import Config


def _ask(prompt: str, default: str = "") -> str:
    if default:
        val = input(f"{prompt} [{default}]: ").strip()
        return val if val else default
    return input(f"{prompt}: ").strip()


def _ask_int(prompt: str, default: int) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("  Enter a whole number.")


def _ask_float(prompt: str, default: float) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Enter a number.")


# ── Step 1: VL53L0X distance sensor ───────────────────────────────────────────
def setup_sensor(cfg: Config) -> None:
    print("\n=== Distance Sensor (VL53L0X) ===")
    print("Wiring: VCC=3.3V  GND  SDA→GPIO2  SCL→GPIO3")

    cfg.detection_distance_cm = _ask_float(
        "Detection distance threshold in cm (VL53L0X range: 3–200)", 60.0
    )

    print("\nTesting sensor — reading 5 values (Ctrl+C to skip):")
    try:
        from distance_sensor import DistanceSensor
        sensor = DistanceSensor()
        for _ in range(5):
            cm = sensor.read_cm()
            label = f"{cm:.1f} cm" if cm != float("inf") else "out of range"
            print(f"  Distance: {label}")
            time.sleep(0.5)
        print("  Sensor OK.")
    except KeyboardInterrupt:
        print("  Skipped.")
    except Exception as e:
        print(f"  Sensor test failed: {e}")
        print("  Check wiring (SDA→GPIO2, SCL→GPIO3) and run: pip install adafruit-circuitpython-vl53l0x")


# ── Step 2: GPIO switches ──────────────────────────────────────────────────────
def setup_switches(cfg: Config) -> None:
    print("\n=== GPIO Switches ===")
    print("Wiring: each switch → one leg to GND, other leg to GPIO pin")

    cfg.switch_camera_pin = _ask_int("Camera switch GPIO pin (BCM)", cfg.switch_camera_pin)
    cfg.switch_gps_pin    = _ask_int("GPS switch GPIO pin (BCM)",    cfg.switch_gps_pin)
    cfg.long_press_seconds = _ask_float("Long press threshold (seconds)", cfg.long_press_seconds)

    print("\nTesting switches — press each button once (Ctrl+C to skip):")
    try:
        import RPi.GPIO as GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(cfg.switch_camera_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(cfg.switch_gps_pin,    GPIO.IN, pull_up_down=GPIO.PUD_UP)

        for label, pin in [("camera", cfg.switch_camera_pin), ("GPS", cfg.switch_gps_pin)]:
            print(f"  Press the {label} switch (GPIO{pin})...", end=" ", flush=True)
            deadline = time.time() + 10
            while time.time() < deadline:
                if GPIO.input(pin) == GPIO.LOW:
                    print("detected!")
                    time.sleep(0.3)
                    break
                time.sleep(0.05)
            else:
                print("timeout — skipping.")
        GPIO.cleanup()
    except KeyboardInterrupt:
        print("\n  Skipped.")
    except ImportError:
        print("  RPi.GPIO not available on this machine — skipping switch test.")
    except Exception as e:
        print(f"  Switch test failed: {e}")


# ── Step 3: GPS ────────────────────────────────────────────────────────────────
def setup_gps(cfg: Config) -> None:
    print("\n=== GPS (NEO-6M) ===")
    print("Wiring: VCC=3.3V  GND  TX→GPIO15(RX)  RX→GPIO14(TX)")
    print("Note: /dev/serial0 is a symlink to the active UART — preferred over ttyAMA0 directly.")
    cfg.gps_serial_port = _ask("Serial port", cfg.gps_serial_port)
    cfg.gps_baud = _ask_int("Baud rate", cfg.gps_baud)

    print("Testing GPS (waiting up to 15s for a fix — Ctrl+C to skip):")
    try:
        import serial
        import pynmea2
        with serial.Serial(cfg.gps_serial_port, cfg.gps_baud, timeout=1) as ser:
            start = time.time()
            while time.time() - start < 15:
                line = ser.readline().decode("ascii", errors="replace").strip()
                try:
                    msg = pynmea2.parse(line)
                    if hasattr(msg, "latitude") and msg.latitude:
                        print(f"  GPS fix: lat={msg.latitude:.5f}  lon={msg.longitude:.5f}")
                        break
                except pynmea2.ParseError:
                    pass
            else:
                print("  No fix yet — normal outdoors, may take a few minutes on first use.")
    except KeyboardInterrupt:
        print("  Skipped.")
    except Exception as e:
        print(f"  GPS test failed: {e}")


# ── Step 4: Telegram ───────────────────────────────────────────────────────────
def setup_telegram(cfg: Config) -> None:
    print("\n=== Telegram Bot ===")
    print("Create a bot at @BotFather if you haven't already.")
    cfg.telegram_token = _ask("Telegram bot token", cfg.telegram_token)

    print("\nHave your partner open Telegram and send /start to your bot.")
    print("Waiting for your partner to message the bot (Ctrl+C to skip auto-detect)...")
    try:
        asyncio.run(_wait_for_start(cfg))
    except KeyboardInterrupt:
        print("  Skipped auto-detect.")
        cfg.partner_chat_id = _ask_int("Enter partner's chat_id manually", cfg.partner_chat_id)


async def _wait_for_start(cfg: Config) -> None:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    found_event = asyncio.Event()

    async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cfg.partner_chat_id = update.effective_chat.id
        await update.message.reply_text(
            "Connected! You will receive image captures and GPS alerts from the blind glasses here."
        )
        print(f"\n  Partner chat_id captured: {cfg.partner_chat_id}")
        found_event.set()

    app = Application.builder().token(cfg.telegram_token).build()
    app.add_handler(CommandHandler("start", on_start))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await found_event.wait()
    await app.updater.stop()
    await app.stop()
    await app.shutdown()


# ── Step 5: Voice ──────────────────────────────────────────────────────────────
def setup_voice() -> None:
    print("\n=== Voice Output ===")
    print("Testing TTS (requires espeak-ng: sudo apt install espeak-ng)...")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        engine.say("Blind glasses setup complete. System is ready.")
        engine.runAndWait()
        print("  Voice OK.")
    except Exception as e:
        print(f"  TTS test failed: {e}")


# ── Step 6: API connectivity ───────────────────────────────────────────────────
def setup_api(cfg: Config) -> None:
    print("\n=== Vision AI API ===")
    print(f"Current endpoint: {cfg.api_base_url}")
    new_url = _ask("API base URL (Enter to keep)", cfg.api_base_url)
    cfg.api_base_url = new_url

    print("Testing API connection (sending a short text request)...")
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=cfg.api_base_url.rstrip("/") + "/",
            api_key=cfg.lightning_api_key,
        )
        resp = client.chat.completions.create(
            model="google/gemini-2.5-flash-lite-preview-06-17",
            messages=[{"role": "user", "content": [{"type": "text", "text": "Reply with just the word OK."}]}],
        )
        print(f"  API OK: {resp.choices[0].message.content.strip()}")
    except KeyboardInterrupt:
        print("  Skipped.")
    except Exception as e:
        print(f"  API test failed: {e}")
        print("  Check your proxy is running or use the direct Lightning.ai URL.")


# ── Step 7: Final Telegram test ────────────────────────────────────────────────
async def _send_test_message(cfg: Config) -> None:
    from telegram import Bot
    bot = Bot(token=cfg.telegram_token)
    await bot.send_message(
        chat_id=cfg.partner_chat_id,
        text=(
            "Blind Glasses setup complete.\n"
            "You will receive image captures here when the camera button is pressed, "
            "and GPS location when the GPS button is pressed."
        ),
    )
    print("  Test message sent.")


def setup_telegram_test(cfg: Config) -> None:
    print("\nSending Telegram test message...")
    try:
        asyncio.run(_send_test_message(cfg))
    except Exception as e:
        print(f"  Could not send test message: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Blind Glasses — First-Run Setup")
    print("=" * 50)

    cfg = cfg_module.load()

    setup_sensor(cfg)
    setup_switches(cfg)
    setup_gps(cfg)
    setup_telegram(cfg)
    setup_voice()
    setup_api(cfg)

    cfg_module.save(cfg)
    print("\n✓ Configuration saved to config.json")

    if cfg.partner_chat_id:
        setup_telegram_test(cfg)

    print("✓ Run 'python main.py' to start the system.")
    print("✓ To start on boot: sudo systemctl enable glasses")


if __name__ == "__main__":
    main()
