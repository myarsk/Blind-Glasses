"""
Face detection and recognition using InsightFace (ONNX-based).

Model: buffalo_sc — compact, fast enough for Pi 4.
Downloads automatically on first run to ~/.insightface/models/buffalo_sc/

Similarity metric: cosine similarity on L2-normalised 512-dim embeddings.
Threshold default: 0.4 (same person scores ~0.5–0.9, strangers ~0.1–0.3).
"""

from typing import List, Optional, Tuple

import numpy as np
from insightface.app import FaceAnalysis

from config import Config
from database import FaceDatabase

# (name_or_None, bbox_tuple, face_crop_ndarray, encoding_ndarray)
RecognitionResult = Tuple[Optional[str], tuple, np.ndarray, np.ndarray]


class FaceRecognizer:
    def __init__(self, db: FaceDatabase, cfg: Config):
        self._db = db
        self._threshold = cfg.face_similarity_threshold
        self._app = FaceAnalysis(
            name="buffalo_sc",
            providers=["CPUExecutionProvider"],
        )
        # 320×320 keeps inference fast on Pi 4; raise to 640×640 for better accuracy
        self._app.prepare(ctx_id=0, det_size=(320, 320))

    def detect_and_recognize(self, frame: np.ndarray) -> List[RecognitionResult]:
        """
        frame: BGR numpy array (OpenCV / picamera2 format — no RGB conversion needed).
        Returns list of (name_or_None, bbox, crop, encoding).
        """
        faces = self._app.get(frame)
        if not faces:
            return []

        known = self._db.get_all()
        results: List[RecognitionResult] = []

        for face in faces:
            x1, y1, x2, y2 = face.bbox.astype(int)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
            crop = frame[y1:y2, x1:x2]
            encoding = face.embedding  # 512-dim, already L2-normalised

            name = _best_match(encoding, known, self._threshold)
            # Preserve (top, right, bottom, left) convention used by callers
            results.append((name, (y1, x2, y2, x1), crop, encoding))

        return results

    def get_encoding(self, img_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Return the first face encoding found in the image, or None."""
        faces = self._app.get(img_bgr)
        return faces[0].embedding if faces else None


def _best_match(
    encoding: np.ndarray,
    known: dict,
    threshold: float,
) -> Optional[str]:
    """Cosine similarity match. Embeddings are pre-normalised so dot == cosine."""
    best_name: Optional[str] = None
    best_sim: float = -1.0
    for name, encodings in known.items():
        for known_enc in encodings:
            sim = float(np.dot(encoding, known_enc))
            if sim > best_sim:
                best_sim = sim
                best_name = name if sim >= threshold else None
    return best_name
