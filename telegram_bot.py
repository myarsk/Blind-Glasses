"""
Telegram bot — single source of truth for configuration.

First-run bootstrap
───────────────────
On a clean run (owner_chat_id == 0) the bot accepts /start from anyone.
The first person to send /start becomes the permanent owner.

Access control
──────────────
Every handler checks the sender against:
  • owner_chat_id
  • allowed_chat_ids

Strangers are silently ignored.
Only the owner can manage users and settings.

Commands
────────
  /start          — first run: claim ownership; subsequent: welcome + menu
  /help           — list commands (language-aware)
  /status         — current config snapshot
  /settings       — inline keyboard setup wizard
  /language en|ar — switch language
  /setdistance N  — detection threshold in cm
  /setcooldown N  — cooldown in seconds
  /setapi <url>   — vision AI API base URL
  /adduser        — add an allowed user (owner only)
  /removeuser     — remove an allowed user by ID or @username (owner only)
  /users          — list allowed users (owner only)
  /list           — list known faces
  /remove <name>  — delete a face
  /add            — register a new face (photo → name flow)
  /location       — get current GPS location
"""

import asyncio
import os
import threading
from typing import Optional, Tuple, List

import numpy as np
from telegram import (
    Update,
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config as cfg_module
from config import Config, TELEGRAM_TOKEN
from database import FaceDatabase
from voice_output import VoiceOutput

# ── Conversation states ────────────────────────────────────────────────────────

(
    ST_AWAIT_SETTING_VALUE,   # generic: waiting for a typed value after inline tap
    ST_ADD_PHOTO,             # /add: waiting for a photo
    ST_ADD_NAME,              # /add: waiting for a name after photo received
    ST_ADDUSER_INPUT,         # /adduser: waiting for chat_id or @username
    ST_REMOVEUSER_INPUT,      # /removeuser: waiting for chat_id or @username
) = range(5)

# Context data keys
_CTX_SETTING_KEY = "setting_key"
_CTX_ADD_ENCODING = "add_encoding"
_CTX_PENDING_ENCODING = "pending_encoding"   # unknown face alert


# ── Translation table ──────────────────────────────────────────────────────────

_T = {
    # ── Bootstrap ──
    "owner_claimed": {
        "en": (
            "✅ You are now the owner of these glasses.\n\n"
            "Use /help to see all available commands.\n"
            "Use /language ar to switch to Arabic."
        ),
        "ar": (
            "✅ أنت الآن مالك هذه النظارات.\n\n"
            "استخدم /help لرؤية جميع الأوامر المتاحة.\n"
            "استخدم /language en للتبديل إلى الإنجليزية."
        ),
    },
    "welcome_back": {
        "en": "👋 Welcome back! Use /help for commands.",
        "ar": "👋 مرحباً بعودتك! استخدم /help للأوامر.",
    },
    "welcome_user": {
        "en": "👋 Hello! Use /help to see what you can do.",
        "ar": "👋 مرحباً! استخدم /help لرؤية ما يمكنك فعله.",
    },
    "not_authorised": {
        "en": "⛔ You are not authorised to use this bot.",
        "ar": "⛔ غير مصرح لك باستخدام هذا البوت.",
    },
    "owner_only": {
        "en": "⛔ This command is for the owner only.",
        "ar": "⛔ هذا الأمر للمالك فقط.",
    },

    # ── Help ──
    "help": {
        "en": (
            "📋 *Commands*\n\n"
            "*Settings (owner only)*\n"
            "/settings — open setup menu\n"
            "/language en|ar — switch language\n"
            "/setdistance \\<cm\\> — detection distance\n"
            "/setcooldown \\<s\\> — cooldown seconds\n"
            "/setapi \\<url\\> — vision API URL\n\n"
            "*Users (owner only)*\n"
            "/adduser — add an allowed user\n"
            "/removeuser — remove a user\n"
            "/users — list users\n\n"
            "*Faces*\n"
            "/list — known faces\n"
            "/remove \\<name\\> — delete a face\n"
            "/add — register a new face\n\n"
            "*Other*\n"
            "/location — get GPS location\n"
            "/status — system status\n"
            "/help — this message"
        ),
        "ar": (
            "📋 *الأوامر*\n\n"
            "*الإعدادات (للمالك فقط)*\n"
            "/settings — فتح قائمة الإعداد\n"
            "/language en|ar — تغيير اللغة\n"
            "/setdistance \\<سم\\> — مسافة الكشف\n"
            "/setcooldown \\<ث\\> — فترة التهدئة بالثواني\n"
            "/setapi \\<رابط\\> — رابط واجهة الذكاء الاصطناعي\n\n"
            "*المستخدمون (للمالك فقط)*\n"
            "/adduser — إضافة مستخدم\n"
            "/removeuser — حذف مستخدم\n"
            "/users — قائمة المستخدمين\n\n"
            "*الوجوه*\n"
            "/list — الوجوه المعروفة\n"
            "/remove \\<اسم\\> — حذف وجه\n"
            "/add — تسجيل وجه جديد\n\n"
            "*أخرى*\n"
            "/location — الموقع الجغرافي\n"
            "/status — حالة النظام\n"
            "/help — هذه الرسالة"
        ),
    },

    # ── Status ──
    "status": {
        "en": (
            "📊 *System Status*\n\n"
            "🌐 Language: {language}\n"
            "📏 Detection distance: {distance} cm\n"
            "⏱ Cooldown: {cooldown} s\n"
            "📷 Camera index: {camera}\n"
            "🛰 GPS port: {gps_port} @ {gps_baud} baud\n"
            "🔌 API: {api}\n"
            "👤 Owner ID: {owner}\n"
            "👥 Allowed users: {users}"
        ),
        "ar": (
            "📊 *حالة النظام*\n\n"
            "🌐 اللغة: {language}\n"
            "📏 مسافة الكشف: {distance} سم\n"
            "⏱ فترة التهدئة: {cooldown} ثانية\n"
            "📷 فهرس الكاميرا: {camera}\n"
            "🛰 منفذ GPS: {gps_port} @ {gps_baud} باود\n"
            "🔌 واجهة الذكاء الاصطناعي: {api}\n"
            "👤 معرّف المالك: {owner}\n"
            "👥 المستخدمون المسموح لهم: {users}"
        ),
    },

    # ── Language switch ──
    "lang_set": {
        "en": "✅ Language set to English.",
        "ar": "✅ تم تعيين اللغة إلى العربية.",
    },
    "lang_invalid": {
        "en": "⚠️ Usage: /language en  or  /language ar",
        "ar": "⚠️ الاستخدام: /language en  أو  /language ar",
    },

    # ── Settings inline ──
    "settings_menu": {
        "en": "⚙️ *Settings* — tap to change:",
        "ar": "⚙️ *الإعدادات* — اضغط للتغيير:",
    },
    "settings_ask_value": {
        "en": "✏️ Send the new value for *{key}* (current: `{current}`):",
        "ar": "✏️ أرسل القيمة الجديدة لـ *{key}* (الحالية: `{current}`):",
    },
    "settings_saved": {
        "en": "✅ *{key}* updated to `{value}`.",
        "ar": "✅ تم تحديث *{key}* إلى `{value}`.",
    },
    "settings_invalid": {
        "en": "⚠️ Invalid value. Please try again.",
        "ar": "⚠️ قيمة غير صالحة. يرجى المحاولة مرة أخرى.",
    },
    "settings_cancel": {
        "en": "❌ Settings update cancelled.",
        "ar": "❌ تم إلغاء تحديث الإعدادات.",
    },

    # ── setdistance / setcooldown / setapi ──
    "set_distance_ok": {
        "en": "✅ Detection distance set to {val} cm.",
        "ar": "✅ تم ضبط مسافة الكشف على {val} سم.",
    },
    "set_cooldown_ok": {
        "en": "✅ Cooldown set to {val} seconds.",
        "ar": "✅ تم ضبط فترة التهدئة على {val} ثانية.",
    },
    "set_api_ok": {
        "en": "✅ API URL updated.",
        "ar": "✅ تم تحديث رابط واجهة الذكاء الاصطناعي.",
    },
    "invalid_number": {
        "en": "⚠️ Please provide a valid number.",
        "ar": "⚠️ يرجى إدخال رقم صحيح.",
    },
    "missing_arg": {
        "en": "⚠️ Usage: {usage}",
        "ar": "⚠️ الاستخدام: {usage}",
    },

    # ── adduser / removeuser / users ──
    "adduser_ask": {
        "en": "👤 Send the chat ID (number) or @username of the user to add:",
        "ar": "👤 أرسل معرّف المحادثة (رقم) أو @اسم_المستخدم للمستخدم المراد إضافته:",
    },
    "adduser_ok": {
        "en": "✅ User added.",
        "ar": "✅ تمت إضافة المستخدم.",
    },
    "adduser_exists": {
        "en": "ℹ️ User is already allowed.",
        "ar": "ℹ️ المستخدم مسموح له بالفعل.",
    },
    "adduser_invalid": {
        "en": "⚠️ Please send a numeric chat ID or @username.",
        "ar": "⚠️ يرجى إرسال معرّف محادثة رقمي أو @اسم_المستخدم.",
    },
    "adduser_cancel": {
        "en": "❌ Add user cancelled.",
        "ar": "❌ تم إلغاء إضافة المستخدم.",
    },
    "removeuser_ask": {
        "en": "🗑 Send the chat ID or @username of the user to remove:",
        "ar": "🗑 أرسل معرّف المحادثة أو @اسم_المستخدم للمستخدم المراد حذفه:",
    },
    "removeuser_ok": {
        "en": "✅ User removed.",
        "ar": "✅ تمت إزالة المستخدم.",
    },
    "removeuser_notfound": {
        "en": "⚠️ User not found in the allowed list.",
        "ar": "⚠️ المستخدم غير موجود في القائمة المسموح بها.",
    },
    "removeuser_cancel": {
        "en": "❌ Remove user cancelled.",
        "ar": "❌ تم إلغاء حذف المستخدم.",
    },
    "users_list": {
        "en": "👥 *Allowed users:*\n{list}",
        "ar": "👥 *المستخدمون المسموح لهم:*\n{list}",
    },
    "users_empty": {
        "en": "ℹ️ No extra users added yet. Only the owner has access.",
        "ar": "ℹ️ لم يتم إضافة مستخدمين إضافيين بعد. المالك فقط لديه صلاحية الوصول.",
    },

    # ── Faces ──
    "faces_list": {
        "en": "👤 Known people:\n{list}",
        "ar": "👤 الأشخاص المعروفون:\n{list}",
    },
    "faces_empty": {
        "en": "ℹ️ No known faces registered yet.",
        "ar": "ℹ️ لم يتم تسجيل أي وجوه معروفة بعد.",
    },
    "face_removed": {
        "en": "✅ Removed: {name}",
        "ar": "✅ تمت الإزالة: {name}",
    },
    "face_not_found": {
        "en": "⚠️ Not found: {name}",
        "ar": "⚠️ غير موجود: {name}",
    },
    "add_send_photo": {
        "en": "📷 Send me a clear photo of the person's face.",
        "ar": "📷 أرسل لي صورة واضحة لوجه الشخص.",
    },
    "add_send_name": {
        "en": "✅ Face captured! Now send me their name.",
        "ar": "✅ تم التقاط الوجه! أرسل لي اسمهم الآن.",
    },
    "add_no_face": {
        "en": "⚠️ No face found in that photo. Try a clearer one.",
        "ar": "⚠️ لم يتم العثور على وجه في تلك الصورة. جرّب صورة أوضح.",
    },
    "add_no_recognizer": {
        "en": "⚠️ Face recognizer unavailable.",
        "ar": "⚠️ معرِّف الوجوه غير متاح.",
    },
    "add_saved": {
        "en": "✅ Saved as: {name}",
        "ar": "✅ تم الحفظ باسم: {name}",
    },
    "add_cancel": {
        "en": "❌ Face registration cancelled.",
        "ar": "❌ تم إلغاء تسجيل الوجه.",
    },
    "add_no_read": {
        "en": "⚠️ Could not read the image. Try again.",
        "ar": "⚠️ تعذّرت قراءة الصورة. حاول مرة أخرى.",
    },

    # ── Alerts (outbound) ──
    "alert_known": {
        "en": "{name} is in front of the blind user.",
        "ar": "{name} أمام المستخدم الكفيف.",
    },
    "alert_unknown": {
        "en": "❓ Unknown person detected.\nReply with their name to register them, or /skip to ignore.",
        "ar": "❓ تم اكتشاف شخص غير معروف.\nردّ باسمه لتسجيله، أو /skip لتجاهله.",
    },
    "alert_scene": {
        "en": "📷 Scene captured.",
        "ar": "📷 تم التقاط المشهد.",
    },
    "alert_location_sent": {
        "en": "📍 Location sent.",
        "ar": "📍 تم إرسال الموقع.",
    },
    "alert_no_gps": {
        "en": "📍 GPS button pressed — no satellite fix yet. Move outdoors and wait a moment.",
        "ar": "📍 تم الضغط على زر GPS — لا يوجد إشارة أقمار صناعية بعد. اخرج للخارج وانتظر لحظة.",
    },

    # ── Unknown face reply flow ──
    "face_registered": {
        "en": "✅ Got it! {name} has been registered. They'll be recognised next time.",
        "ar": "✅ تمّ! تم تسجيل {name}. سيتم التعرف عليهم في المرة القادمة.",
    },
    "no_pending_face": {
        "en": "ℹ️ No pending face to name. Use /add to manually register someone.",
        "ar": "ℹ️ لا يوجد وجه معلّق لتسميته. استخدم /add لتسجيل شخص يدوياً.",
    },
    "skip_ok": {
        "en": "✅ Alert skipped.",
        "ar": "✅ تم تجاهل التنبيه.",
    },

    # ── GPS ──
    "no_gps_fix": {
        "en": "📍 No GPS fix available yet.",
        "ar": "📍 لا يوجد إشارة GPS متاحة بعد.",
    },
}


def _t(key: str, lang: str, **kwargs) -> str:
    """Translate a key to the given language, with optional format args."""
    entry = _T.get(key, {})
    text = entry.get(lang) or entry.get("en", f"[{key}]")
    if kwargs:
        try:
            text = text.format(**kwargs)
        except KeyError:
            pass
    return text


# ── Inline keyboard for /settings ─────────────────────────────────────────────

_SETTING_LABELS = {
    "en": {
        "detection_distance_cm": "📏 Detection Distance (cm)",
        "cooldown_seconds":      "⏱ Cooldown (seconds)",
        "camera_index":          "📷 Camera Index",
        "gps_serial_port":       "🛰 GPS Serial Port",
        "gps_baud":              "📡 GPS Baud Rate",
        "api_base_url":          "🔌 API Base URL",
    },
    "ar": {
        "detection_distance_cm": "📏 مسافة الكشف (سم)",
        "cooldown_seconds":      "⏱ فترة التهدئة (ثواني)",
        "camera_index":          "📷 فهرس الكاميرا",
        "gps_serial_port":       "🛰 منفذ GPS التسلسلي",
        "gps_baud":              "📡 معدل باود GPS",
        "api_base_url":          "🔌 رابط واجهة الذكاء الاصطناعي",
    },
}

_SETTING_TYPES = {
    "detection_distance_cm": float,
    "cooldown_seconds":       int,
    "camera_index":           int,
    "gps_serial_port":        str,
    "gps_baud":               int,
    "api_base_url":           str,
}


def _build_settings_keyboard(lang: str) -> InlineKeyboardMarkup:
    labels = _SETTING_LABELS.get(lang, _SETTING_LABELS["en"])
    rows = []
    for key, label in labels.items():
        rows.append([InlineKeyboardButton(label, callback_data=f"set:{key}")])
    rows.append([InlineKeyboardButton("❌ Cancel" if lang == "en" else "❌ إلغاء", callback_data="set:cancel")])
    return InlineKeyboardMarkup(rows)


# ── Main class ────────────────────────────────────────────────────────────────


class TelegramBot:
    def __init__(
        self,
        cfg: Config,
        db: FaceDatabase,
        voice: VoiceOutput,
        recognizer=None,
        gps_fn=None,
    ):
        self._cfg = cfg
        self._db = db
        self._voice = voice
        self._recognizer = recognizer
        self._gps_fn = gps_fn

        self._loop = asyncio.new_event_loop()
        self._app: Optional[Application] = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ── Language helper ───────────────────────────────────────────────────────

    def _lang(self) -> str:
        return getattr(self._cfg, "language", "en")

    def _t(self, key: str, **kwargs) -> str:
        return _t(key, self._lang(), **kwargs)

    # ── Thread / event loop ───────────────────────────────────────────────────

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._start())

    async def _start(self):
        self._app = Application.builder().token(TELEGRAM_TOKEN).build()
        self._register_handlers()
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        await asyncio.Event().wait()

    # ── Auth helpers ──────────────────────────────────────────────────────────

    def _is_authorised(self, chat_id: int) -> bool:
        return self._cfg.is_authorised(chat_id)

    def _is_owner(self, chat_id: int) -> bool:
        return self._cfg.is_owner(chat_id)

    async def _check_auth(self, update: Update) -> bool:
        """Return True if the sender is authorised; send rejection otherwise."""
        if self._is_authorised(update.effective_chat.id):
            return True
        # During first-run the owner slot is not yet claimed — stay silent so
        # only the right /start message triggers the bootstrap.
        if self._cfg.owner_chat_id == 0:
            return False
        await update.message.reply_text(_t("not_authorised", "en"))
        return False

    async def _check_owner(self, update: Update) -> bool:
        """Return True if sender is the owner; send rejection otherwise."""
        if self._is_owner(update.effective_chat.id):
            return True
        await update.message.reply_text(self._t("owner_only"))
        return False

    # ── Handler registration ──────────────────────────────────────────────────

    def _register_handlers(self):
        app = self._app

        # /start — bootstrap + welcome
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))

        # Language / simple settings
        app.add_handler(CommandHandler("language", self._cmd_language))
        app.add_handler(CommandHandler("setdistance", self._cmd_set_distance))
        app.add_handler(CommandHandler("setcooldown", self._cmd_set_cooldown))
        app.add_handler(CommandHandler("setapi", self._cmd_set_api))

        # Settings inline menu
        app.add_handler(CommandHandler("settings", self._cmd_settings))
        app.add_handler(CallbackQueryHandler(self._cb_settings, pattern=r"^set:"))
        app.add_handler(
            ConversationHandler(
                entry_points=[CallbackQueryHandler(self._cb_setting_selected, pattern=r"^set:[^c]")],
                states={
                    ST_AWAIT_SETTING_VALUE: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self._setting_receive_value)
                    ],
                },
                fallbacks=[CommandHandler("cancel", self._setting_cancel)],
                per_chat=True,
            )
        )

        # /adduser conversation
        app.add_handler(
            ConversationHandler(
                entry_points=[CommandHandler("adduser", self._cmd_adduser)],
                states={
                    ST_ADDUSER_INPUT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self._adduser_receive)
                    ],
                },
                fallbacks=[CommandHandler("cancel", self._adduser_cancel)],
                per_chat=True,
            )
        )

        # /removeuser conversation
        app.add_handler(
            ConversationHandler(
                entry_points=[CommandHandler("removeuser", self._cmd_removeuser)],
                states={
                    ST_REMOVEUSER_INPUT: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self._removeuser_receive)
                    ],
                },
                fallbacks=[CommandHandler("cancel", self._removeuser_cancel)],
                per_chat=True,
            )
        )

        app.add_handler(CommandHandler("users", self._cmd_users))

        # Face commands
        app.add_handler(CommandHandler("list", self._cmd_list))
        app.add_handler(CommandHandler("remove", self._cmd_remove))
        app.add_handler(
            ConversationHandler(
                entry_points=[CommandHandler("add", self._cmd_add)],
                states={
                    ST_ADD_PHOTO: [
                        MessageHandler(filters.PHOTO & ~filters.COMMAND, self._add_receive_photo)
                    ],
                    ST_ADD_NAME: [
                        MessageHandler(filters.TEXT & ~filters.COMMAND, self._add_receive_name)
                    ],
                },
                fallbacks=[CommandHandler("cancel", self._add_cancel)],
                per_chat=True,
            )
        )

        # GPS
        app.add_handler(CommandHandler("location", self._cmd_location))

        # Free text / photo handlers (unknown face reply + /skip)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

    # ── /start ────────────────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = update.effective_user

        # ── First-run: claim ownership ──
        if self._cfg.owner_chat_id == 0:
            self._cfg.owner_chat_id = chat_id
            self._cfg.owner_username = (user.username or "").lstrip("@")
            cfg_module.save(self._cfg)
            print(f"[Bot] Owner registered: {chat_id} (@{self._cfg.owner_username})")
            await update.message.reply_text(
                _t("owner_claimed", "en"),   # always EN for first message
                parse_mode="Markdown",
            )
            return

        # ── Authorised user ──
        if self._is_authorised(chat_id):
            key = "welcome_back" if self._is_owner(chat_id) else "welcome_user"
            await update.message.reply_text(self._t(key))
        # Strangers are silently ignored

    # ── /help ─────────────────────────────────────────────────────────────────

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        await update.message.reply_text(self._t("help"), parse_mode="MarkdownV2")

    # ── /status ───────────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        cfg = self._cfg
        user_list = ", ".join(
            f"{uid}" + (f" (@{uname})" if uname else "")
            for uid, uname in zip(cfg.allowed_chat_ids, cfg.allowed_usernames)
        ) or ("None" if self._lang() == "en" else "لا أحد")
        await update.message.reply_text(
            self._t(
                "status",
                language=cfg.language,
                distance=cfg.detection_distance_cm,
                cooldown=cfg.cooldown_seconds,
                camera=cfg.camera_index,
                gps_port=cfg.gps_serial_port,
                gps_baud=cfg.gps_baud,
                api=cfg.api_base_url,
                owner=cfg.owner_chat_id,
                users=user_list,
            ),
            parse_mode="Markdown",
        )

    # ── /language ─────────────────────────────────────────────────────────────

    async def _cmd_language(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        if not ctx.args or ctx.args[0].lower() not in ("en", "ar"):
            await update.message.reply_text(self._t("lang_invalid"))
            return
        lang = ctx.args[0].lower()
        self._cfg.language = lang
        cfg_module.save(self._cfg)
        await update.message.reply_text(_t("lang_set", lang))

    # ── /setdistance ──────────────────────────────────────────────────────────

    async def _cmd_set_distance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        if not ctx.args:
            await update.message.reply_text(self._t("missing_arg", usage="/setdistance <cm>"))
            return
        try:
            val = float(ctx.args[0])
        except ValueError:
            await update.message.reply_text(self._t("invalid_number"))
            return
        self._cfg.detection_distance_cm = val
        cfg_module.save(self._cfg)
        await update.message.reply_text(self._t("set_distance_ok", val=val))

    # ── /setcooldown ──────────────────────────────────────────────────────────

    async def _cmd_set_cooldown(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        if not ctx.args:
            await update.message.reply_text(self._t("missing_arg", usage="/setcooldown <seconds>"))
            return
        try:
            val = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text(self._t("invalid_number"))
            return
        self._cfg.cooldown_seconds = val
        cfg_module.save(self._cfg)
        await update.message.reply_text(self._t("set_cooldown_ok", val=val))

    # ── /setapi ───────────────────────────────────────────────────────────────

    async def _cmd_set_api(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        if not ctx.args:
            await update.message.reply_text(self._t("missing_arg", usage="/setapi <url>"))
            return
        val = " ".join(ctx.args).strip()
        self._cfg.api_base_url = val
        cfg_module.save(self._cfg)
        await update.message.reply_text(self._t("set_api_ok"))

    # ── /settings inline menu ─────────────────────────────────────────────────

    async def _cmd_settings(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        await update.message.reply_text(
            self._t("settings_menu"),
            parse_mode="Markdown",
            reply_markup=_build_settings_keyboard(self._lang()),
        )

    async def _cb_settings(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data  # "set:<key>" or "set:cancel"
        if data == "set:cancel":
            await query.edit_message_text(self._t("settings_cancel"))
            return
        key = data.split(":", 1)[1]
        current = getattr(self._cfg, key, "?")
        ctx.user_data[_CTX_SETTING_KEY] = key
        await query.edit_message_text(
            self._t("settings_ask_value", key=key, current=current),
            parse_mode="Markdown",
        )

    async def _cb_setting_selected(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        # handled inside _cb_settings — this entry point is for the ConversationHandler
        return ST_AWAIT_SETTING_VALUE

    async def _setting_receive_value(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        key = ctx.user_data.get(_CTX_SETTING_KEY)
        if not key:
            return ConversationHandler.END
        raw = update.message.text.strip()
        expected_type = _SETTING_TYPES.get(key, str)
        try:
            value = expected_type(raw)
        except (ValueError, TypeError):
            await update.message.reply_text(self._t("settings_invalid"))
            return ST_AWAIT_SETTING_VALUE
        setattr(self._cfg, key, value)
        cfg_module.save(self._cfg)
        await update.message.reply_text(
            self._t("settings_saved", key=key, value=value),
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    async def _setting_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._t("settings_cancel"))
        return ConversationHandler.END

    # ── /adduser ──────────────────────────────────────────────────────────────

    async def _cmd_adduser(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return ConversationHandler.END
        await update.message.reply_text(self._t("adduser_ask"))
        return ST_ADDUSER_INPUT

    async def _adduser_receive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        raw = update.message.text.strip()
        username = ""
        chat_id = None

        if raw.startswith("@"):
            # We store the username; we can't resolve it to a chat_id without
            # a message from that user, so we ask the owner to forward the chat_id.
            await update.message.reply_text(
                self._t("adduser_invalid") + "\n\n"
                + ("ℹ️ Forward a message from that user or share their numeric chat ID."
                   if self._lang() == "en"
                   else "ℹ️ أعد توجيه رسالة من ذلك المستخدم أو شارك معرّف محادثته الرقمي.")
            )
            return ST_ADDUSER_INPUT
        else:
            try:
                chat_id = int(raw)
            except ValueError:
                await update.message.reply_text(self._t("adduser_invalid"))
                return ST_ADDUSER_INPUT

        added = self._cfg.add_user(chat_id, username)
        if added:
            cfg_module.save(self._cfg)
            await update.message.reply_text(self._t("adduser_ok"))
        else:
            await update.message.reply_text(self._t("adduser_exists"))
        return ConversationHandler.END

    async def _adduser_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._t("adduser_cancel"))
        return ConversationHandler.END

    # ── /removeuser ───────────────────────────────────────────────────────────

    async def _cmd_removeuser(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return ConversationHandler.END
        await update.message.reply_text(self._t("removeuser_ask"))
        return ST_REMOVEUSER_INPUT

    async def _removeuser_receive(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        raw = update.message.text.strip()
        removed = False

        if raw.startswith("@"):
            removed = self._cfg.remove_user_by_username(raw)
        else:
            try:
                chat_id = int(raw)
                removed = self._cfg.remove_user_by_id(chat_id)
            except ValueError:
                await update.message.reply_text(self._t("adduser_invalid"))
                return ST_REMOVEUSER_INPUT

        if removed:
            cfg_module.save(self._cfg)
            await update.message.reply_text(self._t("removeuser_ok"))
        else:
            await update.message.reply_text(self._t("removeuser_notfound"))
        return ConversationHandler.END

    async def _removeuser_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._t("removeuser_cancel"))
        return ConversationHandler.END

    # ── /users ────────────────────────────────────────────────────────────────

    async def _cmd_users(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_owner(update):
            return
        if not self._cfg.allowed_chat_ids:
            await update.message.reply_text(self._t("users_empty"))
            return
        lines = []
        for uid, uname in zip(self._cfg.allowed_chat_ids, self._cfg.allowed_usernames):
            entry = f"• `{uid}`"
            if uname:
                entry += f" (@{uname})"
            lines.append(entry)
        await update.message.reply_text(
            self._t("users_list", list="\n".join(lines)),
            parse_mode="Markdown",
        )

    # ── /list ─────────────────────────────────────────────────────────────────

    async def _cmd_list(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        names = self._db.list_names()
        if names:
            await update.message.reply_text(
                self._t("faces_list", list="\n".join(f"• {n}" for n in names))
            )
        else:
            await update.message.reply_text(self._t("faces_empty"))

    # ── /remove ───────────────────────────────────────────────────────────────

    async def _cmd_remove(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        if not ctx.args:
            await update.message.reply_text(self._t("missing_arg", usage="/remove <name>"))
            return
        name = " ".join(ctx.args)
        if self._db.remove(name):
            await update.message.reply_text(self._t("face_removed", name=name))
        else:
            await update.message.reply_text(self._t("face_not_found", name=name))

    # ── /add (ConversationHandler) ────────────────────────────────────────────

    async def _cmd_add(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return ConversationHandler.END
        await update.message.reply_text(self._t("add_send_photo"))
        return ST_ADD_PHOTO

    async def _add_receive_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if self._recognizer is None:
            await update.message.reply_text(self._t("add_no_recognizer"))
            return ConversationHandler.END

        photo = update.message.photo[-1]
        file = await photo.get_file()
        tmp_path = os.path.join(
            os.path.dirname(__file__), "captured", f"add_{photo.file_id}.jpg"
        )
        os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
        await file.download_to_drive(tmp_path)

        import cv2
        img = cv2.imread(tmp_path)
        if img is None:
            await update.message.reply_text(self._t("add_no_read"))
            return ST_ADD_PHOTO

        encoding = self._recognizer.get_encoding(img)
        if encoding is None:
            await update.message.reply_text(self._t("add_no_face"))
            return ST_ADD_PHOTO

        ctx.user_data[_CTX_ADD_ENCODING] = encoding
        await update.message.reply_text(self._t("add_send_name"))
        return ST_ADD_NAME

    async def _add_receive_name(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        encoding = ctx.user_data.pop(_CTX_ADD_ENCODING, None)
        if encoding is None:
            return ConversationHandler.END
        name = update.message.text.strip()
        self._db.add_face(name, encoding)
        await update.message.reply_text(self._t("add_saved", name=name))
        return ConversationHandler.END

    async def _add_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        ctx.user_data.pop(_CTX_ADD_ENCODING, None)
        await update.message.reply_text(self._t("add_cancel"))
        return ConversationHandler.END

    # ── /location ─────────────────────────────────────────────────────────────

    async def _cmd_location(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        if self._gps_fn:
            loc = self._gps_fn()
            if loc:
                lat, lon = loc
                await update.message.reply_location(latitude=lat, longitude=lon)
                return
        await update.message.reply_text(self._t("no_gps_fix"))

    # ── Free text handler ─────────────────────────────────────────────────────

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await self._check_auth(update):
            return
        text = update.message.text.strip()

        # /skip (sent as plain text by some clients)
        if text.lower() == "/skip":
            ctx.user_data.pop(_CTX_PENDING_ENCODING, None)
            await update.message.reply_text(self._t("skip_ok"))
            return

        # Unknown-face reply flow: name the pending face
        encoding = ctx.user_data.get(_CTX_PENDING_ENCODING)
        if encoding is not None:
            self._db.add_face(text, encoding)
            ctx.user_data.pop(_CTX_PENDING_ENCODING, None)
            await update.message.reply_text(self._t("face_registered", name=text))
            self._voice.speak(
                f"{text} " + ("has been registered" if self._lang() == "en" else "تم تسجيله")
            )
        else:
            await update.message.reply_text(self._t("no_pending_face"))

    # ── Outbound broadcast ────────────────────────────────────────────────────

    def _all_recipients(self) -> List[int]:
        """Owner + all allowed users."""
        return self._cfg.all_chat_ids()

    def send_capture(
        self,
        photo_path: str,
        encoding=None,
        known_name: Optional[str] = None,
        chat_id: Optional[int] = None,
    ) -> None:
        """Broadcast a captured photo to all recipients. Thread-safe."""
        asyncio.run_coroutine_threadsafe(
            self._do_send_capture(photo_path, encoding, known_name, chat_id),
            self._loop,
        )

    async def _do_send_capture(
        self,
        photo_path: str,
        encoding,
        known_name: Optional[str],
        target_chat_id: Optional[int],
    ):
        bot: Bot = self._app.bot
        recipients = [target_chat_id] if target_chat_id else self._all_recipients()
        if known_name:
            caption = _t("alert_known", self._lang(), name=known_name)
        elif encoding is not None:
            caption = _t("alert_unknown", self._lang())
        else:
            caption = _t("alert_scene", self._lang())

        with open(photo_path, "rb") as f:
            photo_bytes = f.read()

        for cid in recipients:
            try:
                import io
                await bot.send_photo(
                    chat_id=cid,
                    photo=io.BytesIO(photo_bytes),
                    caption=caption,
                )
                # Store pending encoding per-chat in user_data
                if encoding is not None:
                    # We'll put it in a shared dict keyed by chat_id
                    # Accessed later in _handle_text via ctx.user_data
                    # But since we're outside a handler we store in bot_data
                    if "pending_encodings" not in self._app.bot_data:
                        self._app.bot_data["pending_encodings"] = {}
                    self._app.bot_data["pending_encodings"][cid] = encoding
            except Exception as e:
                print(f"[Bot] Failed to send photo to {cid}: {e}")

    def send_text(self, text: str) -> None:
        """Broadcast a plain text message to all recipients. Thread-safe."""
        asyncio.run_coroutine_threadsafe(self._do_send_text(text), self._loop)

    async def _do_send_text(self, text: str):
        if self._app is None:
            return
        for cid in self._all_recipients():
            try:
                await self._app.bot.send_message(chat_id=cid, text=text)
            except Exception as e:
                print(f"[Bot] Failed to send text to {cid}: {e}")

    def send_gps_location(self, location: Tuple[float, float]) -> None:
        """Broadcast GPS coordinates to all recipients. Thread-safe."""
        asyncio.run_coroutine_threadsafe(self._do_send_gps(location), self._loop)

    async def _do_send_gps(self, location: Tuple[float, float]):
        lat, lon = location
        for cid in self._all_recipients():
            try:
                await self._app.bot.send_location(
                    chat_id=cid, latitude=lat, longitude=lon
                )
            except Exception as e:
                print(f"[Bot] Failed to send GPS to {cid}: {e}")
