import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("Переменная BOT_TOKEN не задана")

if not GEMINI_API_KEY:
    raise RuntimeError("Переменная GEMINI_API_KEY не задана")
