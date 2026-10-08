from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    filters,
)
from groq import AsyncGroq

from config import BOT_TOKEN, GROQ_API_KEY


client = AsyncGroq(api_key=GROQ_API_KEY)

MODEL = "openai/gpt-oss-20b"


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # Бот реагирует только на сообщения,
    # начинающиеся с "ИИ"
    if not text.lower().startswith("ии"):
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

        # Telegram имеет ограничение на размер сообщения
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


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    print(f"Telegram error: {context.error}")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    app.add_error_handler(error_handler)

    print("🤖 Бот запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()
