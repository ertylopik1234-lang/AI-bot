from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)

from google import genai

from config import BOT_TOKEN, GEMINI_API_KEY


ai = genai.Client(api_key=GEMINI_API_KEY)


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
        return

    message = update.message.text.strip()

    # Реагируем только на сообщения, начинающиеся с "ИИ"
    if not message.lower().startswith("ии"):
        return

    prompt = message[2:].strip()

    if not prompt:
        await update.message.reply_text(
            "Напиши запрос после «ИИ» 🙂\n\n"
            "Например:\n"
            "ИИ придумай название для канала"
        )
        return

    await update.message.chat.send_action("typing")

    try:
        response = ai.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        answer = response.text or "Не удалось получить ответ."

        # Разбиваем длинный ответ на сообщения Telegram
        max_length = 4000

        for i in range(0, len(answer), max_length):
            await update.message.reply_text(
                answer[i:i + max_length]
            )

    except Exception as e:
        print("Gemini error:", e)

        await update.message.reply_text(
            "❌ Не удалось получить ответ от ИИ."
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("Бот запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
