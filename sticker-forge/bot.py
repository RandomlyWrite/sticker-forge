from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.message.reply_text(
        "Sticker Forge is online.\n\n"
        "Send me a green-screen video to begin."
    )


async def receive_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message.video:
        media = message.video
        original_name = "input.mp4"
    elif message.document:
        media = message.document
        original_name = message.document.file_name or "input.mov"
    else:
        await message.reply_text("That does not appear to be a video.")
        return

    await message.reply_text("🔥 Video received. Preparing the forge...")

    telegram_file = await context.bot.get_file(media.file_id)

    with TemporaryDirectory() as temp_dir:
        input_path = Path(temp_dir) / original_name
        await telegram_file.download_to_drive(custom_path=input_path)

        file_size = input_path.stat().st_size

        await message.reply_text(
            "✅ Download complete.\n"
            f"File: {original_name}\n"
            f"Size: {file_size / 1024 / 1024:.2f} MB\n\n"
            "Sticker conversion is the next stage."
        )


def build_bot() -> Application:
    import os

    token = os.environ["BOT_TOKEN"]

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))

    application.add_handler(
        MessageHandler(
            filters.VIDEO
            | filters.Document.VIDEO
            | filters.Document.FileExtension("mov")
            | filters.Document.FileExtension("mp4"),
            receive_video,
        )
    )

    return application
