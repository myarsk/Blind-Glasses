"""
Blind Glasses — main entry point.
Run: python main.py
Stop: Ctrl+C

Hardware behaviour:
  VL53L0X (auto)      : proximity trigger → face recognition
                        known person  → voice announces name
                        unknown person → silent
  Camera switch short : capture image → send via Telegram
  Camera switch long  : toggle agent mode (vision AI conversation)
  GPS switch short    : send GPS location via Telegram
  GPS switch long     : send GPS location + capture image via Telegram
"""

import os
import time
import threading
import hashlib

import cv2
import numpy as np

import config as cfg_module
from database import FaceDatabase
from distance_sensor import DistanceSensor
from face_recognizer import FaceRecognizer
from gps_tracker import GPSTracker
from voice_output import VoiceOutput
from telegram_bot import TelegramBot
from switches import SwitchHandler
from agent import VisionAgent

CAPTURED_DIR = os.path.join(os.path.dirname(__file__), "captured")


def _init_camera(camera_index: int):
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_preview_configuration(
            main={"format": "BGR888", "size": (640, 480)}
        ))
        cam.start()
        time.sleep(0.5)
        return cam, "picamera2"
    except Exception as e:
        # Surface why picamera2 was unavailable — on a Pi this is usually a venv
        # created without --system-site-packages, so libcamera isn't importable.
        # OpenCV can open a CSI camera device but often can't read frames from it.
        print(f"[Camera] picamera2 unavailable ({e}) — falling back to OpenCV.")
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError("No camera found. Check connection and camera_index in config.json.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        # Warm up: some cameras return empty frames for the first reads.
        for _ in range(5):
            ok, _frame = cap.read()
            if ok:
                break
            time.sleep(0.1)
        return cap, "opencv"


def _capture_frame(cam, cam_type: str) -> np.ndarray:
    if cam_type == "picamera2":
        return cam.capture_array()
    # Retry a few times — transient read failures are common over USB/CSI.
    for _ in range(3):
        ret, frame = cam.read()
        if ret and frame is not None:
            return frame
        time.sleep(0.05)
    raise RuntimeError("Failed to read frame from camera.")


def _save_image(img: np.ndarray, prefix: str = "capture") -> str:
    os.makedirs(CAPTURED_DIR, exist_ok=True)
    path = os.path.join(CAPTURED_DIR, f"{prefix}_{int(time.time())}.jpg")
    cv2.imwrite(path, img)
    return path


def main():
    cfg = cfg_module.load()

    if not cfg.telegram_token or not cfg.partner_chat_id:
        print("Config incomplete. Run: python setup.py")
        return

    print("[Boot] Initializing modules...")
    db = FaceDatabase()
    sensor = DistanceSensor()
    gps = GPSTracker(cfg)
    voice = VoiceOutput(cfg)
    recognizer = FaceRecognizer(db, cfg)
    bot = TelegramBot(cfg, db, voice, recognizer=recognizer, gps_fn=gps.get_location)
    cam, cam_type = _init_camera(cfg.camera_index)

    # Serialize camera access across detection thread, button callbacks, and agent
    cam_lock = threading.Lock()

    def locked_capture() -> np.ndarray:
        with cam_lock:
            return _capture_frame(cam, cam_type)

    agent = VisionAgent(
        api_key=cfg.lightning_api_key,
        api_base_url=cfg.api_base_url,
        capture_fn=locked_capture,
        speak_fn=voice.speak,
    )

    print(f"[Boot] Camera: {cam_type}  |  Known faces: {len(db)}")
    print(f"[Boot] Detection distance: {cfg.detection_distance_cm} cm")
    print("[Boot] Running — press Ctrl+C to stop.\n")

    # ── Auto-detection thread (known faces only) ───────────────────────────────

    cooldowns: dict = {}   # name → last_announced_time

    def detection_loop() -> None:
        while True:
            try:
                # Skip detection while agent is active (it's already using the camera)
                if agent.is_active:
                    time.sleep(0.5)
                    continue

                dist_cm = sensor.read_cm()
                if dist_cm <= cfg.detection_distance_cm:
                    frame = locked_capture()
                    results = recognizer.detect_and_recognize(frame)
                    for name, _bbox, _crop, encoding in results:
                        if not name:
                            continue   # unknown — ignore
                        elapsed = time.time() - cooldowns.get(name, 0)
                        if elapsed < cfg.cooldown_seconds:
                            continue
                        print(f"[Auto] Recognized: {name}")
                        voice.speak(f"{name} is nearby")
                        cooldowns[name] = time.time()
            except Exception as e:
                print(f"[Auto] Error: {e}")
            time.sleep(0.1)

    threading.Thread(target=detection_loop, daemon=True).start()

    # ── Button callbacks ───────────────────────────────────────────────────────

    def do_capture_and_send() -> None:
        if agent.is_active:
            return   # don't interfere while agent is talking
        print("[Camera] Short press — capturing...")
        frame = locked_capture()
        results = recognizer.detect_and_recognize(frame)
        if results:
            name, _bbox, crop, encoding = results[0]
            path = _save_image(crop, "capture")
            if name:
                print(f"[Camera] Recognized: {name}")
                voice.speak(f"{name} is in front of you")
                bot.send_capture(path, known_name=name)
            else:
                print("[Camera] Unknown person")
                voice.speak("Unknown person in front of you")
                bot.send_capture(path, encoding=encoding)
        else:
            path = _save_image(frame, "capture")
            print("[Camera] No face detected")
            voice.speak("Image captured")
            bot.send_capture(path)

    def do_toggle_agent() -> None:
        print("[Camera] Long press — toggling agent mode")
        agent.toggle()

    def do_gps() -> None:
        print("[GPS] Short press — sending location...")
        location = gps.get_location()
        if location:
            bot.send_gps_location(location)
            voice.speak("Location sent")
        else:
            voice.speak("No GPS signal yet")

    def do_gps_and_capture() -> None:
        print("[GPS] Long press — sending location and image...")
        do_gps()
        do_capture_and_send()

    switches = SwitchHandler(
        camera_pin=cfg.switch_camera_pin,
        gps_pin=cfg.switch_gps_pin,
        long_press_seconds=cfg.long_press_seconds,
        on_camera=do_capture_and_send,
        on_camera_long=do_toggle_agent,
        on_gps_short=do_gps,
        on_gps_long=do_gps_and_capture,
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Exit] Stopping.")
    finally:
        agent.stop()
        switches.cleanup()
        if cam_type == "opencv":
            cam.release()
        else:
            cam.stop()


if __name__ == "__main__":
    main()
