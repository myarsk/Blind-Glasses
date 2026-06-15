import queue
import subprocess
import threading


class VoiceOutput:
    def __init__(self, cfg=None):
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        while True:
            text = self._q.get()
            try:
                subprocess.run(
                    ["espeak-ng", "-s", "150", "-v", "en", text],
                    capture_output=True,
                )
            except FileNotFoundError:
                print("[Voice] espeak-ng not found — run: sudo apt install espeak-ng")
            except Exception as e:
                print(f"[Voice] TTS error: {e}")
            self._q.task_done()

    def speak(self, text: str) -> None:
        """Enqueue text for speech. Non-blocking."""
        self._q.put(text)
