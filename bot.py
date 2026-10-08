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
# НАСТРОЙКИ БОТА
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
# ПОЛЬЗОВАТЕЛИ
# username -> user_id
# =========================

known_users = {}


# =========================
# ЛОГИ
# =========================

logs = []


def add_log(text):
    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    logs.append(
        f"[{timestamp}] {text}"
    )

    # Храним последние 100 логов
    if len(logs) > 100:
        logs.pop(0)


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

    if user.username:
        known_users[
            user.username.lower()
        ] = user.id


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
            "ИИ помоги с кодом"
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
                "🪙 Орёл/решка"
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
            "🔇 Мут после 3 варнов\n\n"
            "Администраторы:\n"
            "/adminPANEL\n"
            "/warn @username"
        )

    elif query.data == "help_info":

        text = (
            "ℹ️ Vega CHAT\n\n"
            "🤖 Telegram AI-бот\n"
            "🧠 Powered by Groq\n"
            "🎮 Игры\n"
            "🛡 Модерация\n\n"
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
                "🔴 Игры сейчас отключены администратором."
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

    if not settings["games"]:

        await query.answer(
            "🔴 Игры отключены.",
            show_alert=True
        )

        return

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
        stats["game_users"].add(
            query.from_user.id
        )

    await context.bot.send_dice(
        chat_id=query.message.chat.id,
        emoji=emoji
    )


# =========================
# ADMIN PANEL
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
            ),
        ],
        [
            InlineKeyboardButton(
                "👋 Приветствие: "
                + ("🟢 ВКЛ" if settings["welcome"] else "🔴 ВЫКЛ"),
                callback_data="admin_welcome"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎮 Игры: "
                + ("🟢 ВКЛ" if settings["games"] else "🔴 ВЫКЛ"),
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
                "🔄 Обновить",
                callback_data="admin_refresh"
            ),
        ],
    ]

    await update.message.reply_text(
        "🛡 Vega CHAT — ADMIN PANEL\n\n"
        "🔐 Доступ разрешён.\n\n"
        "Выбери раздел:",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
            f"👥 Пользователей: "
            f"{len(stats['users'])}\n"
            f"🎮 Игроков: "
            f"{len(stats['game_users'])}\n"
            f"🤖 Запросов ИИ: "
            f"{stats['ai_requests']}\n\n"
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
    # НАЗАД
    # =========================

    elif query.data == "admin_back":

        await show_admin_panel(query)


async def show_admin_panel(
    query,
    notice=None
):

    text = (
        "🛡 Vega CHAT — ADMIN PANEL\n\n"
        "🔐 Доступ разрешён."
    )

    if notice:
        text += f"\n\n{notice}"

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats"
            )
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
            )
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
            )
        ],
        [
            InlineKeyboardButton(
                "📜 Логи",
                callback_data="admin_logs"
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="admin_refresh"
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
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
            "⛔ Только администраторы Vega CHAT могут выдавать варны."
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

    username = context.args[0].lstrip("@").lower()

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
        f"{admin_name} выдал варн @{username} "
        f"({count}/3)"
    )

    # =========================
    # 3 ВАРНА
    # =========================

    if count >= 3:

        until = datetime.now(
            timezone.utc
        ) + timedelta(hours=1)

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

            warnings[key] = 0

            await update.message.reply_text(
                f"🔇 @{username} получил мут на 1 час.\n\n"
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
        f"⚠️ @{username} получил предупреждение.\n\n"
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
            "⛔ Только администраторы могут снимать мут."
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

    username = context.args[0].lstrip("@").lower()

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
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False,
                can_manage_topics=False,
            )
        )

        # Сбрасываем все варны
        warnings[key] = 0

        add_log(
            f"@{admin.username} снял мут и сбросил варны "
            f"@{username}"
        )

        await update.message.reply_text(
            f"🔓 Мут с @{username} снят.\n"
            f"⚠️ Варны сброшены: 0/3"
        )

    except Exception as e:
        print(f"Unmute error: {e}")

        await update.message.reply_text(
            "❌ Не удалось снять мут.\n"
            "Проверь права бота."
        )


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
        and new_status in {
            "member",
            "administrator"
        }
    ):

        user = result.new_chat_member.user

        remember_user(user)

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
    print(
        f"Telegram error: {context.error}"
    )
    # что после

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

    # /warn
    app.add_handler(
        CommandHandler(
            "warn",
            warn_command
        )
    )

    # Игры
    app.add_handler(
        CallbackQueryHandler(
            game_button,
            pattern=r"^game_"
        )
    )

    # Кнопки Help
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

    # Кнопки админ-панели
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

    # Обработчик ошибок
    app.add_error_handler(
        error_handler
    )

    print("🤖 Vega CHAT запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
