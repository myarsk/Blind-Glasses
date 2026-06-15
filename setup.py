"""
setup.py — DEPRECATED

All first-run configuration is now handled through the Telegram bot.

To set up the Blind Glasses system:
  1. Run: python main.py
  2. Open Telegram and send /start to the bot.
  3. The first person to send /start becomes the permanent owner.
  4. Use /settings or individual commands to configure everything:
       /language ar          — switch to Arabic
       /setdistance 60       — detection distance in cm
       /setcooldown 300      — cooldown in seconds
       /setapi <url>         — vision AI API endpoint
       /adduser              — add an allowed user
       /help                 — full command list

The bot token is hardcoded and does not need to be provided.
"""

print(
    "\n"
    "⚠️  setup.py is no longer needed.\n"
    "\n"
    "All configuration is done through the Telegram bot:\n"
    "  1. Run:  python main.py\n"
    "  2. Send /start to the bot in Telegram.\n"
    "     The first person to do so becomes the owner.\n"
    "  3. Use /help to see all available commands.\n"
)
