import json
import os
from dataclasses import dataclass, field, asdict
from typing import List

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")



@dataclass
class Config:
    # ── Access control ────────────────────────────────────────────────────────
    owner_chat_id: int = 0          # set on first /start; 0 = not yet claimed
    owner_username: str = ""        # Telegram @username of the owner (may be empty)
    allowed_chat_ids: List[int] = field(default_factory=list)
    allowed_usernames: List[str] = field(default_factory=list)  # parallel list of @usernames

    # ── Localisation ──────────────────────────────────────────────────────────
    language: str = "en"            # "en" or "ar"

    # ── Hardware ──────────────────────────────────────────────────────────────
    detection_distance_cm: float = 30.0
    cooldown_seconds: int = 600
    camera_index: int = 0
    gps_serial_port: str = "/dev/serial0"
    gps_baud: int = 9600
    switch_camera_pin: int = 17     # BCM GPIO pin for camera button
    switch_gps_pin: int = 27        # BCM GPIO pin for GPS button
    long_press_seconds: float = 1.0

    # ── Face recognition ──────────────────────────────────────────────────────
    face_similarity_threshold: float = 0.4  # cosine similarity (0–1, higher = stricter)


    # ── Helpers ───────────────────────────────────────────────────────────────
    def all_chat_ids(self) -> List[int]:
        """All chat IDs that are authorised to interact with the bot."""
        ids = list(self.allowed_chat_ids)
        if self.owner_chat_id:
            ids.insert(0, self.owner_chat_id)
        return ids

    def is_authorised(self, chat_id: int) -> bool:
        return chat_id == self.owner_chat_id or chat_id in self.allowed_chat_ids

    def is_owner(self, chat_id: int) -> bool:
        return chat_id == self.owner_chat_id

    def add_user(self, chat_id: int, username: str = "") -> bool:
        """Add a user. Returns False if already present."""
        if chat_id in self.allowed_chat_ids:
            return False
        self.allowed_chat_ids.append(chat_id)
        self.allowed_usernames.append(username.lstrip("@"))
        return True

    def remove_user_by_id(self, chat_id: int) -> bool:
        if chat_id not in self.allowed_chat_ids:
            return False
        idx = self.allowed_chat_ids.index(chat_id)
        self.allowed_chat_ids.pop(idx)
        self.allowed_usernames.pop(idx)
        return True

    def remove_user_by_username(self, username: str) -> bool:
        uname = username.lstrip("@").lower()
        for i, u in enumerate(self.allowed_usernames):
            if u.lower() == uname:
                self.allowed_chat_ids.pop(i)
                self.allowed_usernames.pop(i)
                return True
        return False


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
