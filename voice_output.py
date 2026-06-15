import queue
import subprocess
import threading

# Kill a stuck espeak-ng so a broken audio device can't jam the whole queue.
_TTS_TIMEOUT_S = 45

# espeak-ng voice identifiers
_VOICE_MAP = {
    "en": "en",
    "ar": "ar",
}


class VoiceOutput:
    """
    Thread-safe TTS output via espeak-ng.

    Language is read from cfg.language on every speak() call so a language
    change mid-session takes effect on the very next utterance.
    """

    def __init__(self, cfg=None):
        self._cfg = cfg
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _current_voice(self) -> str:
        if self._cfg is not None:
            lang = getattr(self._cfg, "language", "en")
            return _VOICE_MAP.get(lang, "en")
        return "en"

    def _worker(self):
        while True:
            item = self._q.get()
            # item is either a plain str or a (text, voice_override) tuple
            if isinstance(item, tuple):
                text, voice = item
            else:
                text, voice = item, self._current_voice()

            try:
                subprocess.run(
                    ["espeak-ng", "-s", "140", "-v", voice, text],
                    capture_output=True,
                    timeout=_TTS_TIMEOUT_S,
                )
            except FileNotFoundError:
                print("[Voice] espeak-ng not found — run: sudo apt install espeak-ng")
            except subprocess.TimeoutExpired:
                print("[Voice] espeak-ng timed out — check the audio output device.")
            except Exception as e:
                print(f"[Voice] TTS error: {e}")
            self._q.task_done()

    def speak(self, text: str) -> None:
        """Enqueue text for speech. Language is resolved at call time. Non-blocking."""
        voice = self._current_voice()
        self._q.put((text, voice))
