import queue
import threading
import pyttsx3


class VoiceOutput:
    def __init__(self, cfg=None):
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        engine = pyttsx3.init()
        engine.setProperty("rate", 150)
        engine.setProperty("volume", 1.0)
        while True:
            text = self._q.get()
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                print(f"[Voice] TTS error: {e}")
            self._q.task_done()

    def speak(self, text: str) -> None:
        """Enqueue text for announcement. Non-blocking."""
        self._q.put(text)
