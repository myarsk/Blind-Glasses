"""
Voice-driven vision agent — bilingual (English / Arabic).

Activated by long press on the camera switch. Maintains a chat context for
the whole session so follow-up questions work naturally.

Flow:
  long press → "Agent mode on. Ask me anything."
  user speaks → speech-to-text → frame captured → Gemini 2.5 Flash Lite → TTS
  say "exit / stop / bye" (EN) or "اخرج / وقف / انتهى" (AR) to leave agent mode

Dependencies:
  pip install openai speechrecognition pyaudio
"""

import base64
import threading
from typing import Callable

import cv2
import numpy as np

from config import Config

# ── System prompts ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_EN = (
    "You are an AI assistant built into the smart glasses of a visually impaired person. "
    "You can see through their camera in real time. "
    "Keep every response short and spoken-friendly — no bullet points, no markdown, no lists. "
    "Prioritise safety: mention traffic, obstacles, and hazards first. "
    "When asked about crossing a road, describe traffic direction, speed, and gaps clearly. "
    "When asked who is there, describe people and their approximate distance. "
    "Always respond in English."
)

_SYSTEM_PROMPT_AR = (
    "أنت مساعد ذكاء اصطناعي مدمج في النظارات الذكية لشخص ضعيف البصر. "
    "يمكنك رؤية المحيط عبر كاميرا النظارات في الوقت الفعلي. "
    "اجعل كل إجابة قصيرة ومناسبة للاستماع — بدون تعداد نقاط، بدون تنسيق، بدون قوائم. "
    "أعطِ الأولوية للسلامة: اذكر حركة المرور والعقبات والمخاطر أولاً. "
    "عند السؤال عن عبور الطريق، صِف اتجاه حركة المرور وسرعتها والفجوات بوضوح. "
    "عند السؤال عمّن هو موجود، صِف الأشخاص ومسافتهم التقريبية. "
    "تحدث دائماً باللغة العربية."
)

# ── Exit words ─────────────────────────────────────────────────────────────────

_EXIT_WORDS_EN = {"exit", "stop", "bye", "quit", "goodbye", "end", "cancel"}
_EXIT_WORDS_AR = {"اخرج", "وقف", "انتهى", "إلغاء", "باي", "انتهي", "إيقاف"}

# ── Agent start/stop phrases ───────────────────────────────────────────────────

_PHRASES = {
    "en": {
        "agent_on": "Agent mode on. I can see through your camera. Ask me anything.",
        "agent_off": "Agent mode off.",
        "no_mic": "No microphone available. Agent mode off.",
        "no_catch": "I didn't catch that. Please repeat.",
        "error": "Something went wrong. Please try again.",
    },
    "ar": {
        "agent_on": "وضع المساعد مفعّل. يمكنني رؤية ما حولك. اسألني أي شيء.",
        "agent_off": "تم إيقاف وضع المساعد.",
        "no_mic": "لا يوجد ميكروفون متاح. تم إيقاف وضع المساعد.",
        "no_catch": "لم أفهم ذلك. يرجى التكرار.",
        "error": "حدث خطأ ما. يرجى المحاولة مرة أخرى.",
    },
}

_MODEL = "google/gemini-2.5-flash-lite-preview-06-17"

# STT language codes for Google Speech Recognition
_STT_LANG = {
    "en": "en-US",
    "ar": "ar-SA",
}


class VisionAgent:
    def __init__(
        self,
        cfg: Config,
        capture_fn: Callable[[], np.ndarray],
        speak_fn: Callable[[str], None],
        relay_fn: Callable[[str], None] = None,
    ):
        self._cfg = cfg
        self._speak = speak_fn
        self._capture = capture_fn
        self._relay = relay_fn   # optional: mirror the conversation to Telegram
        self._active = False
        self._messages: list = []

        from openai import OpenAI
        self._client = OpenAI(
            base_url=cfg.api_base_url.rstrip("/") + "/",
            api_key=cfg.lightning_api_key,
        )

    # ── Language helpers ──────────────────────────────────────────────────────

    def _lang(self) -> str:
        return getattr(self._cfg, "language", "en")

    def _phrase(self, key: str) -> str:
        lang = self._lang()
        return _PHRASES.get(lang, _PHRASES["en"])[key]

    def _system_prompt(self) -> str:
        return _SYSTEM_PROMPT_AR if self._lang() == "ar" else _SYSTEM_PROMPT_EN

    def _exit_words(self) -> set:
        return _EXIT_WORDS_AR if self._lang() == "ar" else _EXIT_WORDS_EN

    def _stt_lang(self) -> str:
        return _STT_LANG.get(self._lang(), "en-US")

    # ── Public API ────────────────────────────────────────────────────────────

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
            self._speak(self._phrase("agent_off"))
            self._relay_text("🛑 " + self._phrase("agent_off"))
            print("[Agent] Stopped")

    def _relay_text(self, text: str) -> None:
        """Mirror a line of the conversation to Telegram, if a relay is set."""
        if self._relay is None:
            return
        try:
            self._relay(text)
        except Exception as e:
            print(f"[Agent] Telegram relay failed: {e}")

    # ── Main loop ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        import speech_recognition as sr

        self._speak(self._phrase("agent_on"))
        self._relay_text("🎤 " + self._phrase("agent_on"))

        recognizer = sr.Recognizer()

        try:
            mic = sr.Microphone()
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
        except Exception as e:
            print(f"[Agent] Microphone unavailable: {e}")
            self._speak(self._phrase("no_mic"))
            self._relay_text(f"⚠️ Agent could not start: microphone unavailable ({e})")
            self._active = False
            return

        stt_lang = self._stt_lang()

        while self._active:
            try:
                with mic as source:
                    audio = recognizer.listen(source, timeout=10, phrase_time_limit=12)

                text = recognizer.recognize_google(audio, language=stt_lang).strip()
                print(f"[Agent] Heard: {text}")
                self._relay_text(f"🗣️ {text}")

                if text.lower() in self._exit_words() or text in self._exit_words():
                    self._active = False
                    self._speak(self._phrase("agent_off"))
                    self._relay_text("🛑 " + self._phrase("agent_off"))
                    break

                response = self._query(text)
                print(f"[Agent] Response: {response}")
                self._relay_text(f"🤖 {response}")
                self._speak(response)

            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                self._speak(self._phrase("no_catch"))
            except Exception as e:
                print(f"[Agent] Error: {e}")
                self._relay_text(f"⚠️ Agent error: {e}")
                self._speak(self._phrase("error"))

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
                {"role": "system", "content": self._system_prompt()},
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
