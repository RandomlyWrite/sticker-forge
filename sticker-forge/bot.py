import logging
import os
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

from sticker_forge.video_processor import process_green_screen_to_sticker


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".avi",
    ".mkv",
}


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Respond to /start."""

    message = update.effective_message

    if message is None:
        return

    await message.reply_text(
        "Sticker Forge is online.\n\n"
        "Send me a green-screen video to begin."
    )


def get_uploaded_video(message):
    """
    Extract a Telegram video or video-like document.

    Returns:
        tuple[file_object, filename] | tuple[None, None]
    """

    if message.video:
        return message.video, "input.mp4"

    if message.document:
        document = message.document
        filename = document.file_name or "input.mov"
        extension = Path(filename).suffix.lower()
        mime_type = document.mime_type or ""

        is_video_mime = mime_type.startswith("video/")
        has_video_extension = extension in SUPPORTED_EXTENSIONS

        if is_video_mime or has_video_extension:
            return document, filename

    return None, None


async def receive_video(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Download, process, and return a transparent WebM sticker."""

    message = update.effective_message

    if message is None:
        return

    media, original_name = get_uploaded_video(message)

    if media is None or original_name is None:
        await message.reply_text(
            "That does not appear to be a supported video.\n\n"
            "Send an MP4, MOV, M4V, WebM, AVI, or MKV file."
        )
        return

    await message.reply_text(
        "🔥 Video received. Preparing the forge..."
    )

    try:
        telegram_file = await context.bot.get_file(media.file_id)
    except Exception:
        logger.exception("Could not request the uploaded file from Telegram")

        await message.reply_text(
            "❌ I could not retrieve that video from Telegram."
        )
        return

    with TemporaryDirectory(prefix="sticker_forge_") as temp_dir:
        temp_path = Path(temp_dir)
        safe_extension = Path(original_name).suffix.lower() or ".mp4"

        input_path = temp_path / f"input{safe_extension}"
        output_path = temp_path / "sticker.webm"

        try:
            await telegram_file.download_to_drive(
                custom_path=input_path
            )
        except Exception:
            logger.exception("Video download failed")

            await message.reply_text(
                "❌ The video reached the bot, but its download failed."
            )
            return

        input_size_mb = input_path.stat().st_size / (1024 * 1024)

        await message.reply_text(
            "✅ Download complete.\n"
            f"File: {original_name}\n"
            f"Size: {input_size_mb:.2f} MB\n\n"
            "⚒️ Removing the green screen and forging the sticker..."
        )

        try:
            process_green_screen_to_sticker(
                str(input_path),
                str(output_path),
            )
        except Exception as exc:
            logger.exception("Sticker conversion failed")

            error_text = str(exc).strip()

            if len(error_text) > 700:
                error_text = error_text[-700:]

            await message.reply_text(
                "❌ Conversion failed.\n\n"
                f"{type(exc).__name__}: "
                f"{error_text or 'No error details were returned.'}"
            )
            return

        if not output_path.exists():
            logger.error(
                "Processor completed without creating %s",
                output_path,
            )

            await message.reply_text(
                "❌ The conversion process finished, but no WebM file "
                "was created."
            )
            return

        output_size = output_path.stat().st_size
        output_size_kb = output_size / 1024

        if output_size == 0:
            await message.reply_text(
                "❌ The converter produced an empty WebM file."
            )
            return

        try:
            with output_path.open("rb") as sticker_file:
                await message.reply_document(
                    document=sticker_file,
                    filename="sticker.webm",
                    caption=(
                        "✅ Sticker forged.\n"
                        f"Output size: {output_size_kb:.1f} KB"
                    ),
                )
        except Exception:
            logger.exception("Could not send converted sticker")

            await message.reply_text(
                "❌ The sticker was converted, but Telegram refused "
                "the finished file."
            )


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Log unexpected Telegram handler errors."""

    logger.error(
        "Unhandled Telegram bot error",
        exc_info=context.error,
    )


def build_bot() -> Application:
    """Create and configure the Telegram application."""

    token = os.environ.get("BOT_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "BOT_TOKEN is missing from the Render environment."
        )

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        MessageHandler(
            filters.VIDEO | filters.Document.ALL,
            receive_video,
        )
    )

    application.add_error_handler(error_handler)

    return application
