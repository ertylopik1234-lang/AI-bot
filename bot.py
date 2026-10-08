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
BOT_VERSION = "2.0.0"


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
                ai_requests INTEGER NOT NULL DEFAULT 0
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

    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            """
            INSERT INTO profiles (user_id, first_name, ai_requests)
            VALUES (?, ?, 0)
            ON CONFLICT(user_id) DO UPDATE SET first_name=excluded.first_name
            """,
            (user.id, first_name),
        )
        conn.commit()
    finally:
        conn.close()


def increment_ai_requests(user):
    if not user:
        return

    save_user(user)

    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(
            """
            UPDATE profiles
            SET ai_requests = ai_requests + 1,
                first_name = ?
            WHERE user_id = ?
            """,
            (user.first_name or "Без имени", user.id),
        )
        conn.commit()
    finally:
        conn.close()


def get_profile(user):
    if not user:
        return "❌ Не удалось получить профиль."

    save_user(user)

    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            """
            SELECT first_name, ai_requests
            FROM profiles
            WHERE user_id = ?
            """,
            (user.id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return "❌ Профиль не найден."

    first_name, ai_requests = row

    return (
        "👤 Профиль\n\n"
        f"📝 Имя: {first_name}\n"
        f"🧠 Запросов ИИ: {ai_requests}"
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
            "/profile"
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

    elif query.data == "help_info":
        text = (
            "ℹ️ Vega CHAT\n\n"
            "🤖 Telegram AI-бот\n"
            "🧠 Powered by Groq\n"
            "🎮 Игры\n"
            "🛡 Модерация\n"
            "🌐 Переводчик\n"
            "👤 Профили\n"
            "💾 SQLite\n\n"
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
    ]

    await query.edit_message_text(
        "📚 Vega CHAT — помощь\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# АНЕКДОТЫ
# =========================

JOKES = [
    "😂 Программист пришёл в магазин и спросил: «У вас есть хлеб?» Продавец: «Да». Программист: «Отлично, тогда дайте два, если есть два».",
    "😂 — Почему программист любит тёмную тему? — Потому что светлая притягивает баги.",
    "😂 — Доктор, у меня раздвоение личности! — Отлично, сегодня вас двое, а платит кто-нибудь один?",
    "😂 — Что сказал ноль восьмёрке? — Хороший ремень!",
    "😂 — Почему компьютер замёрз? — Потому что оставили Windows открытым.",
    "😂 Учитель: «Почему ты опоздал?» Ученик: «На дороге была надпись “Школа — 20 км/ч”, вот я и ехал медленно».",
]


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
        result = random.choice(JOKES)

        stats["games"] += 1

        if query.from_user:
            stats["game_users"].add(query.from_user.id)

        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=result
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
            f"{get_total_profile_requests()}\n\n"
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
