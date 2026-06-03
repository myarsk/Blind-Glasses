"""
Blind Glasses — main loop.
Run: python main.py
Stop: Ctrl+C
"""

import os
import time
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

CAPTURED_DIR = os.path.join(os.path.dirname(__file__), "captured")


def _init_camera(camera_index: int):
    """Try picamera2 first, fall back to OpenCV VideoCapture."""
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_preview_configuration(
            main={"format": "BGR888", "size": (640, 480)}
        ))
        cam.start()
        time.sleep(0.5)
        return cam, "picamera2"
    except Exception:
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError("No camera found. Check connection and camera_index in config.json.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return cap, "opencv"


def _capture_frame(cam, cam_type: str) -> np.ndarray:
    if cam_type == "picamera2":
        return cam.capture_array()
    ret, frame = cam.read()
    if not ret:
        raise RuntimeError("Failed to read frame from camera.")
    return frame


def _face_key(name, encoding: np.ndarray) -> str:
    if name:
        return name
    h = hashlib.md5(encoding.tobytes()).hexdigest()
    return h


def _save_crop(crop: np.ndarray) -> str:
    os.makedirs(CAPTURED_DIR, exist_ok=True)
    path = os.path.join(CAPTURED_DIR, f"unknown_{int(time.time())}.jpg")
    cv2.imwrite(path, crop)
    return path


def main():
    cfg = cfg_module.load()

    if not cfg.telegram_token or not cfg.partner_chat_id:
        print("Config incomplete. Run: python setup.py")
        return

    print("[Boot] Initializing modules...")
    db = FaceDatabase()
    sensor = DistanceSensor(cfg)
    gps = GPSTracker(cfg)
    voice = VoiceOutput(cfg)
    bot = TelegramBot(cfg, db, voice, gps_fn=gps.get_location)
    recognizer = FaceRecognizer(db, cfg)
    cam, cam_type = _init_camera(cfg.camera_index)

    print(f"[Boot] Camera: {cam_type}  |  Known faces: {len(db)}")
    print(f"[Boot] Detection distance: {cfg.detection_distance_cm} cm")
    print("[Boot] Running — press Ctrl+C to stop.\n")

    cooldowns: dict = {}  # face_key -> last_announced_time

    try:
        while True:
            dist_cm = sensor.read_cm()

            if dist_cm <= cfg.detection_distance_cm:
                frame = _capture_frame(cam, cam_type)
                results = recognizer.detect_and_recognize(frame)

                for name, _bbox, crop, encoding in results:
                    key = _face_key(name, encoding)
                    elapsed = time.time() - cooldowns.get(key, 0)
                    if elapsed < cfg.cooldown_seconds:
                        continue

                    if name:
                        print(f"[Recognized] {name}")
                        voice.speak(f"{name} is nearby")
                    else:
                        print("[Unknown] Sending Telegram alert")
                        location = gps.get_location()
                        path = _save_crop(crop)
                        bot.send_unknown_alert(path, encoding, location)
                        voice.speak("Unknown person detected nearby")

                    cooldowns[key] = time.time()

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n[Exit] Stopping.")
    finally:
        if cam_type == "opencv":
            cam.release()
        else:
            cam.stop()


if __name__ == "__main__":
    main()
