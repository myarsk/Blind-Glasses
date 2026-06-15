"""
Telegram bot for image capture alerts, GPS sharing, and face registration.

Partner flow:
  1. Blind user presses camera button → photo sent to partner
  2. If unknown face: partner replies with name → saved to database
  Commands:
    /list          — show all known names
    /remove <name> — delete a person
    /add           — manually register a new face (bot asks for photo then name)
    /location      — get current GPS location of the blind user
"""

import asyncio
import os
import threading
from typing import Optional, Tuple

import numpy as np
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import Config
from database import FaceDatabase
from voice_output import VoiceOutput


class TelegramBot:
    def __init__(self, cfg: Config, db: FaceDatabase, voice: VoiceOutput, recognizer=None, gps_fn=None):
        self._cfg = cfg
        self._db = db
        self._voice = voice
        self._recognizer = recognizer
        self._gps_fn = gps_fn  # callable -> Optional[(lat, lon)]

        # State for pending face registration
        self._pending_encoding: Optional[np.ndarray] = None
        self._pending_add_chat: Optional[int] = None  # for /add flow
        self._pending_add_encoding: Optional[np.ndarray] = None

        self._loop = asyncio.new_event_loop()
        self._app: Optional[Application] = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())

    async def _start(self):
        self._app = (
            Application.builder()
            .token(self._cfg.telegram_token)
            .build()
        )
        self._app.add_handler(CommandHandler("list", self._cmd_list))
        self._app.add_handler(CommandHandler("remove", self._cmd_remove))
        self._app.add_handler(CommandHandler("add", self._cmd_add))
        self._app.add_handler(CommandHandler("location", self._cmd_location))
        self._app.add_handler(
            MessageHandler(filters.PHOTO & ~filters.COMMAND, self._handle_photo)
        )
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        # Keep running
        await asyncio.Event().wait()

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send_capture(
        self,
        photo_path: str,
        encoding: Optional[np.ndarray] = None,
        known_name: Optional[str] = None,
    ) -> None:
        """Send a captured photo to the partner. Thread-safe."""
        if encoding is not None:
            self._pending_encoding = encoding
        asyncio.run_coroutine_threadsafe(
            self._do_send_capture(photo_path, known_name), self._loop
        )

    async def _do_send_capture(self, photo_path: str, known_name: Optional[str]):
        bot: Bot = self._app.bot
        chat_id = self._cfg.partner_chat_id
        if known_name:
            caption = f"{known_name} is in front of the blind user."
        elif self._pending_encoding is not None:
            caption = (
                "Unknown person detected.\n"
                "Reply with their name to register them, or /skip to ignore."
            )
        else:
            caption = "Scene captured."
        with open(photo_path, "rb") as f:
            await bot.send_photo(chat_id=chat_id, photo=f, caption=caption)

    def send_text(self, text: str) -> None:
        """Send a plain text message to the partner. Thread-safe."""
        asyncio.run_coroutine_threadsafe(self._do_send_text(text), self._loop)

    async def _do_send_text(self, text: str):
        if self._app is None:
            return   # bot not started yet
        await self._app.bot.send_message(chat_id=self._cfg.partner_chat_id, text=text)

    def send_gps_location(self, location: Tuple[float, float]) -> None:
        """Send GPS coordinates to the partner. Thread-safe."""
        asyncio.run_coroutine_threadsafe(
            self._do_send_gps(location), self._loop
        )

    async def _do_send_gps(self, location: Tuple[float, float]):
        lat, lon = location
        await self._app.bot.send_location(
            chat_id=self._cfg.partner_chat_id, latitude=lat, longitude=lon
        )

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _cmd_list(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        names = self._db.list_names()
        if names:
            await update.message.reply_text("Known people:\n" + "\n".join(f"• {n}" for n in names))
        else:
            await update.message.reply_text("No known faces registered yet.")

    async def _cmd_remove(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not ctx.args:
            await update.message.reply_text("Usage: /remove <name>")
            return
        name = " ".join(ctx.args)
        if self._db.remove(name):
            await update.message.reply_text(f"Removed: {name}")
        else:
            await update.message.reply_text(f"Not found: {name}")

    async def _cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        self._pending_add_chat = update.effective_chat.id
        self._pending_add_encoding = None
        await update.message.reply_text(
            "Send me a clear photo of the person's face."
        )

    async def _cmd_location(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if self._gps_fn:
            loc = self._gps_fn()
            if loc:
                lat, lon = loc
                await update.message.reply_location(latitude=lat, longitude=lon)
                return
        await update.message.reply_text("No GPS fix available yet.")

    # ── Message handlers ──────────────────────────────────────────────────────

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text.lower() == "/skip":
            self._pending_encoding = None
            await update.message.reply_text("Alert skipped.")
            return

        # /add flow: waiting for name after receiving photo
        if (
            self._pending_add_chat == update.effective_chat.id
            and self._pending_add_encoding is not None
        ):
            self._db.add_face(text, self._pending_add_encoding)
            self._pending_add_encoding = None
            self._pending_add_chat = None
            await update.message.reply_text(f"Saved as: {text}")
            return

        # Reply-with-name flow for unknown alert
        if self._pending_encoding is not None:
            self._db.add_face(text, self._pending_encoding)
            self._pending_encoding = None
            await update.message.reply_text(
                f"Got it! {text} has been registered. They'll be recognized next time."
            )
            self._voice.speak(f"{text} has been registered")
        else:
            await update.message.reply_text(
                "No pending face to name. Use /add to manually register someone."
            )

    async def _handle_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle photo sent during /add flow to extract face encoding."""
        if self._pending_add_chat != update.effective_chat.id:
            await update.message.reply_text("Send /add first to register a new person.")
            return

        # Download photo
        photo = update.message.photo[-1]  # largest size
        file = await photo.get_file()
        tmp_path = os.path.join(
            os.path.dirname(__file__), "captured", f"add_{photo.file_id}.jpg"
        )
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        await file.download_to_drive(tmp_path)

        import cv2
        img = cv2.imread(tmp_path)
        if img is None:
            await update.message.reply_text("Could not read the image. Try again.")
            return

        if self._recognizer is None:
            await update.message.reply_text("Face recognizer unavailable.")
            return

        encoding = self._recognizer.get_encoding(img)
        if encoding is None:
            await update.message.reply_text("No face found in that photo. Try a clearer one.")
            return

        self._pending_add_encoding = encoding
        await update.message.reply_text("Face captured! Now send me their name.")
