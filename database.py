import os
import pickle
import threading
from typing import Dict, List, Optional
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "known_faces", "encodings.pkl")


class FaceDatabase:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, List[np.ndarray]] = {}
        self._load()

    def _load(self):
        if os.path.exists(DB_PATH):
            with open(DB_PATH, "rb") as f:
                self._data = pickle.load(f)

    def _save(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open(DB_PATH, "wb") as f:
            pickle.dump(self._data, f)

    def add_face(self, name: str, encoding: np.ndarray) -> None:
        with self._lock:
            if name not in self._data:
                self._data[name] = []
            self._data[name].append(encoding)
            self._save()

    def get_all(self) -> Dict[str, List[np.ndarray]]:
        with self._lock:
            return dict(self._data)

    def list_names(self) -> List[str]:
        with self._lock:
            return list(self._data.keys())

    def remove(self, name: str) -> bool:
        with self._lock:
            if name in self._data:
                del self._data[name]
                self._save()
                return True
            return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
