from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TELEGRAM_TOKEN = "8298157683:AAG8-TLkM4hpNZdOocWRqEr7BywKEc3rea0"
WEB_APP_URL = "https://steady-brioche-e0b7ee.netlify.app/"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("✍️ Проверить работу", web_app=WebAppInfo(url=WEB_APP_URL))]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        "👋 Привет! Я помогу проверить твою работу по ЕГЭ.\n\n"
        "Нажми кнопку ниже 👇",
        reply_markup=keyboard
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()