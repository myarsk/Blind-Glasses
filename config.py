import json
import os
from dataclasses import dataclass, field, asdict

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


@dataclass
class Config:
    telegram_token: str = ""
    partner_chat_id: int = 0
    detection_distance_cm: float = 30.0
    cooldown_seconds: int = 600
    camera_index: int = 0
    gps_serial_port: str = "/dev/ttyAMA0"
    gps_baud: int = 9600
    face_similarity_threshold: float = 0.4  # cosine similarity (0–1, higher = stricter)
    switch_camera_pin: int = 17   # BCM GPIO pin for camera button
    switch_gps_pin: int = 27      # BCM GPIO pin for GPS button
    long_press_seconds: float = 1.0
    lightning_api_key: str = "sk-lit-be40652e-72d3-4f4f-8bcb-f83dd3a08e37"
    api_base_url: str = "http://abd.softup.agency:8418"  # set to proxy URL; or https://lightning.ai/api/v1 for direct


def load() -> Config:
    if not os.path.exists(CONFIG_PATH):
        return Config()
    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)
    cfg = Config()
    for k, v in data.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    return cfg


def save(cfg: Config) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(asdict(cfg), f, indent=2)
