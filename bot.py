from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

from config import BOT_TOKEN, GROQ_API_KEY


# =========================
# НАСТРОЙКИ
# =========================

client = AsyncGroq(api_key=GROQ_API_KEY)

MODEL = "openai/gpt-oss-20b"

ADMINS = {
    "DKLART",
    "Qnwru",
}


# =========================
# СТАТИСТИКА
# =========================

stats = {
    "games": 0,
    "messages": 0,
    "users": set(),
    "game_users": set(),
}


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
# КНОПКИ HELP
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
            "ИИ придумай название для канала\n\n"
            "ИИ расскажи анекдот\n\n"
            "ИИ помоги с кодом"
        )

    elif query.data == "help_games":
        text = (
            "🎮 Игры\n\n"
            "Напиши:\n"
            "ИГРА\n\n"
            "Доступно:\n"
            "🎲 Кубик\n"
            "⚽ Футбол\n"
            "🏀 Баскетбол\n"
            "🎯 Дартс\n"
            "🎰 Казино\n"
            "🪙 Орёл/решка"
        )

    elif query.data == "help_group":
        text = (
            "👥 Группа\n\n"
            "👋 Vega CHAT приветствует новых участников.\n\n"
            "Для администраторов:\n"
            "/adminPANEL"
        )

    elif query.data == "help_info":
        text = (
            "ℹ️ Vega CHAT\n\n"
            "🤖 Telegram AI-бот\n"
            "🧠 Powered by Groq\n"
            "🎮 Игры\n"
            "👥 Функции для групп\n\n"
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
# ОСНОВНЫЕ СООБЩЕНИЯ
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    text_lower = text.lower()

    # Статистика сообщений
    stats["messages"] += 1

    if update.effective_user:
        stats["users"].add(update.effective_user.id)

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
        await update.message.chat.send_action("typing")

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты полезный Telegram-ассистент. "
                        "Отвечай понятно, дружелюбно и по существу. "
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
            "❌ Произошла ошибка при обращении к ИИ."
        )


# =========================
# ИГРЫ
# =========================

async def game_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    games = {
        "game_dice": "🎲",
        "game_football": "⚽",
        "game_basketball": "🏀",
        "game_darts": "🎯",
        "game_casino": "🎰",
        "game_coin": "🪙",
    }

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
# /adminPANEL
# =========================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not user or not is_admin(user.username):
        await update.message.reply_text(
            "⛔ У тебя нет доступа к админ-панели."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="admin_stats"
            )
        ],
    ]

    await update.message.reply_text(
        "🛡 Vega CHAT — ADMIN PANEL\n\n"
        "🔐 Доступ разрешён.\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================
# СТАТИСТИКА АДМИНКИ
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

    if query.data == "admin_stats":

        text = (
            "📊 Vega CHAT — статистика\n\n"
            f"🎮 Сыграно игр: {stats['games']}\n"
            f"💬 Сообщений: {stats['messages']}\n"
            f"👥 Активных пользователей: "
            f"{len(stats['users'])}\n"
            f"🎮 Игроков: {len(stats['game_users'])}\n\n"
            "📌 Статистика с момента запуска бота."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="admin_stats"
                )
            ]
        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# =========================
# ПРИВЕТСТВИЕ
# =========================

async def welcome_new_member(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    result = update.chat_member

    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    # Пользователь действительно вошёл
    if (
        old_status in {"left", "kicked"}
        and new_status in {"member", "administrator"}
    ):
        user = result.new_chat_member.user

        name = user.first_name or "новый участник"

        await update.effective_chat.send_message(
            f"👋 Добро пожаловать, {name}!\n\n"
            "🤖 Ты находишься в группе с Vega CHAT.\n"
            "🎮 Напиши «ИГРА», чтобы поиграть.\n"
            "🧠 Напиши «ИИ <текст>», чтобы задать вопрос."
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
# ЗАПУСК
# =========================

def main():

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Обычные сообщения
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    # /help
    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    # /adminPANEL
    app.add_handler(
        CommandHandler(
            "adminPANEL",
            admin_panel
        )
    )

    # Кнопки игр
    app.add_handler(
        CallbackQueryHandler(
            game_button,
            pattern=r"^game_"
        )
    )

    # Кнопки help
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

    # Кнопки админки
    app.add_handler(
        CallbackQueryHandler(
            admin_buttons,
            pattern=r"^admin_"
        )
    )

    # Новые участники
    app.add_handler(
        ChatMemberHandler(
            welcome_new_member,
            ChatMemberHandler.CHAT_MEMBER
        )
    )

    app.add_error_handler(error_handler)

    print("🤖 Vega CHAT запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
