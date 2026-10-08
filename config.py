import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в Environment Variables")

if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY не задан в Environment Variables")
