from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ChatPermissions,
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

from config import BOT_TOKEN, GROQ_API_KEY


# =========================
# ИНФОРМАЦИЯ О БОТЕ
# =========================

BOT_NAME = "Vega CHAT"
BOT_VERSION = "2.1.0"


# =========================
# GROQ
# =========================

client = AsyncGroq(api_key=GROQ_API_KEY)
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

DB_FILE = "vega.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
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
        conn.commit()
    finally:
        conn.close()


def save_user(user):
    if not user:
        return

    first_name = user.first_name or "Без имени"
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(ai_requests), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_translations():
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(translations), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_messages():
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(messages), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_total_profile_games():
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(games), 0) FROM profiles"
        ).fetchone()
        return int(row[0] or 0)
    finally:
        conn.close()


def get_joke_history(limit=30):
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = conn.execute(
            "SELECT joke FROM jokes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()


def save_joke(joke):
    joke = joke.strip()
    if not joke:
        return False
    conn = sqlite3.connect(DB_FILE)
    try:
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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

    if user.username:
        known_users[user.username.lower()] = user.id


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
        stats["translations"] += 1

    except Exception as e:
        print(f"Translate error: {e}")

        await update.message.reply_text(
            "❌ Произошла ошибка при переводе."
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
    ]

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
            "/top"
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
            "Команда: /top\n\n"
            "Топ считается по общей активности: сообщения, ИИ, "
            "переводы и игры."
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
            "💾 SQLite\n"
            "🏆 Топ активности\n\n"
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
    ]

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

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты полезный Telegram-ассистент. "
                        "Отвечай понятно, дружелюбно "
                        "и по существу. "
                        "Отвечай на языке пользователя."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.7,
            max_tokens=2000,
        )

        answer = response.choices[0].message.content

        if not answer:
            answer = "❌ ИИ не вернул ответ."

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

async def generate_joke():
    history = get_joke_history(30)
    history_text = "\n---\n".join(history)

    prompt = (
        "Придумай совершенно новый короткий смешной анекдот на русском языке. "
        "Не используй копипасту и не повторяй известные шаблоны. "
        "Не добавляй заголовок, пояснения и кавычки. "
        "Верни только текст анекдота."
    )
    if history_text:
        prompt += (
            "\n\nВот последние анекдоты, которые уже выдавались. "
            "Ни один из них нельзя повторять или пересказывать близко по смыслу:\n"
            + history_text
        )

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Ты генератор оригинальных коротких анекдотов. "
                    "Создавай каждый раз новый вариант."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=1.0,
        max_tokens=500,
    )
    answer = (response.choices[0].message.content or "").strip()
    return answer


async def send_generated_joke(chat_id, bot, user=None):
    try:
        await bot.send_chat_action(chat_id=chat_id, action="typing")

        for _ in range(3):
            joke = await generate_joke()
            if joke and save_joke(joke):
                await bot.send_message(chat_id=chat_id, text=f"😂 {joke}")
                return

        await bot.send_message(
            chat_id=chat_id,
            text="❌ Не получилось придумать новый анекдот. Попробуй ещё раз."
        )
    except Exception as e:
        print(f"Joke generation error: {e}")
        await bot.send_message(
            chat_id=chat_id,
            text="❌ Ошибка при генерации анекдота."
        )


async def joke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text("😂 Придумываю анекдот...")
    await send_generated_joke(
        update.message.chat.id,
        context.bot,
        update.effective_user,
    )


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not update.message or not user:
        return
    remember_user(user)
    await update.message.reply_text(format_top(user))


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
        await query.edit_message_text("😂 Придумываю анекдот...")
        await send_generated_joke(
            query.message.chat.id,
            context.bot,
            query.from_user,
        )
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
# MAIN
# =========================

def main():
    init_db()

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

    app.add_handler(
        CommandHandler("profile", profile_command)
    )

    app.add_handler(
        CommandHandler("translate", translate_command)
    )

    app.add_handler(
        CommandHandler("joke", joke_command)
    )

    app.add_handler(
        CommandHandler("top", top_command)
    )

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
