"""
First-run setup wizard.
Run once on the Pi: python setup.py
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
            print("  Enter a number.")


def _ask_float(prompt: str, default: float) -> float:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("  Enter a number.")


# ── Step 1: ADC / distance sensor ─────────────────────────────────────────────
def setup_sensor(cfg: Config) -> None:
    print("\n=== Distance Sensor (SHARP GP2Y0A41SK0F) ===")
    print("ADC types: 1) MCP3008 (SPI)  2) ADS1115 (I2C)")
    choice = _ask("Choose ADC type [1/2]", "1")
    cfg.adc_type = "mcp3008" if choice != "2" else "ads1115"
    cfg.adc_channel = _ask_int("ADC channel (0–7 for MCP3008, 0–3 for ADS1115)", 0)
    print(f"  → Using {cfg.adc_type.upper()}, channel {cfg.adc_channel}")

    cfg.detection_distance_cm = _ask_float(
        "Detection distance threshold in cm (SHARP range: 4–30)", 30.0
    )

    # Live test
    print("\nTesting sensor — reading 5 values (press Ctrl+C to skip):")
    try:
        from distance_sensor import DistanceSensor
        sensor = DistanceSensor(cfg)
        for _ in range(5):
            cm = sensor.read_cm()
            print(f"  Distance: {cm:.1f} cm")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("  Skipped.")
    except Exception as e:
        print(f"  Sensor test failed: {e}")
        print("  Check wiring and ADC type, then re-run setup.")


# ── Step 2: GPS ────────────────────────────────────────────────────────────────
def setup_gps(cfg: Config) -> None:
    print("\n=== GPS (NEO-6M) ===")
    cfg.gps_serial_port = _ask("Serial port", "/dev/ttyAMA0")
    cfg.gps_baud = _ask_int("Baud rate", 9600)
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
                        print(f"  GPS fix: lat={msg.latitude:.5f} lon={msg.longitude:.5f}")
                        break
                except pynmea2.ParseError:
                    pass
            else:
                print("  No fix yet (normal outdoors, may take a few minutes on first use).")
    except KeyboardInterrupt:
        print("  Skipped.")
    except Exception as e:
        print(f"  GPS test failed: {e}")


# ── Step 3: Telegram ───────────────────────────────────────────────────────────
def setup_telegram(cfg: Config) -> None:
    print("\n=== Telegram Bot ===")
    print("Create a bot at @BotFather if you haven't already.")
    cfg.telegram_token = _ask("Telegram bot token")

    print("\nHave your partner open Telegram and send /start to your bot.")
    print("Waiting for your partner to message the bot (Ctrl+C to skip auto-detect)...")
    try:
        asyncio.run(_wait_for_start(cfg))
    except KeyboardInterrupt:
        print("  Skipped auto-detect.")
        cfg.partner_chat_id = _ask_int("Enter partner's chat_id manually", 0)


async def _wait_for_start(cfg: Config) -> None:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes

    found_event = asyncio.Event()

    async def on_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cfg.partner_chat_id = update.effective_chat.id
        await update.message.reply_text(
            "Connected! You'll receive alerts here when an unknown person is detected."
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


# ── Step 4: Voice ──────────────────────────────────────────────────────────────
def setup_voice() -> None:
    print("\n=== Voice Output ===")
    print("Testing TTS...")
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.say("Setup complete. Blind glasses system is ready.")
        engine.runAndWait()
        print("  Voice OK.")
    except Exception as e:
        print(f"  TTS test failed: {e}. Install espeak: sudo apt install espeak")


# ── Step 5: Telegram test message ─────────────────────────────────────────────
async def _send_test_message(cfg: Config) -> None:
    from telegram import Bot
    bot = Bot(token=cfg.telegram_token)
    await bot.send_message(
        chat_id=cfg.partner_chat_id,
        text="Blind Glasses setup complete. Alerts will appear here."
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
    setup_gps(cfg)
    setup_telegram(cfg)
    setup_voice()

    if cfg.partner_chat_id:
        setup_telegram_test(cfg)

    cfg_module.save(cfg)
    print("\n✓ Configuration saved to config.json")
    print("✓ Run 'python main.py' to start the system.")
    print("✓ To start on boot: sudo systemctl enable glasses")


if __name__ == "__main__":
    main()
