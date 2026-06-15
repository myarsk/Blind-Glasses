"""
Voice-driven vision agent.

Activated by long press on the camera switch. Maintains a chat context for
the whole session so follow-up questions work naturally.

Flow:
  long press → "Agent mode on. Ask me anything."
  user speaks → speech-to-text → frame captured → Gemini 2.5 Flash Lite → TTS
  say "exit / stop / bye" to leave agent mode

Dependencies:
  pip install openai speechrecognition pyaudio
"""

import base64
import threading
from typing import Callable

import cv2
import numpy as np

_SYSTEM_PROMPT = (
    "You are an AI assistant built into the smart glasses of a visually impaired person. "
    "You can see through their camera in real time. "
    "Keep every response short and spoken-friendly — no bullet points, no markdown, no lists. "
    "Prioritise safety: mention traffic, obstacles, and hazards first. "
    "When asked about crossing a road, describe traffic direction, speed, and gaps clearly. "
    "When asked who is there, describe people and their approximate distance."
)

_EXIT_WORDS = {"exit", "stop", "bye", "quit", "goodbye", "end", "cancel"}
_MODEL = "google/gemini-2.5-flash-lite-preview-06-17"


class VisionAgent:
    def __init__(
        self,
        api_key: str,
        api_base_url: str,
        capture_fn: Callable[[], np.ndarray],
        speak_fn: Callable[[str], None],
    ):
        self._speak = speak_fn
        self._capture = capture_fn
        self._active = False
        self._messages: list = []

        from openai import OpenAI
        self._client = OpenAI(base_url=api_base_url.rstrip("/") + "/", api_key=api_key)

    @property
    def is_active(self) -> bool:
        return self._active

    def toggle(self) -> None:
        if self._active:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._messages = []   # fresh context for each session
        threading.Thread(target=self._run, daemon=True).start()
        print("[Agent] Started")

    def stop(self) -> None:
        if self._active:
            self._active = False
            self._speak("Agent mode off.")
            print("[Agent] Stopped")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        import speech_recognition as sr

        self._speak("Agent mode on. I can see through your camera. Ask me anything.")

        recognizer = sr.Recognizer()

        # Mic setup can fail if no input device is available (no mic plugged in,
        # or running under sudo without access to the user's audio session).
        # Fail cleanly instead of crashing the thread with _active stuck True.
        try:
            mic = sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
        except Exception as e:
            print(f"[Agent] Microphone unavailable: {e}")
            self._speak("No microphone available. Agent mode off.")
            self._active = False
            return

        while self._active:
            try:
                with mic as source:
                    audio = recognizer.listen(source, timeout=10, phrase_time_limit=12)

                text = recognizer.recognize_google(audio).strip()
                print(f"[Agent] Heard: {text}")

                if text.lower() in _EXIT_WORDS:
                    self._active = False
                    self._speak("Agent mode off.")
                    break

                response = self._query(text)
                print(f"[Agent] Response: {response}")
                self._speak(response)

            except sr.WaitTimeoutError:
                # No speech detected — keep waiting silently
                continue
            except sr.UnknownValueError:
                self._speak("I didn't catch that. Please repeat.")
            except Exception as e:
                print(f"[Agent] Error: {e}")
                self._speak("Something went wrong. Please try again.")

    # ── AI query ───────────────────────────────────────────────────────────────

    def _query(self, user_text: str) -> str:
        frame = self._capture()
        b64 = _frame_to_b64(frame)

        user_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        }
        self._messages.append(user_msg)

        completion = self._client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                *self._messages,
            ],
        )
        reply = completion.choices[0].message.content
        # Store plain text in history so future turns don't resend images
        self._messages.append({"role": "assistant", "content": reply})
        return reply


def _frame_to_b64(frame: np.ndarray) -> str:
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode()
