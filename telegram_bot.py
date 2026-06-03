"""
Telegram bot for unknown-face alerts and face registration.

Partner flow:
  1. Unknown face detected → bot sends photo + location to partner
  2. Partner replies with name → face saved to database
  Commands:
    /list   — show all known names
    /remove <name> — delete a person
    /add    — manually register a new face (bot asks for photo then name)
    /location — get current GPS location of the blind person
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
    def __init__(self, cfg: Config, db: FaceDatabase, voice: VoiceOutput, gps_fn=None):
        self._cfg = cfg
        self._db = db
        self._voice = voice
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

    def send_unknown_alert(self, photo_path: str, encoding: np.ndarray,
                           location: Optional[Tuple[float, float]] = None) -> None:
        """Called from main thread. Schedules coroutine on bot's event loop."""
        self._pending_encoding = encoding
        asyncio.run_coroutine_threadsafe(
            self._do_send_alert(photo_path, location), self._loop
        )

    async def _do_send_alert(self, photo_path: str,
                             location: Optional[Tuple[float, float]]):
        bot: Bot = self._app.bot
        chat_id = self._cfg.partner_chat_id
        with open(photo_path, "rb") as f:
            await bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=(
                    "Unknown person detected nearby.\n"
                    "Reply with their name to register them, or /skip to ignore."
                ),
            )
        if location:
            lat, lon = location
            await bot.send_location(chat_id=chat_id, latitude=lat, longitude=lon)

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

        # Extract encoding
        import face_recognition
        import cv2
        img = cv2.imread(tmp_path)
        if img is None:
            await update.message.reply_text("Could not read the image. Try again.")
            return
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        encs = face_recognition.face_encodings(rgb)
        if not encs:
            await update.message.reply_text("No face found in that photo. Try a clearer one.")
            return

        self._pending_add_encoding = encs[0]
        await update.message.reply_text("Face captured! Now send me their name.")
