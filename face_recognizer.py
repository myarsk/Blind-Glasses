import os
import numpy as np
from typing import List, Tuple, Optional
import face_recognition
import cv2

from database import FaceDatabase
from config import Config

# (name_or_None, bbox_tuple, face_crop_ndarray, encoding_ndarray)
RecognitionResult = Tuple[Optional[str], tuple, np.ndarray, np.ndarray]


class FaceRecognizer:
    def __init__(self, db: FaceDatabase, cfg: Config):
        self._db = db
        self._tolerance = cfg.face_recognition_tolerance

    def detect_and_recognize(self, frame: np.ndarray) -> List[RecognitionResult]:
        """
        Detect all faces in frame and match against known faces.
        frame: BGR numpy array from OpenCV / picamera2.
        Returns list of (name_or_None, bbox, crop, encoding).
        """
        # face_recognition expects RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        locations = face_recognition.face_locations(rgb, model="hog")
        if not locations:
            return []

        encodings = face_recognition.face_encodings(rgb, locations)
        known = self._db.get_all()
        known_names = list(known.keys())
        known_encs = [enc for encs in known.values() for enc in encs]
        known_labels = [name for name, encs in known.items() for _ in encs]

        results: List[RecognitionResult] = []
        for encoding, location in zip(encodings, locations):
            top, right, bottom, left = location
            crop = frame[top:bottom, left:right]

            name = None
            if known_encs:
                distances = face_recognition.face_distance(known_encs, encoding)
                best_idx = int(np.argmin(distances))
                if distances[best_idx] <= self._tolerance:
                    name = known_labels[best_idx]

            results.append((name, location, crop, encoding))

        return results
