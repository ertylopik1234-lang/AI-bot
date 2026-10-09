from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    filters,
)
from groq import AsyncGroq
from datetime import datetime, timedelta, timezone
import random
import sqlite3
import asyncio
import threading
import os
from flask import Flask, jsonify

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()


# =========================
# ИНФОРМАЦИЯ О БОТЕ
# =========================

BOT_NAME = "Vega CHAT"
BOT_VERSION = "2.3.1"


# =========================
# GROQ
# =========================

client = AsyncGroq(api_key=GROQ_API_KEY or "missing-groq-api-key")
MODEL = "openai/gpt-oss-20b"


# =========================
# АДМИНИСТРАТОРЫ
# =========================

ADMINS = {
    "DKLART",
    "Qnwru",
}


# =========================
# НАСТРОЙКИ
# =========================

settings = {
    "welcome": True,
    "games": True,
}


# =========================
# СТАТИСТИКА
# =========================

stats = {
    "games": 0,
    "messages": 0,
    "users": set(),
    "game_users": set(),
    "ai_requests": 0,
    "translations": 0,
}


# =========================
# ВАРНЫ
# =========================

warnings = {}


# =========================
# ИЗВЕСТНЫЕ ПОЛЬЗОВАТЕЛИ
# username -> user_id
# =========================

known_users = {}


# =========================
# ЛОГИ
# =========================

logs = []


def add_log(text):
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    logs.append(f"[{timestamp}] {text}")

    if len(logs) > 100:
        logs.pop(0)


# =========================
# SQLITE
# =========================

DB_FILE = os.environ.get("VEGA_DB_PATH", "vega.db")
STARTED_AT = datetime.now(timezone.utc)


def db_connect():
    """SQLite connection with sensible concurrency and durability settings."""
    folder = os.path.dirname(os.path.abspath(DB_FILE))
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=15, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db():
    conn = db_connect()
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT NOT NULL,
                ai_requests INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                games INTEGER NOT NULL DEFAULT 0,
                translations INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_active TEXT NOT NULL
            )
            """
        )

        # Миграция старой базы 2.0.0.
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(profiles)").fetchall()
        }
        migrations = {
            "messages": "ALTER TABLE profiles ADD COLUMN messages INTEGER NOT NULL DEFAULT 0",
            "games": "ALTER TABLE profiles ADD COLUMN games INTEGER NOT NULL DEFAULT 0",
            "translations": "ALTER TABLE profiles ADD COLUMN translations INTEGER NOT NULL DEFAULT 0",
            "first_seen": "ALTER TABLE profiles ADD COLUMN first_seen TEXT",
            "last_active": "ALTER TABLE profiles ADD COLUMN last_active TEXT",
        }
        for column, sql in migrations.items():
            if column not in columns:
                conn.execute(sql)

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE profiles SET first_seen = COALESCE(first_seen, ?), last_active = COALESCE(last_active, ?) ",
            (now, now),
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jokes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                joke TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                first_name TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_events(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_user_time ON activity_events(user_id, created_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_owner ON conversation_history(chat_id, user_id, id)")
        conn.commit()
    finally:
        conn.close()



def record_activity(user, points=1):
    if not user:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO activity_events (user_id, first_name, points, created_at) VALUES (?, ?, ?, ?)",
            (user.id, user.first_name or "Без имени", int(points), now),
        )
        conn.commit()
    finally:
        conn.close()


def load_conversation(chat_id, user_id, limit=12):
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT role, content FROM conversation_history WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
            (chat_id, user_id, limit),
        ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]
    finally:
        conn.close()


def save_conversation_message(chat_id, user_id, role, content, limit=12):
    content = (content or "").strip()
    if not content:
        return
    conn = db_connect()
    try:
        conn.execute(
            "INSERT INTO conversation_history (chat_id, user_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, user_id, role, content[:12000], datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "DELETE FROM conversation_history WHERE chat_id = ? AND user_id = ? AND id NOT IN "
            "(SELECT id FROM conversation_history WHERE chat_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?)",
            (chat_id, user_id, chat_id, user_id, limit),
        )
        conn.commit()
    finally:
        conn.close()


def clear_conversation(chat_id, user_id):
    conn = db_connect()
    try:
        conn.execute("DELETE FROM conversation_history WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        conn.commit()
    finally:
        conn.close()


def format_period_top(period, limit=10):
    now = datetime.now(timezone.utc)
    if period == "day":
        since = (now - timedelta(days=1)).isoformat()
        heading = "🏆 ТОП ЗА ПОСЛЕДНИЕ 24 ЧАСА"
    else:
        since = (now - timedelta(days=7)).isoformat()
        heading = "🏆 ТОП ЗА ПОСЛЕДНИЕ 7 ДНЕЙ"
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT user_id, MAX(first_name), SUM(points) AS score FROM activity_events "
            "WHERE created_at >= ? GROUP BY user_id ORDER BY score DESC, MAX(created_at) DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return heading + "\n\nПока активности нет."
    medals = ["🥇", "🥈", "🥉"]
    lines = [heading, ""]
    for i, (_, name, score) in enumerate(rows, 1):
        marker = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(f"{marker} {name} — {score} балл.")
    return "\n".join(lines)

def save_user(user):
    if not user:
        return

    first_name = user.first_name or "Без имени"
    now = datetime.now(timezone.utc).isoformat()

    conn = db_connect()
    try:
        conn.execute(
            """
            INSERT INTO profiles (
                user_id, first_name, ai_requests, messages, games,
                translations, first_seen, last_active
            )
            VALUES (?, ?, 0, 0, 0, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                first_name=excluded.first_name,
                last_active=excluded.last_active
            """,
            (user.id, first_name, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def increment_profile_stat(user, field, amount=1):
    if not user or field not in {"ai_requests", "messages", "games", "translations"}:
        return

    save_user(user)
    now = datetime.now(timezone.utc).isoformat()
    conn = db_connect()
    try:
        conn.execute(
            f"UPDATE profiles SET {field} = {field} + ?, last_active = ?, first_name = ? WHERE user_id = ?",
            (amount, now, user.first_name or "Без имени", user.id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_ai_requests(user):
    increment_profile_stat(user, "ai_requests")


def get_profile(user):
    if not user:
        return "❌ Не удалось получить профиль."

    save_user(user)
    conn = db_connect()
    try:
        row = conn.execute(
            """
            SELECT first_name, user_id, ai_requests, messages, games,
                   translations, first_seen, last_active
            FROM profiles WHERE user_id = ?
            """,
            (user.id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return "❌ Профиль не найден."

    first_name, user_id, ai_requests, messages, games, translations, first_seen, last_active = row

    try:
        first_dt = datetime.fromisoformat(first_seen).astimezone(timezone.utc)
        last_dt = datetime.fromisoformat(last_active).astimezone(timezone.utc)
        first_text = first_dt.strftime("%d.%m.%Y")
        last_text = last_dt.strftime("%d.%m.%Y %H:%M UTC")
    except Exception:
        first_text = str(first_seen)[:10]
        last_text = str(last_active)

    return (
        "👤 Профиль\n\n"
        f"📝 Имя: {first_name}\n"
        f"🆔 ID: {user_id}\n\n"
        f"🧠 Запросов ИИ: {ai_requests}\n"
        f"🌐 Переводов: {translations}\n"
        f"💬 Сообщений: {messages}\n"
        f"🎮 Игр сыграно: {games}\n\n"
        f"📅 В боте с: {first_text}\n"
        f"🕐 Последняя активность: {last_text}"
    )


def get_total_profile_requests():
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(ai_requests), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_translations():
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(translations), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_messages():
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_games():
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(games), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_joke_history(limit=30):
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT joke FROM jokes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def save_joke(joke):
    joke = joke.strip()
    normalized = normalize_joke(joke)
    if not normalized:
        return False
    conn = db_connect()
    try:
        existing = conn.execute(
            "SELECT joke FROM jokes ORDER BY id DESC LIMIT 250"
        ).fetchall()
        if any(normalize_joke(row[0]) == normalized for row in existing):
            return False
        conn.execute(
            "INSERT INTO jokes (joke, created_at) VALUES (?, ?)",
            (joke, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_top_users(limit=10):
    conn = db_connect()
    try:
        return conn.execute(
            """
            SELECT user_id, first_name,
                   (messages + ai_requests + translations + games) AS activity
            FROM profiles
            ORDER BY activity DESC, last_active DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def get_user_rank(user):
    save_user(user)
    conn = db_connect()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) + 1 FROM profiles p
            WHERE (p.messages + p.ai_requests + p.translations + p.games) > (
                SELECT messages + ai_requests + translations + games
                FROM profiles WHERE user_id = ?
            )
            """,
            (user.id,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def format_top(user):
    rows = get_top_users(10)
    rank = get_user_rank(user)

    lines = ["🏆 ТОП АКТИВНОСТИ", ""]
    medals = ["🥇", "🥈", "🥉"]

    if not rows:
        lines.append("Пока здесь никого нет.")
    else:
        for index, (_, first_name, activity) in enumerate(rows, start=1):
            medal = medals[index - 1] if index <= 3 else f"{index}."
            lines.append(f"{medal} {first_name} — {activity}")

    lines.extend(["", f"📊 Твоя позиция: #{rank}"])
    return "\n".join(lines)


# =========================
# ПРОВЕРКА АДМИНА
# =========================

def is_admin(username):
    if not username:
        return False

    return username.lstrip("@").lower() in {
        name.lower() for name in ADMINS
    }


# =========================
# ЗАПОМИНАЕМ ПОЛЬЗОВАТЕЛЯ
# =========================

def remember_user(user):
    if not user:
        return

    save_user(user)
    record_activity(user)

    if user.username:
        known_users[user.username.lower()] = user.id


# =========================
# ОПРОСЫ
# =========================

async def poll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a Telegram poll: /poll Question | option 1 | option 2"""
    message = update.effective_message
    if not message:
        return
    raw = " ".join(context.args).strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 3 or not parts[0] or any(not x for x in parts[1:]):
        await message.reply_text(
            "📊 Создание опроса:\n"
            "/poll Вопрос | Вариант 1 | Вариант 2\n\n"
            "Нужно от 2 до 10 вариантов. Пример:\n"
            "/poll Какой режим добавить? | Игры | ИИ | Музыка"
        )
        return
    question, options = parts[0], parts[1:]
    if len(options) > 10:
        await message.reply_text("❌ В опросе может быть не больше 10 вариантов.")
        return
    if len(question) > 300 or any(len(option) > 100 for option in options):
        await message.reply_text("❌ Слишком длинный вопрос или вариант ответа.")
        return
    try:
        await message.reply_poll(question=question, options=options, is_anonymous=True)
        add_log(f"Опрос создан: user={update.effective_user.id if update.effective_user else 'unknown'}")
    except Exception as exc:
        print(f"Poll error: {exc}")
        await message.reply_text("❌ Не удалось создать опрос. Попробуй ещё раз.")




# =========================
# /profile
# =========================

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user:
        return

    remember_user(user)

    await update.message.reply_text(
        get_profile(user)
    )


# =========================
# /translate
# =========================

async def translate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not update.message:
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "🌐 Использование:\n"
            "/translate <язык> <текст>\n\n"
            "Примеры:\n"
            "/translate en Привет, как дела?\n"
            "/translate fi Доброе утро!\n"
            "/translate de Я люблю программирование.\n\n"
            "Можно использовать коды языков: "
            "ru, en, de, fi, es, fr, it, uk и другие."
        )
        return

    target_language = context.args[0].strip().lower()
    source_text = " ".join(context.args[1:]).strip()

    if not source_text:
        await update.message.reply_text(
            "❌ Укажи текст для перевода."
        )
        return

    language_names = {
        "ru": "русский",
        "en": "английский",
        "de": "немецкий",
        "fi": "финский",
        "es": "испанский",
        "fr": "французский",
        "it": "итальянский",
        "uk": "украинский",
        "pl": "польский",
        "sv": "шведский",
        "no": "норвежский",
        "da": "датский",
        "nl": "нидерландский",
        "pt": "португальский",
        "tr": "турецкий",
        "cs": "чешский",
        "sk": "словацкий",
        "ja": "японский",
        "ko": "корейский",
        "zh": "китайский",
        "ar": "арабский",
    }

    target = language_names.get(
        target_language,
        target_language
    )

    try:
        await update.message.chat.send_action("typing")

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты профессиональный переводчик. "
                        f"Переведи текст на {target}. "
                        "Сохраняй смысл, стиль и форматирование. "
                        "Не добавляй объяснений, комментариев "
                        "или кавычек от себя. "
                        "Верни только готовый перевод."
                    ),
                },
                {
                    "role": "user",
                    "content": source_text,
                },
            ],
            temperature=0.2,
            max_tokens=2000,
        )

        answer = response.choices[0].message.content

        if not answer:
            answer = "❌ Переводчик не вернул результат."

        await update.message.reply_text(
            f"🌐 Перевод ({target}):\n\n{answer}"
        )

        increment_profile_stat(user, "translations")
        record_activity(user, points=2)
        stats["translations"] += 1

    except Exception as e:
        print(f"Translate error: {e}")

        await update.message.reply_text(
            "❌ Произошла ошибка при переводе."
        )


# =========================
# MINI APP
# =========================

def miniapp_url():
    """Public HTTPS URL of the Mini App hosted by this Render service."""
    custom = os.environ.get("MINI_APP_URL", "").strip()
    if custom:
        return custom.rstrip("/")
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    return f"{render_url}/miniapp" if render_url else ""


def miniapp_keyboard_button(private_chat=True):
    url = miniapp_url()
    if not url:
        return None
    if private_chat:
        return InlineKeyboardButton("📱 Открыть Mini App", web_app=WebAppInfo(url=url))
    return InlineKeyboardButton("📱 Открыть Mini App", url=url)


async def app_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    url = miniapp_url()
    if not url:
        await message.reply_text(
            "📱 Mini App пока не настроен: укажи публичный HTTPS-адрес сервиса в переменной MINI_APP_URL на Render."
        )
        return
    if update.effective_chat and update.effective_chat.type == "private":
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📱 Открыть Vega CHAT", web_app=WebAppInfo(url=url))]])
    else:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📱 Открыть Vega CHAT", url=url)]])
    await message.reply_text(
        "📱 Vega CHAT Mini App\n\nИнформация о боте и краткая инструкция по командам.",
        reply_markup=keyboard,
    )


# =========================
# /help
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton(
                "🤖 ИИ",
                callback_data="help_ai"
            ),
            InlineKeyboardButton(
                "🎮 Игры",
                callback_data="help_games"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Группа",
                callback_data="help_group"
            ),
            InlineKeyboardButton(
                "ℹ️ Инфо",
                callback_data="help_info"
            ),
        ],
        [
            InlineKeyboardButton(
                "🏆 Топ",
                callback_data="help_top"
            ),
        ],
        [
            miniapp_keyboard_button(bool(update.effective_chat and update.effective_chat.type == "private")),
        ] if miniapp_url() else [],
    ]
    keyboard = [row for row in keyboard if row]

    await update.message.reply_text(
        "📚 Vega CHAT — помощь\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# HELP КНОПКИ
# =========================

async def help_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if query.data == "help_ai":
        text = (
            "🤖 ИИ\n\n"
            "Напиши:\n"
            "ИИ <твой вопрос>\n\n"
            "Например:\n"
            "ИИ придумай название\n"
            "ИИ расскажи анекдот\n"
            "ИИ помоги с кодом\n\n"
            "🌐 Перевод:\n"
            "/translate en Привет\n\n"
            "👤 Профиль:\n"
            "/profile\n\n"
            "🏆 Топ активности:\n"
            "/top — за всё время\n"
            "/topday — за 24 часа\n"
            "/topweek — за 7 дней\n\n"
            "😂 Анекдоты по категориям: /joke\n"
            "🧠 Очистить память ИИ: /reset\n"
            "📊 Статус бота: /status\n\n"
            "📊 Опрос: /poll Вопрос | Вариант 1 | Вариант 2\n"
        )

    elif query.data == "help_games":
        if settings["games"]:
            text = (
                "🎮 Игры\n\n"
                "Напиши «ИГРА».\n\n"
                "Доступно:\n"
                "🎲 Кубик\n"
                "⚽ Футбол\n"
                "🏀 Баскетбол\n"
                "🎯 Дартс\n"
                "🎰 Казино\n"
                "🪙 Орёл/решка\n"
                "😂 Анекдот"
            )
        else:
            text = (
                "🎮 Игры\n\n"
                "🔴 Игры сейчас отключены."
            )

    elif query.data == "help_group":
        text = (
            "👥 Функции группы\n\n"
            "👋 Приветствие новых участников\n"
            "⚠️ Система варнов\n"
            "🔇 Мут после 3 варнов\n"
            "🔓 Снятие мута: /unmute\n"
            "📢 Вызов: /call\n\n"
            "Администраторы:\n"
            "/adminPANEL\n"
            "/warn @username\n"
            "/unmute @username"
        )

    elif query.data == "help_top":
        text = (
            "🏆 Топ активности\n\n"
            "Команды:\n"
            "/top — за всё время\n"
            "/topday — за последние 24 часа\n"
            "/topweek — за последние 7 дней\n\n"
            "В рейтинг попадают сообщения, команды и действия в боте."
        )

    elif query.data == "help_info":
        text = (
            "ℹ️ Vega CHAT\n\n"
            "🤖 Telegram AI-бот\n"
            "🧠 Powered by Groq\n"
            "🎮 Игры\n"
            "🛡 Модерация\n"
            "🌐 Переводчик\n"
            "👤 Профили\n"
            "💾 SQLite с журналированием WAL\n"
            "🧠 Память диалога ИИ\n"
            "😂 Анекдоты по категориям\n"
            "🏆 Топ за всё время, день и неделю\n"
            "📊 Команда /status\n\n"
            f"📦 Версия: {BOT_VERSION}\n\n"
            "👑 Создатель:\n"
            "@Qnwru"
        )
    else:
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="help_back"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def help_back(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [
            InlineKeyboardButton(
                "🤖 ИИ",
                callback_data="help_ai"
            ),
            InlineKeyboardButton(
                "🎮 Игры",
                callback_data="help_games"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Группа",
                callback_data="help_group"
            ),
            InlineKeyboardButton(
                "ℹ️ Инфо",
                callback_data="help_info"
            ),
        ],
        [
            InlineKeyboardButton(
                "🏆 Топ",
                callback_data="help_top"
            ),
        ],
        [
            miniapp_keyboard_button(bool(update.effective_chat and update.effective_chat.type == "private")),
        ] if miniapp_url() else [],
    ]
    keyboard = [row for row in keyboard if row]

    await query.edit_message_text(
        "📚 Vega CHAT — помощь\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# ОСНОВНЫЕ СООБЩЕНИЯ
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not update.message.text:
        return

    user = update.effective_user
    remember_user(user)

    text = update.message.text.strip()
    text_lower = text.lower()

    stats["messages"] += 1
    if user:
        increment_profile_stat(user, "messages")

    if user:
        stats["users"].add(user.id)

    # =========================
    # АДМИН
    # =========================

    if text_lower == "админ":
        await update.message.reply_text(
            "👑 Юзернейм моего создателя: @Qnwru\n"
            "🤖 Vega CHAT"
        )
        return

    # =========================
    # ИГРА
    # =========================

    if text_lower == "игра":
        if not settings["games"]:
            await update.message.reply_text(
                "🔴 Игры сейчас отключены "
                "администратором."
            )
            return

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎲 Кубик",
                    callback_data="game_dice"
                ),
                InlineKeyboardButton(
                    "⚽ Футбол",
                    callback_data="game_football"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🏀 Баскетбол",
                    callback_data="game_basketball"
                ),
                InlineKeyboardButton(
                    "🎯 Дартс",
                    callback_data="game_darts"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎰 Казино",
                    callback_data="game_casino"
                ),
                InlineKeyboardButton(
                    "🪙 Орёл/решка",
                    callback_data="game_coin"
                ),
            ],
            [
                InlineKeyboardButton(
                    "😂 Анекдот",
                    callback_data="game_joke"
                ),
            ],
        ]

        await update.message.reply_text(
            "🎮 Выбери игру:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # =========================
    # ИИ
    # =========================

    if not text_lower.startswith("ии"):
        return

    prompt = text[2:].strip()

    if not prompt:
        await update.message.reply_text(
            "🤖 Напиши запрос после «ИИ».\n\n"
            "Например:\n"
            "ИИ расскажи анекдот"
        )
        return

    try:
        stats["ai_requests"] += 1
        increment_ai_requests(user)

        await update.message.chat.send_action("typing")

        history = load_conversation(update.effective_chat.id, user.id) if user else []
        ai_messages = [
            {
                "role": "system",
                "content": (
                    "Ты полезный Telegram-ассистент Vega CHAT. "
                    "Отвечай понятно, дружелюбно и по существу. "
                    "Отвечай на языке пользователя и учитывай контекст прошлых реплик."
                ),
            },
            *history,
            {"role": "user", "content": prompt},
        ]
        response = await client.chat.completions.create(
            model=MODEL,
            messages=ai_messages,
            temperature=0.7,
            max_tokens=2000,
        )

        answer = response.choices[0].message.content

        if not answer:
            answer = "❌ ИИ не вернул ответ."
        elif user:
            save_conversation_message(update.effective_chat.id, user.id, "user", prompt)
            save_conversation_message(update.effective_chat.id, user.id, "assistant", answer)

        max_length = 4000

        for i in range(0, len(answer), max_length):
            await update.message.reply_text(
                answer[i:i + max_length]
            )

    except Exception as e:
        print(f"Groq error: {e}")

        await update.message.reply_text(
            "❌ Произошла ошибка "
            "при обращении к ИИ."
        )


# =========================
# ГЕНЕРАТОР АНЕКДОТОВ
# =========================

def normalize_joke(text):
    """Нормализация для надёжной проверки повторов."""
    import re
    text = (text or "").casefold().strip()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


JOKE_CATEGORIES = {
    "general": "обычный универсальный юмор",
    "school": "школа, уроки, учителя и домашние задания",
    "it": "IT, программисты, компьютеры и баги",
    "work": "работа, офис и начальники",
    "animals": "животные и их забавные привычки",
    "absurd": "абсурдный, неожиданный и добрый юмор",
}
JOKE_CATEGORY_LABELS = {
    "general": "😄 Разные", "school": "🏫 Школьные", "it": "💻 IT",
    "work": "💼 Работа", "animals": "🐱 Животные", "absurd": "🤪 Абсурдные",
}


def joke_category_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(JOKE_CATEGORY_LABELS["general"], callback_data="joke_cat_general"),
         InlineKeyboardButton(JOKE_CATEGORY_LABELS["school"], callback_data="joke_cat_school")],
        [InlineKeyboardButton(JOKE_CATEGORY_LABELS["it"], callback_data="joke_cat_it"),
         InlineKeyboardButton(JOKE_CATEGORY_LABELS["work"], callback_data="joke_cat_work")],
        [InlineKeyboardButton(JOKE_CATEGORY_LABELS["animals"], callback_data="joke_cat_animals"),
         InlineKeyboardButton(JOKE_CATEGORY_LABELS["absurd"], callback_data="joke_cat_absurd")],
    ])


# Локальная коллекция коротких анекдотов — работает даже без Groq и интернета.
# Тексты написаны для Vega CHAT; при желании коллекцию можно расширять.
LOCAL_JOKES = {
    "general": [
        "— Доктор, у меня провалы в памяти. — И давно? — Что давно?",
        "— Ты почему опоздал? — Я боролся с желанием поспать. — И кто победил? — Оно. Я здесь только формально.",
        "— У тебя есть план на вечер? — Да. Не менять план на вечер.",
        "Купил книгу «Как перестать откладывать дела». Начну читать завтра.",
        "— Как настроение? — Как Wi-Fi в лифте: вроде есть надежда, но связи нет.",
        "— Ты оптимист? — Конечно! Даже когда всё идёт не так, я уверен: дальше будет интереснее.",
        "Мой будильник настолько настойчивый, что каждое утро мы расстаёмся на пять минут и снова встречаемся.",
        "— Что делаешь? — Ничего. — А вчера? — Не успел закончить.",
        "— У тебя хорошая память? — Отличная! Только иногда забываю, где её оставил.",
        "Самый короткий список дел — тот, который я уже перенёс на завтра.",
    ],
    "school": [
        "Учитель: — Почему домашнее задание не сделано? Ученик: — Я решил оставить вам немного интриги.",
        "— Что такое контрольная? — Это когда учитель проверяет знания, а ученик — удачу.",
        "Учитель: — Назови три времени глагола. Ученик: — Вчера я не выучил, сегодня не знаю, завтра не приду.",
        "— Почему ты разговариваешь с учебником? — Он единственный, кто сегодня открылся мне с новой стороны.",
        "На уроке: — Кто ответит на вопрос? В классе такая тишина, что даже мел решил не скрипеть.",
        "— Ты подготовился к экзамену? — Да. Подготовил друзей к тому, что я его провалю.",
        "Учитель: — Где твой дневник? — Он тоже не был готов к сегодняшнему уроку.",
        "— Что самое сложное в школе? — Услышать «это будет в тесте» и понять, что это было вчера.",
    ],
    "it": [
        "Программист назвал кота Багом. Теперь, когда кот что-то роняет, он говорит: «Это не ошибка, это фича». ",
        "— Почему программист не вышел из дома? — У него всё работало локально.",
        "Код был настолько чистым, что баги оставили его в покое и пошли искать проект попроще.",
        "— Ты проверил обновление? — Да. Теперь у меня две проблемы вместо одной. Значит, обновление работает.",
        "Программист объяснил компьютеру шутку. Компьютер ответил: «Смешно: false». ",
        "— Почему сервер грустит? — Его никто не спрашивает, как он себя чувствует, только почему он упал.",
        "Самый опасный вопрос в IT: «А что будет, если удалить вот эту строчку?»",
        "— Как понять, что программист устал? — Он пытается перезагрузить чайник.",
    ],
    "work": [
        "Начальник: — Почему ты ушёл ровно в шесть? Сотрудник: — Потому что в шесть я перестаю быть загадкой и становлюсь свободным человеком.",
        "— Как прошёл рабочий день? — Быстро. Я только открыл почту, а уже пора закрывать ноутбук.",
        "На собеседовании: — Ваш главный недостаток? — Честность. — Не думаю, что это недостаток. — Мне всё равно, что вы думаете.",
        "— У нас дружный коллектив! — Правда? — Да, все вместе ждём пятницу.",
        "Рабочий чат спросил: «Вы на месте?» Я ответил: «Морально — уже в отпуске». ",
        "— Почему отчёт пустой? — Я решил не перегружать руководство лишней информацией.",
        "Понедельник — это обновление системы, которое никто не просил устанавливать.",
        "— Ты любишь свою работу? — Конечно. Особенно ту часть, где я её заканчиваю.",
    ],
    "animals": [
        "Кот сел на клавиатуру и отправил начальнику письмо. Впервые в жизни у меня появилось алиби: это был кот.",
        "Собака принесла поводок. Человек вздохнул: даже у собаки планы на выходные лучше моих.",
        "— Почему кот смотрит в пустую стену? — Там открыто окно с мышью. Просто невидимое для людей.",
        "Попугай услышал слово «совещание» и впервые за день замолчал. Птица явно понимала корпоративную культуру.",
        "Кот не игнорирует тебя. Он просто проводит независимую оценку твоей полезности.",
        "Пёс посмотрел на хозяина, который говорил по телефону, и принёс мяч. «Хватит совещаться, у нас дела!»",
        "— Почему черепаха не опаздывает? — Она заранее принимает, что всё будет медленно.",
        "Кот уронил чашку со стола и посмотрел так, будто это был научный эксперимент с неожиданным результатом.",
    ],
    "absurd": [
        "Купил невидимые часы. Теперь я не вижу, как летит время.",
        "— Почему холодильник молчит? — Он хранит холодное спокойствие.",
        "У меня настолько умный диван, что каждый раз, когда я сажусь работать, он включает режим удержания пользователя.",
        "— Ты разговариваешь с растением? — Да. Оно хотя бы не отвечает «ок» и не пропадает на три дня.",
        "Будильник спросил, готов ли я к новому дню. Я нажал «отложить обновление».",
        "Вчера купил пакет воздуха. Продавец сказал, что это самая лёгкая покупка в моей жизни.",
        "— Почему лестница устала? — На неё весь день сваливали проблемы.",
        "Мой чайник настолько философский, что сначала долго кипит, а потом всё равно остывает.",
    ],
}


def get_local_joke(category="general"):
    """Выбирает анекдот из встроенной коллекции, стараясь избегать недавних повторов."""
    pool = LOCAL_JOKES.get(category, LOCAL_JOKES["general"])
    recent = {normalize_joke(item) for item in get_joke_history(250)}
    fresh = [item for item in pool if normalize_joke(item) not in recent]
    # Если категория уже исчерпана, всё равно ответим, а не покажем ошибку.
    return random.choice(fresh or pool)


async def generate_joke(category="general"):
    """Пробует Groq; при ошибке вызывающий код использует локальный резерв."""
    history = get_joke_history(40)
    history_text = "\n---\n".join(history)
    category_description = JOKE_CATEGORIES.get(category, JOKE_CATEGORIES["general"])
    prompt = (
        "Придумай оригинальный короткий смешной анекдот на русском языке. "
        f"Категория: {category_description}. "
        "Нужны понятная завязка и неожиданная концовка. "
        "Верни только текст анекдота, без заголовка."
    )
    if history_text:
        prompt += (
            "\n\nНе повторяй дословно эти недавно выданные анекдоты:\n" + history_text
        )
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Ты комедийный автор. Верни только короткий анекдот на русском языке."},
            {"role": "user", "content": prompt},
        ],
        temperature=1.15,
        max_tokens=350,
        timeout=18.0,
    )
    answer = (response.choices[0].message.content or "").strip()
    answer = answer.strip('"“”`* ')
    if len(answer) < 15:
        raise ValueError("Groq вернул слишком короткий ответ")
    return answer


async def send_generated_joke(chat_id, bot, user=None, category="general"):
    await bot.send_chat_action(chat_id=chat_id, action="typing")
    joke = None
    source = "Groq"
    # Groq может быть недоступен или вернуть повтор; в таком случае используем встроенную коллекцию.
    for _ in range(3):
        try:
            candidate = await generate_joke(category)
            if candidate and save_joke(candidate):
                joke = candidate
                break
        except Exception as e:
            print(f"Groq joke generation failed; using local fallback: {e}")
            break

    if not joke:
        source = "local"
        joke = get_local_joke(category)
        save_joke(joke)  # Сохранение необязательно: даже повтор не мешает отправке.

    if user:
        record_activity(user, points=2)
    await bot.send_message(chat_id=chat_id, text=f"😂 {joke}")
    if source == "local":
        print(f"Joke fallback used: category={category}")


async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    aliases = {"разные": "general", "школа": "school", "школьные": "school", "it": "it",
               "программирование": "it", "работа": "work", "животные": "animals", "абсурд": "absurd"}
    raw = context.args[0].lower() if context.args else ""
    category = aliases.get(raw, raw)
    if category not in JOKE_CATEGORIES:
        await update.message.reply_text("😂 Выбери категорию анекдота:", reply_markup=joke_category_keyboard())
        return
    await update.message.reply_text(f"😂 Категория: {JOKE_CATEGORY_LABELS[category]}. Придумываю...")
    await send_generated_joke(update.message.chat.id, context.bot, update.effective_user, category)


async def joke_category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.removeprefix("joke_cat_")
    if category not in JOKE_CATEGORIES:
        return
    await query.edit_message_text(f"😂 Категория: {JOKE_CATEGORY_LABELS[category]}. Придумываю...")
    await send_generated_joke(query.message.chat.id, context.bot, query.from_user, category)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message or not user:
        return
    remember_user(user)
    period = context.args[0].lower() if context.args else "all"
    if period in {"day", "день", "today"}:
        result = format_period_top("day")
    elif period in {"week", "неделя", "week"}:
        result = format_period_top("week")
    else:
        result = format_top(user)
    await update.message.reply_text(result)


async def top_day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        remember_user(update.effective_user)
        await update.message.reply_text(format_period_top("day"))


async def top_week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        remember_user(update.effective_user)
        await update.message.reply_text(format_period_top("week"))


async def reset_memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    clear_conversation(update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text("🧠 Память диалога очищена. Начнём с чистого листа!")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uptime = datetime.now(timezone.utc) - STARTED_AT
    total_seconds = int(uptime.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    conn = db_connect()
    try:
        users = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
        messages = conn.execute("SELECT COALESCE(SUM(messages), 0) FROM profiles").fetchone()[0]
        jokes = conn.execute("SELECT COUNT(*) FROM jokes").fetchone()[0]
    finally:
        conn.close()
    await update.message.reply_text(
        f"🤖 {BOT_NAME} v{BOT_VERSION}\n"
        "🟢 Статус: работает\n"
        f"⏱ Аптайм: {days} дн. {hours} ч. {minutes} мин.\n"
        f"👥 Пользователей в базе: {users}\n"
        f"💬 Сообщений: {messages}\n"
        f"😂 Анекдотов сохранено: {jokes}\n"
        f"🧠 Groq: {'настроен' if GROQ_API_KEY else 'не настроен'}"
    )


# =========================
# ИГРЫ
# =========================

async def game_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not settings["games"]:
        await query.answer(
            "🔴 Игры отключены.",
            show_alert=True
        )
        return

    await query.answer()

    if query.data == "game_joke":
        await query.edit_message_text("😂 Выбери категорию анекдота:", reply_markup=joke_category_keyboard())
        return

    games = {
        "game_dice": "🎲",
        "game_football": "⚽",
        "game_basketball": "🏀",
        "game_darts": "🎯",
        "game_casino": "🎰",
    }

    if query.data == "game_coin":
        result = random.choice(
            [
                "🪙 Орёл!",
                "🪙 Решка!"
            ]
        )

        stats["games"] += 1

        if query.from_user:
            increment_profile_stat(query.from_user, "games")
            record_activity(query.from_user, points=2)
            stats["game_users"].add(query.from_user.id)

        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=result
        )
        return

    emoji = games.get(query.data)

    if not emoji:
        return

    stats["games"] += 1

    if query.from_user:
        increment_profile_stat(query.from_user, "games")
        record_activity(query.from_user, points=2)
        stats["game_users"].add(query.from_user.id)

    await context.bot.send_dice(
        chat_id=query.message.chat.id,
        emoji=emoji
    )


# =========================
# ADMIN PANEL
# =========================

def admin_keyboard():
    return [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats"
            ),
        ],
        [
            InlineKeyboardButton(
                "👋 Приветствие: "
                + (
                    "🟢 ВКЛ"
                    if settings["welcome"]
                    else "🔴 ВЫКЛ"
                ),
                callback_data="admin_welcome"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎮 Игры: "
                + (
                    "🟢 ВКЛ"
                    if settings["games"]
                    else "🔴 ВЫКЛ"
                ),
                callback_data="admin_games"
            ),
        ],
        [
            InlineKeyboardButton(
                "📜 Логи",
                callback_data="admin_logs"
            ),
        ],
        [
            InlineKeyboardButton(
                "🆕 Обновления",
                callback_data="admin_updates"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="admin_refresh"
            ),
        ],
    ]


async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not user or not is_admin(user.username):
        await update.message.reply_text(
            "⛔ У тебя нет доступа "
            "к админ-панели."
        )
        return

    await update.message.reply_text(
        "🛡 Vega CHAT — ADMIN PANEL\n\n"
        "🔐 Доступ разрешён.\n"
        f"📦 Версия: {BOT_VERSION}\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(
            admin_keyboard()
        )
    )


# =========================
# ADMIN BUTTONS
# =========================

async def admin_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    user = query.from_user

    if not user or not is_admin(user.username):
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    await query.answer()

    admin_name = (
        f"@{user.username}"
        if user.username
        else str(user.id)
    )

    # =========================
    # СТАТИСТИКА
    # =========================

    if query.data in {
        "admin_stats",
        "admin_refresh"
    }:
        text = (
            "📊 Vega CHAT — статистика\n\n"
            f"🎮 Сыграно игр: {stats['games']}\n"
            f"💬 Сообщений: {stats['messages']}\n"
            f"👥 Пользователей: {len(stats['users'])}\n"
            f"🎮 Игроков: {len(stats['game_users'])}\n"
            f"🤖 Запросов ИИ: {stats['ai_requests']}\n"
            f"💾 Запросов ИИ в SQLite: "
            f"{get_total_profile_requests()}\n"
            f"🌐 Переводов: {get_total_profile_translations()}\n"
            f"💬 Сообщений в SQLite: {get_total_profile_messages()}\n"
            f"🎮 Игр в SQLite: {get_total_profile_games()}\n\n"
            "⚙️ Настройки:\n"
            f"👋 Приветствие: "
            f"{'🟢 ВКЛ' if settings['welcome'] else '🔴 ВЫКЛ'}\n"
            f"🎮 Игры: "
            f"{'🟢 ВКЛ' if settings['games'] else '🔴 ВЫКЛ'}"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="admin_refresh"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin_back"
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # =========================
    # ПРИВЕТСТВИЕ
    # =========================

    elif query.data == "admin_welcome":
        settings["welcome"] = not settings["welcome"]

        state = (
            "🟢 ВКЛ"
            if settings["welcome"]
            else "🔴 ВЫКЛ"
        )

        add_log(
            f"{admin_name} изменил приветствие: {state}"
        )

        await show_admin_panel(
            query,
            "👋 Приветствие: " + state
        )

    # =========================
    # ИГРЫ
    # =========================

    elif query.data == "admin_games":
        settings["games"] = not settings["games"]

        state = (
            "🟢 ВКЛ"
            if settings["games"]
            else "🔴 ВЫКЛ"
        )

        add_log(
            f"{admin_name} изменил игры: {state}"
        )

        await show_admin_panel(
            query,
            "🎮 Игры: " + state
        )

    # =========================
    # ЛОГИ
    # =========================

    elif query.data == "admin_logs":
        if logs:
            last_logs = logs[-20:]
            text = (
                "📜 Последние логи:\n\n"
                + "\n".join(last_logs)
            )
        else:
            text = (
                "📜 Логи\n\n"
                "Пока логов нет."
            )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="admin_logs"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin_back"
                )
            ],
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # =========================
    # ОБНОВЛЕНИЯ
    # =========================

    elif query.data == "admin_updates":
        text = (
            "🆕 ИСТОРИЯ ОБНОВЛЕНИЙ\n\n"

            "📦 Версия 2.1.0\n"
            "• Генерируемые анекдоты через Groq\n"
            "• Защита от повторов анекдотов через SQLite\n"
            "• Расширенный профиль пользователя\n"
            "• /top — рейтинг активности\n"
            "• Счётчики сообщений, игр и переводов\n\n"

            "📦 Версия 2.0.0\n"
            "• Добавлена команда /translate\n"
            "• Перевод выполняется через Groq AI\n"
            "• Добавлена команда /profile\n"
            "• Профиль содержит имя из Telegram\n"
            "• Добавлен персональный счётчик запросов ИИ\n"
            "• Добавлена база данных SQLite\n"
            "• Профили сохраняются между перезапусками\n"
            "• Добавлена игра 😂 Анекдот\n"
            "• Обновлено меню игр\n"
            "• Обновлена статистика\n"
            "• Обновлена справка /help\n\n"

            "📦 Версия 1.3.0\n"
            "• Добавлена команда /unmute\n"
            "• /unmute снимает мут\n"
            "• /unmute сбрасывает все варны\n"
            "• Добавлена команда /call\n"
            "• /call повторяет username 10 раз\n"
            "• Добавлен раздел «Обновления»\n"
            "• Добавлен номер версии бота\n"
            "• Исправлена игра 🪙 Орёл/решка\n"
            "• Исправлена двойная обработка кнопок игр\n\n"

            "📦 Версия 1.2.0\n"
            "• Добавлена админ-панель\n"
            "• Добавлена статистика\n"
            "• Добавлены логи\n"
            "• Добавлено управление играми\n"
            "• Добавлено управление приветствием\n"
            "• Добавлена система варнов\n"
            "• Добавлен мут после 3 варнов\n\n"

            "📦 Версия 1.1.0\n"
            "• Добавлен Groq AI\n"
            "• Добавлена команда ИИ\n"
            "• Добавлены игры\n"
            "• Добавлено приветствие\n"
            "• Добавлена команда /help\n\n"

            "📦 Версия 1.0.0\n"
            "• Первый запуск Vega CHAT\n"
            "• Базовая система Telegram-бота"
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin_back"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # =========================
    # НАЗАД
    # =========================

    elif query.data == "admin_back":
        await show_admin_panel(query)


# =========================
# ПОКАЗ АДМИН-ПАНЕЛИ
# =========================

async def show_admin_panel(
    query,
    notice=None
):
    text = (
        "🛡 Vega CHAT — ADMIN PANEL\n\n"
        "🔐 Доступ разрешён.\n"
        f"📦 Версия: {BOT_VERSION}"
    )

    if notice:
        text += f"\n\n{notice}"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            admin_keyboard()
        )
    )


# =========================
# /warn
# =========================

async def warn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin = update.effective_user

    if not admin or not is_admin(admin.username):
        await update.message.reply_text(
            "⛔ Только администраторы Vega CHAT "
            "могут выдавать варны."
        )
        return

    if not update.effective_chat:
        return

    if not context.args:
        await update.message.reply_text(
            "⚠️ Использование:\n"
            "/warn @username"
        )
        return

    username = (
        context.args[0]
        .lstrip("@")
        .lower()
    )

    user_id = known_users.get(username)

    if not user_id:
        await update.message.reply_text(
            "❌ Пользователь не найден.\n\n"
            "Бот должен был увидеть его сообщение "
            "в группе хотя бы один раз."
        )
        return

    chat_id = update.effective_chat.id
    key = (chat_id, user_id)

    warnings[key] = warnings.get(key, 0) + 1
    count = warnings[key]

    admin_name = (
        f"@{admin.username}"
        if admin.username
        else str(admin.id)
    )

    add_log(
        f"{admin_name} выдал варн "
        f"@{username} ({count}/3)"
    )

    if count >= 3:
        until = (
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        )

        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=False
                ),
                until_date=until
            )

            add_log(
                f"@{username} получил мут "
                f"на 1 час после 3 варнов"
            )

            await update.message.reply_text(
                f"🔇 @{username} получил мут "
                f"на 1 час.\n\n"
                "⚠️ Причина: 3/3 предупреждения."
            )

        except Exception as e:
            print(f"Mute error: {e}")

            await update.message.reply_text(
                "❌ Не удалось выдать мут.\n"
                "Проверь права бота."
            )

        return

    await update.message.reply_text(
        f"⚠️ @{username} получил "
        f"предупреждение.\n\n"
        f"Варны: {count}/3"
    )


# =========================
# /unmute
# =========================

async def unmute_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    admin = update.effective_user

    if not admin or not is_admin(admin.username):
        await update.message.reply_text(
            "⛔ Только администраторы могут "
            "использовать /unmute."
        )
        return

    if not update.effective_chat:
        return

    if not context.args:
        await update.message.reply_text(
            "🔓 Использование:\n"
            "/unmute @username"
        )
        return

    username = (
        context.args[0]
        .lstrip("@")
        .lower()
    )

    user_id = known_users.get(username)

    if not user_id:
        await update.message.reply_text(
            "❌ Я не знаю этого пользователя.\n\n"
            "Пусть он сначала напишет "
            "сообщение в группе."
        )
        return

    chat_id = update.effective_chat.id
    key = (chat_id, user_id)

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
            )
        )

        warnings.pop(key, None)

        add_log(
            f"@{admin.username} снял мут "
            f"с @{username} и сбросил варны"
        )

        await update.message.reply_text(
            f"🔓 Мут с @{username} снят!\n"
            f"⚠️ Варны сброшены: 0/3"
        )

    except Exception as e:
        print(f"UNMUTE ERROR: {e}")

        await update.message.reply_text(
            "❌ Не удалось снять мут.\n\n"
            "Проверь, что Vega CHAT является "
            "администратором группы и имеет "
            "право блокировать пользователей."
        )


# =========================
# /call
# =========================

async def call_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "📢 Использование:\n"
            "/call @username"
        )
        return

    username = context.args[0].strip()

    if not username.startswith("@"):
        username = "@" + username

    text = " ".join([username] * 10)

    await update.message.reply_text(text)


# =========================
# ПРИВЕТСТВИЕ
# =========================

async def welcome_new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not settings["welcome"]:
        return

    result = update.chat_member

    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    if (
        old_status in {"left", "kicked"}
        and
        new_status in {"member", "administrator"}
    ):
        user = result.new_chat_member.user

        remember_user(user)

        name = user.first_name or "новый участник"

        await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {name}!\n\n"
            "🤖 Ты находишься в группе "
            "с Vega CHAT.\n"
            "🎮 Напиши «ИГРА», чтобы поиграть.\n"
            "🧠 Напиши «ИИ <текст>», "
            "чтобы задать вопрос.\n"
            "🌐 /translate — переводчик\n"
            "👤 /profile — твой профиль"
        )


# =========================
# ОШИБКИ
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    print(f"Telegram error: {context.error}")


# =========================
# RENDER WEB SERVICE / LIVE STATUS
# =========================

health_app = Flask(__name__)

@health_app.get("/")
def health_root():
    return jsonify({"service": BOT_NAME, "version": BOT_VERSION, "status": "online", "miniapp": "/miniapp"}), 200


@health_app.get("/miniapp")
def miniapp_page():
    from flask import Response
    page = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover"><meta name="theme-color" content="#111126"><title>Vega CHAT — Mini App</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark;--bg:var(--tg-theme-bg-color,#101020);--card:var(--tg-theme-secondary-bg-color,#19192d);--fg:var(--tg-theme-text-color,#f6f5ff);--muted:var(--tg-theme-hint-color,#aaa9c4);--accent:var(--tg-theme-button-color,#8b5cf6);--accentText:var(--tg-theme-button-text-color,#fff);--line:rgba(180,170,255,.15);--surface:rgba(255,255,255,.045);--good:#65e6b2}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 10% -5%,rgba(115,70,220,.25),transparent 38%),radial-gradient(ellipse at 100% 30%,rgba(28,170,202,.12),transparent 32%),var(--bg);color:var(--fg);font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;padding:calc(14px + env(safe-area-inset-top)) 15px calc(28px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}
.wrap{max-width:680px;margin:0 auto}.hero{position:relative;overflow:hidden;padding:22px 20px 20px;border:1px solid rgba(255,255,255,.13);border-radius:27px;background:linear-gradient(135deg,rgba(99,61,184,.94),rgba(51,47,118,.96) 54%,rgba(20,115,145,.92));box-shadow:0 18px 48px rgba(0,0,0,.22);margin-bottom:17px}.hero:after{content:"";position:absolute;width:190px;height:190px;right:-65px;top:-90px;border-radius:50%;border:1px solid rgba(255,255,255,.17);box-shadow:0 0 0 22px rgba(255,255,255,.035),0 0 0 45px rgba(255,255,255,.025)}.hero-top{display:flex;align-items:center;gap:13px;position:relative;z-index:1}.brand-icon{display:grid;place-items:center;width:57px;height:57px;border-radius:19px;background:rgba(255,255,255,.17);border:1px solid rgba(255,255,255,.25);font-size:30px}.eyebrow{font-size:11px;font-weight:800;letter-spacing:1.7px;text-transform:uppercase;color:#ded8ff}.hero h1{font-size:27px;letter-spacing:-.8px;line-height:1.08;margin:4px 0 0}.hero p{position:relative;z-index:1;color:#e6e4ff;font-size:13px;line-height:1.55;margin:16px 0 17px;max-width:430px}.hero-bottom{display:flex;gap:8px;align-items:center;flex-wrap:wrap;position:relative;z-index:1}.status{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border-radius:999px;background:rgba(7,13,32,.2);border:1px solid rgba(255,255,255,.16);font-size:11px;font-weight:700;color:#fff}.dot{width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 12px rgba(101,230,178,.65)}.tag{font-size:11px;padding:7px 10px;border-radius:999px;background:rgba(255,255,255,.11);color:#f2efff}
.tabs{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:5px;border-radius:16px;background:var(--surface);border:1px solid var(--line);margin:0 0 17px}.tab{appearance:none;border:0;border-radius:12px;padding:12px 8px;background:transparent;color:var(--muted);font-size:13px;font-weight:800;transition:background .18s,color .18s,transform .18s}.tab.active{color:var(--accentText);background:linear-gradient(135deg,var(--accent),#6e55dc);box-shadow:0 5px 18px rgba(108,78,220,.22)}.tab:active,.action:active,.copy:active{transform:scale(.98)}.panel{display:none;animation:fade .22s ease}.panel.active{display:block}@keyframes fade{from{opacity:.25;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.section-head{display:flex;justify-content:space-between;align-items:end;gap:12px;margin:22px 2px 11px}.section-head h2{font-size:17px;letter-spacing:-.35px;margin:0}.section-head span{font-size:11px;color:var(--muted)}.intro{font-size:13px;color:var(--muted);line-height:1.55;margin:0 2px 15px}.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin-bottom:18px}.stat{padding:13px 10px;border-radius:17px;border:1px solid var(--line);background:var(--surface);min-width:0}.stat-icon{font-size:18px;margin-bottom:7px}.stat strong{display:block;font-size:13px;margin-bottom:3px}.stat small{font-size:10px;line-height:1.35;color:var(--muted);display:block}
.features{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.feature{min-height:137px;padding:15px;border-radius:19px;border:1px solid var(--line);background:linear-gradient(145deg,var(--surface),rgba(255,255,255,.018));position:relative;overflow:hidden}.feature-icon{display:grid;place-items:center;width:38px;height:38px;border-radius:13px;background:rgba(139,92,246,.16);font-size:20px;margin-bottom:13px}.feature h3{font-size:13px;margin:0 0 6px}.feature p{font-size:11px;line-height:1.5;color:var(--muted);margin:0}.card{padding:16px;border-radius:19px;border:1px solid var(--line);background:var(--card);margin:0 0 11px;box-shadow:0 8px 22px rgba(0,0,0,.06)}.card-head{display:flex;align-items:center;gap:10px;margin-bottom:10px}.card-icon{display:grid;place-items:center;width:35px;height:35px;border-radius:12px;background:rgba(139,92,246,.15);font-size:18px;flex-shrink:0}.card h3{font-size:14px;margin:0}.card p{font-size:12px;line-height:1.65;color:var(--muted);margin:0}.pills{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.pill{font-size:10px;font-weight:700;color:var(--fg);padding:7px 9px;border-radius:9px;background:rgba(139,92,246,.13);border:1px solid rgba(139,92,246,.18)}
.search{display:flex;align-items:center;gap:9px;padding:0 13px;border:1px solid var(--line);background:var(--surface);border-radius:14px;margin-bottom:11px}.search span{font-size:16px;color:var(--muted)}.search input{width:100%;min-width:0;border:0;outline:0;background:transparent;color:var(--fg);font:inherit;font-size:13px;padding:13px 0}.search input::placeholder{color:var(--muted)}.command{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--line)}.command:last-child{border-bottom:0;padding-bottom:0}.cmd-text{flex:1;min-width:0}.cmd-text code{display:inline-block;color:#c9baff;background:rgba(139,92,246,.13);border:1px solid rgba(139,92,246,.15);padding:4px 7px;border-radius:7px;font-size:11px;overflow-wrap:anywhere}.cmd-text p{font-size:11px;color:var(--muted);margin:5px 0 0;line-height:1.45}.copy{flex-shrink:0;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--fg);font-size:11px;font-weight:700;padding:9px 10px}.action{width:100%;display:flex;justify-content:center;align-items:center;gap:8px;border:0;border-radius:14px;background:linear-gradient(135deg,var(--accent),#6556d9);color:var(--accentText);font-size:13px;font-weight:800;padding:14px;text-decoration:none;margin-top:12px}.note{border-radius:14px;padding:13px;background:rgba(45,190,145,.08);border:1px solid rgba(45,190,145,.18);font-size:11px;line-height:1.55;color:var(--muted)}.note strong{color:var(--fg)}.empty{display:none;text-align:center;color:var(--muted);font-size:12px;padding:18px}.footer{text-align:center;padding:22px 8px 4px;color:var(--muted);font-size:10px;line-height:1.8}.footer b{color:var(--fg);font-weight:800}.hidden{display:none!important}
@media(min-width:520px){body{padding-left:22px;padding-right:22px}.hero{padding:27px}.hero h1{font-size:30px}.feature{min-height:145px}.features{gap:12px}.stats{gap:12px}}@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style></head><body><main class="wrap">
<header class="hero"><div class="hero-top"><div class="brand-icon">🤖</div><div><div class="eyebrow">Your Telegram assistant</div><h1>Vega CHAT</h1></div></div><p>Умный помощник для общения, быстрых ответов и управления группой — прямо в Telegram.</p><div class="hero-bottom"><span class="status"><i class="dot"></i> Mini App готов</span><span class="tag">✦ Powered by Groq</span><span class="tag">v{{VERSION}}</span></div></header>
<nav class="tabs" aria-label="Разделы"><button class="tab active" data-tab="info">✦ Обзор</button><button class="tab" data-tab="guide">⌘ Команды</button></nav>
<section id="info" class="panel active"><p class="intro">Всё самое важное о боте — коротко и в одном месте.</p>
<div class="stats"><div class="stat"><div class="stat-icon">🧠</div><strong>ИИ-чат</strong><small>Ответы на вопросы и помощь</small></div><div class="stat"><div class="stat-icon">🎮</div><strong>Игры</strong><small>Мини-игры в сообщениях</small></div><div class="stat"><div class="stat-icon">🛡️</div><strong>Группы</strong><small>Инструменты модерации</small></div></div>
<div class="section-head"><h2>Возможности</h2><span>Что умеет Vega</span></div><div class="features"><article class="feature"><div class="feature-icon">✨</div><h3>ИИ-помощник</h3><p>Идеи, объяснения, тексты и помощь с кодом по запросу «ИИ …».</p></article><article class="feature"><div class="feature-icon">🎲</div><h3>Игровое меню</h3><p>Кубик, футбол, баскетбол, дартс, казино и орёл/решка.</p></article><article class="feature"><div class="feature-icon">🌐</div><h3>Переводчик</h3><p>Переводи текст на нужный язык одной командой.</p></article><article class="feature"><div class="feature-icon">🏆</div><h3>Профиль и топ</h3><p>Статистика активности и рейтинги за разные периоды.</p></article><article class="feature"><div class="feature-icon">📊</div><h3>Опросы</h3><p>Создавай опросы прямо из чата.</p></article><article class="feature"><div class="feature-icon">🛡️</div><h3>Модерация</h3><p>Предупреждения и управление участниками для админов.</p></article></div>
<div class="section-head"><h2>Быстрый старт</h2><span>Попробуй сейчас</span></div><div class="card"><div class="card-head"><div class="card-icon">💬</div><h3>Задай вопрос ИИ</h3></div><p>В чате или группе отправь сообщение, которое начинается с «ИИ», затем напиши свой запрос.</p><div class="pills"><span class="pill">ИИ придумай название</span><span class="pill">ИИ объясни Python</span></div><button class="action" data-switch="guide">Посмотреть команды <span>→</span></button></div>
<div class="card"><div class="card-head"><div class="card-icon">👑</div><h3>Создатель</h3></div><p>Vega CHAT создан для удобного общения в Telegram.</p><div class="pills"><span class="pill">@Qnwru</span><span class="pill">Vega CHAT {{VERSION}}</span></div></div></section>
<section id="guide" class="panel"><p class="intro">Найди команду и нажми «Копировать», чтобы быстро вставить её в чат.</p><label class="search"><span>⌕</span><input id="commandSearch" type="search" placeholder="Поиск команд…" autocomplete="off" aria-label="Поиск команд"></label>
<div class="card command-group"><div class="card-head"><div class="card-icon">🧠</div><h3>ИИ и инструменты</h3></div>
<div class="command" data-search="ии вопрос ответ помощник"><div class="cmd-text"><code>ИИ твой вопрос</code><p>Задать вопрос ИИ-помощнику.</p></div><button class="copy" data-copy="ИИ ">Копировать</button></div>
<div class="command" data-search="reset сброс память диалог"><div class="cmd-text"><code>/reset</code><p>Сбросить историю диалога с ИИ.</p></div><button class="copy" data-copy="/reset">Копировать</button></div>
<div class="command" data-search="translate перевод язык"><div class="cmd-text"><code>/translate en Привет</code><p>Перевести текст; замени en на код языка.</p></div><button class="copy" data-copy="/translate en Привет">Копировать</button></div>
<div class="command" data-search="joke анекдот шутка"><div class="cmd-text"><code>/joke</code><p>Открыть категории анекдотов.</p></div><button class="copy" data-copy="/joke">Копировать</button></div></div>
<div class="card command-group"><div class="card-head"><div class="card-icon">📈</div><h3>Профиль и активность</h3></div>
<div class="command" data-search="help помощь"><div class="cmd-text"><code>/help</code><p>Показать меню помощи.</p></div><button class="copy" data-copy="/help">Копировать</button></div>
<div class="command" data-search="profile профиль статистика"><div class="cmd-text"><code>/profile</code><p>Посмотреть профиль и статистику.</p></div><button class="copy" data-copy="/profile">Копировать</button></div>
<div class="command" data-search="top рейтинг"><div class="cmd-text"><code>/top</code><p>Общий рейтинг активности.</p></div><button class="copy" data-copy="/top">Копировать</button></div>
<div class="command" data-search="topday рейтинг день"><div class="cmd-text"><code>/topday</code><p>Рейтинг за сутки.</p></div><button class="copy" data-copy="/topday">Копировать</button></div>
<div class="command" data-search="topweek рейтинг неделя"><div class="cmd-text"><code>/topweek</code><p>Рейтинг за неделю.</p></div><button class="copy" data-copy="/topweek">Копировать</button></div>
<div class="command" data-search="status состояние бот"><div class="cmd-text"><code>/status</code><p>Информация о состоянии бота.</p></div><button class="copy" data-copy="/status">Копировать</button></div></div>
<div class="card command-group"><div class="card-head"><div class="card-icon">👥</div><h3>Группы и администрирование</h3></div>
<div class="command" data-search="игра games кубик футбол"><div class="cmd-text"><code>ИГРА</code><p>Открыть игровое меню.</p></div><button class="copy" data-copy="ИГРА">Копировать</button></div>
<div class="command" data-search="poll опрос вопрос варианты"><div class="cmd-text"><code>/poll Вопрос | Да | Нет</code><p>Создать опрос с вариантами ответа.</p></div><button class="copy" data-copy="/poll Вопрос | Да | Нет">Копировать</button></div>
<div class="command" data-search="warn предупреждение админ"><div class="cmd-text"><code>/warn @username</code><p>Выдать предупреждение участнику (для админов).</p></div><button class="copy" data-copy="/warn @username">Копировать</button></div>
<div class="command" data-search="unmute снять мут"><div class="cmd-text"><code>/unmute @username</code><p>Снять ограничение с участника.</p></div><button class="copy" data-copy="/unmute @username">Копировать</button></div>
<div class="command" data-search="call вызвать участника"><div class="cmd-text"><code>/call @username</code><p>Упомянуть участника в чате.</p></div><button class="copy" data-copy="/call @username">Копировать</button></div>
<div class="command" data-search="adminpanel админ панель"><div class="cmd-text"><code>/adminPANEL</code><p>Открыть панель администратора.</p></div><button class="copy" data-copy="/adminPANEL">Копировать</button></div></div>
<div class="empty" id="emptySearch">Ничего не найдено. Попробуй другое слово.</div><div class="note"><strong>Важно:</strong> для команд модерации Vega CHAT должен иметь нужные права администратора в группе. Некоторые команды доступны только администраторам.</div></section>
<footer class="footer"><b>VEGA CHAT</b> · {{VERSION}}<br>Сделано для Telegram · Powered by Groq</footer></main>
<script>
(function(){const tg=window.Telegram&&window.Telegram.WebApp;if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('bg_color')}catch(e){}}
const tabs=[...document.querySelectorAll('.tab')],panels=[...document.querySelectorAll('.panel')];function showTab(id){tabs.forEach(b=>b.classList.toggle('active',b.dataset.tab===id));panels.forEach(p=>p.classList.toggle('active',p.id===id));if(tg&&tg.HapticFeedback){try{tg.HapticFeedback.selectionChanged()}catch(e){}}window.scrollTo({top:0,behavior:'smooth'})}tabs.forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));document.querySelectorAll('[data-switch]').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.switch)));
const search=document.getElementById('commandSearch'),empty=document.getElementById('emptySearch');if(search){search.addEventListener('input',()=>{const q=search.value.toLowerCase().trim();let count=0;document.querySelectorAll('.command').forEach(row=>{const match=!q||(row.innerText+' '+(row.dataset.search||'')).toLowerCase().includes(q);row.classList.toggle('hidden',!match);if(match)count++});document.querySelectorAll('.command-group').forEach(group=>group.classList.toggle('hidden',!group.querySelector('.command:not(.hidden)')));empty.style.display=count?'none':'block'})}
document.querySelectorAll('[data-copy]').forEach(btn=>btn.addEventListener('click',async()=>{const value=btn.dataset.copy;let ok=false;try{if(tg&&tg.isVersionAtLeast&&tg.isVersionAtLeast('6.4')&&tg.writeText){await tg.writeText(value);ok=true}else if(navigator.clipboard&&navigator.clipboard.writeText){await navigator.clipboard.writeText(value);ok=true}}catch(e){}const old=btn.textContent;btn.textContent=ok?'Готово ✓':'Не скопировано';if(tg&&tg.HapticFeedback){try{tg.HapticFeedback.notificationOccurred(ok?'success':'warning')}catch(e){}}setTimeout(()=>btn.textContent=old,1400)}))})();
</script></body></html>
'''
    page = page.replace("{{VERSION}}", BOT_VERSION)
    return Response(page, mimetype="text/html")

def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    thread = threading.Thread(
        target=lambda: health_app.run(host="0.0.0.0", port=port, use_reloader=False),
        daemon=True,
    )
    thread.start()


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")
    init_db()
    start_health_server()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # =========================
    # СООБЩЕНИЯ
    # =========================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    # =========================
    # КОМАНДЫ
    # =========================

    app.add_handler(
        CommandHandler("help", help_command)
    )
    app.add_handler(CommandHandler("app", app_command))

    app.add_handler(
        CommandHandler("profile", profile_command)
    )

    app.add_handler(
        CommandHandler("translate", translate_command)
    )

    app.add_handler(CommandHandler("joke", joke_command))
    app.add_handler(CallbackQueryHandler(joke_category_callback, pattern=r"^joke_cat_"))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("topday", top_day_command))
    app.add_handler(CommandHandler("topweek", top_week_command))
    app.add_handler(CommandHandler("reset", reset_memory_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("poll", poll_command))
    app.add_handler(
        CommandHandler("adminPANEL", admin_panel)
    )

    app.add_handler(
        CommandHandler("warn", warn_command)
    )

    app.add_handler(
        CommandHandler("unmute", unmute_command)
    )

    app.add_handler(
        CommandHandler("call", call_command)
    )

    # =========================
    # ИГРЫ
    # =========================

    app.add_handler(
        CallbackQueryHandler(
            game_button,
            pattern=r"^game_"
        )
    )

    # =========================
    # HELP
    # =========================

    app.add_handler(
        CallbackQueryHandler(
            help_buttons,
            pattern=r"^help_(?!back$)"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            help_back,
            pattern=r"^help_back$"
        )
    )

    # =========================
    # ADMIN
    # =========================

    app.add_handler(
        CallbackQueryHandler(
            admin_buttons,
            pattern=r"^admin_"
        )
    )

    # =========================
    # НОВЫЕ УЧАСТНИКИ
    # =========================

    app.add_handler(
        ChatMemberHandler(
            welcome_new_member,
            ChatMemberHandler.CHAT_MEMBER
        )
    )

    # =========================
    # ОШИБКИ
    # =========================

    app.add_error_handler(error_handler)

    print(
        f"🤖 {BOT_NAME} "
        f"v{BOT_VERSION} запущен!"
    )

    app.run_polling()


# =========================
# ЗАПУСК
# =========================

if __name__ == "__main__":
    main()
