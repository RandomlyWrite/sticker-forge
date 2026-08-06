import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Sticker Forge is online.\n\n"
        "Send me a green-screen video to begin."
    )


def build_bot() -> Application:
    token = os.environ["BOT_TOKEN"]

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))

    return application
