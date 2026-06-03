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
    adc_type: str = "mcp3008"   # "mcp3008" or "ads1115"
    adc_channel: int = 0
    face_recognition_tolerance: float = 0.5


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
