"""
Face detection and recognition using InsightFace (ONNX-based).

Model: buffalo_sc — compact, fast enough for Pi 4.
If the model isn't downloaded yet, the recognizer stays in a disabled state
and returns empty results — run download_models.py first to fetch it.

Similarity metric: cosine similarity on L2-normalised 512-dim embeddings.
Threshold default 0.4: same person ~0.5–0.9, strangers ~0.1–0.3.
"""

import os
from typing import List, Optional, Tuple

import numpy as np

from config import Config
from database import FaceDatabase

RecognitionResult = Tuple[Optional[str], tuple, np.ndarray, np.ndarray]

_MODELS_ROOT = os.path.expanduser("~/.insightface/models")
_MODELS_DIR  = os.path.join(_MODELS_ROOT, "buffalo_sc")
_ONNX_FILES  = ("det_500m.onnx", "w600k_mbf.onnx")


def _auto_relocate_flat_models() -> None:
    """
    If the user manually unzipped buffalo_sc.zip into the models root dir
    (flat layout), move the ONNX files into the expected buffalo_sc/ subdir.
    """
    flat_files = [f for f in _ONNX_FILES if os.path.isfile(os.path.join(_MODELS_ROOT, f))]
    if not flat_files:
        return
    print("[FaceRecognizer] Moving model files into buffalo_sc/ subdirectory...")
    os.makedirs(_MODELS_DIR, exist_ok=True)
    import shutil
    for fname in flat_files:
        shutil.move(os.path.join(_MODELS_ROOT, fname), os.path.join(_MODELS_DIR, fname))
    print("[FaceRecognizer] Models relocated.")


class FaceRecognizer:
    def __init__(self, db: FaceDatabase, cfg: Config):
        self._db = db
        self._threshold = cfg.face_similarity_threshold
        self._app = None
        self._try_init()

    def _try_init(self) -> None:
        _auto_relocate_flat_models()
        if not os.path.isdir(_MODELS_DIR) or not os.listdir(_MODELS_DIR):
            print(
                "[FaceRecognizer] Model not found — face recognition disabled.\n"
                "  Run: python download_models.py"
            )
            return
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name="buffalo_sc", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=0, det_size=(320, 320))
            self._app = app
            print("[FaceRecognizer] Ready.")
        except Exception as e:
            print(f"[FaceRecognizer] Init failed ({e}) — face recognition disabled.")

    def detect_and_recognize(self, frame: np.ndarray) -> List[RecognitionResult]:
        """
        frame: BGR numpy array. Returns [] if recognizer is unavailable.
        Returns list of (name_or_None, bbox, crop, encoding).
        """
        if self._app is None:
            return []

        try:
            faces = self._app.get(frame)
        except Exception as e:
            print(f"[FaceRecognizer] Detection error: {e}")
            return []

        if not faces:
            return []

        known = self._db.get_all()
        results: List[RecognitionResult] = []

        for face in faces:
            x1, y1, x2, y2 = face.bbox.astype(int)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(frame.shape[1], x2), min(frame.shape[0], y2)
            crop = frame[y1:y2, x1:x2]
            encoding = face.embedding

            name = _best_match(encoding, known, self._threshold)
            results.append((name, (y1, x2, y2, x1), crop, encoding))

        return results

    def get_encoding(self, img_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Return the first face encoding found, or None."""
        if self._app is None:
            return None
        try:
            faces = self._app.get(img_bgr)
            return faces[0].embedding if faces else None
        except Exception:
            return None


def _best_match(encoding: np.ndarray, known: dict, threshold: float) -> Optional[str]:
    best_name: Optional[str] = None
    best_sim: float = -1.0
    for name, encodings in known.items():
        for known_enc in encodings:
            sim = float(np.dot(encoding, known_enc))
            if sim > best_sim:
                best_sim = sim
                best_name = name if sim >= threshold else None
    return best_name
