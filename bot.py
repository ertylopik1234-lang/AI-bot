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
BOT_VERSION = "2.2.3"


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


async def generate_joke(category="general"):
    history = get_joke_history(40)
    history_text = "\n---\n".join(history)
    category_description = JOKE_CATEGORIES.get(category, JOKE_CATEGORIES["general"])
    prompt = (
        "Придумай оригинальный короткий смешной анекдот на русском языке. "
        f"Категория: {category_description}. "
        "Он должен иметь понятную завязку и неожиданную концовку. "
        "Не копируй известные анекдоты. Верни только текст анекдота, без заголовка."
    )
    if history_text:
        prompt += (
            "\n\nЭти анекдоты уже выдавались. Не повторяй их дословно "
            "и не пересказывай тот же сюжет:\n" + history_text
        )

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "Ты комедийный автор. Каждый ответ должен быть новым анекдотом, а не отказом или пояснением."},
            {"role": "user", "content": prompt},
        ],
        temperature=1.15,
        max_tokens=350,
        timeout=25.0,
    )
    answer = (response.choices[0].message.content or "").strip()
    # Убираем возможные кавычки/markdown от модели.
    answer = answer.strip('\"“”`* ') 
    if len(answer) < 15:
        raise ValueError("Groq вернул слишком короткий ответ")
    return answer


async def send_generated_joke(chat_id, bot, user=None, category="general"):
    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing")

        for _ in range(6):
            joke = await generate_joke(category)
            if joke and save_joke(joke):
                if user:
                    record_activity(user, points=2)
                await bot.send_message(chat_id=chat_id, text=f"😂 {joke}")
                return

        await bot.send_message(
            chat_id=chat_id,
            text="❌ Groq пока не смог придумать новый анекдот. Попробуй ещё раз через минуту."
        )
    except Exception as e:
        print(f"Joke generation error: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Не удалось получить анекдот от ИИ. Проверь GROQ_API_KEY и модель в Render Logs."
        )


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
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<meta name="theme-color" content="#101827"><title>Vega CHAT</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark;--bg:var(--tg-theme-bg-color,#101827);--card:var(--tg-theme-secondary-bg-color,#1b2638);--fg:var(--tg-theme-text-color,#f4f7fb);--muted:var(--tg-theme-hint-color,#9aa9bf);--accent:var(--tg-theme-button-color,#6c63ff);--line:rgba(160,180,210,.16)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,Segoe UI,sans-serif;padding:18px 16px 30px}.hero{padding:22px 18px;border-radius:22px;background:linear-gradient(135deg,#252c60,#5639a9 70%,#236b91);margin-bottom:18px}.logo{font-size:32px}.hero h1{font-size:25px;margin:7px 0}.hero p{margin:0;color:#e0e5ff;line-height:1.45}.tabs{display:flex;gap:8px;margin:0 0 16px}.tab{flex:1;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--fg);padding:12px 8px;font-size:14px;font-weight:700}.tab.active{background:var(--accent);border-color:transparent;color:var(--tg-theme-button-text-color,#fff)}.panel{display:none}.panel.active{display:block}.card{background:var(--card);border:1px solid var(--line);padding:15px;border-radius:16px;margin:0 0 11px}.card h2{font-size:16px;margin:0 0 8px}.card p,.card li{font-size:14px;line-height:1.55;color:var(--muted)}.card p{margin:0}.card ul{padding-left:20px;margin:8px 0 0}.pill{display:inline-block;background:rgba(108,99,255,.17);color:var(--fg);border-radius:8px;padding:5px 8px;font-size:12px;margin:3px 3px 0 0}code{color:var(--fg);background:rgba(125,145,180,.15);padding:2px 5px;border-radius:5px;overflow-wrap:anywhere}.footer{text-align:center;color:var(--muted);font-size:12px;padding:12px}
</style></head><body>
<div class="hero"><div class="logo">🤖</div><h1>Vega CHAT</h1><p>ИИ-помощник, игры и инструменты для Telegram-групп — всё в одном боте.</p></div>
<div class="tabs"><button class="tab active" data-tab="info">ℹ️ Информация</button><button class="tab" data-tab="guide">📖 Инструкция</button></div>
<section id="info" class="panel active">
<div class="card"><h2>✨ О боте</h2><p>Vega CHAT помогает отвечать на вопросы с помощью ИИ, играть, переводить сообщения и поддерживать порядок в группах.</p><div><span class="pill">🤖 Groq AI</span><span class="pill">🎮 Игры</span><span class="pill">🛡️ Модерация</span><span class="pill">📊 Опросы</span></div></div>
<div class="card"><h2>🧩 Возможности</h2><ul><li>ИИ-ответы и контекст диалога</li><li>Анекдоты по категориям</li><li>Игры Telegram: кубик, футбол, баскетбол, дартс, казино и монетка</li><li>Профиль и рейтинги активности</li><li>Опросы, перевод и инструменты управления группой</li></ul></div>
<div class="card"><h2>📦 Версия</h2><p>Vega CHAT {{VERSION}} · Powered by Groq</p><p style="margin-top:6px">Создатель: @Qnwru</p></div>
</section>
<section id="guide" class="panel">
<div class="card"><h2>🤖 ИИ</h2><p>Начни сообщение с <code>ИИ</code>, затем напиши вопрос.</p><p style="margin-top:8px"><code>ИИ придумай название</code><br><code>ИИ помоги с кодом</code><br><code>/reset</code> — сбросить память диалога.</p></div>
<div class="card"><h2>🎮 Игры и анекдоты</h2><p>Напиши <code>ИГРА</code>, чтобы открыть игровое меню. Команда <code>/joke</code> открывает категории анекдотов.</p></div>
<div class="card"><h2>📊 Команды</h2><p><code>/help</code> — помощь<br><code>/app</code> — открыть это мини-приложение<br><code>/profile</code> — профиль<br><code>/translate en Привет</code> — пример перевода<br><code>/poll Вопрос | Да | Нет</code> — создать опрос<br><code>/top</code> — рейтинг<br><code>/topday</code> — рейтинг за сутки<br><code>/topweek</code> — рейтинг за неделю<br><code>/status</code> — состояние бота.</p></div>
<div class="card"><h2>👥 Команды для групп</h2><p><code>/warn @username</code> — предупреждение (для админов)<br><code>/unmute @username</code> — снять мут<br><code>/call @username</code> — вызвать участника<br><code>/adminPANEL</code> — панель администратора.</p><p style="margin-top:8px">Для работы модерации у бота должны быть необходимые права администратора в группе.</p></div>
</section><div class="footer">Vega CHAT · {{VERSION}}</div>
<script>
const tg=window.Telegram&&window.Telegram.WebApp;if(tg){tg.ready();tg.expand();}
document.querySelectorAll('.tab').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b===btn));document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id===btn.dataset.tab));}));
</script></body></html>'''
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
