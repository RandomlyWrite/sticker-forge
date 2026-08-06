"""Telegram bot entry point for Sticker Forge.

Responsibilities:
1. Point the Telegram chat menu button at the Mini App.
2. Accept green-screen videos sent directly to the bot.
3. Run the same forge-and-upload pipeline used by the Mini App.
4. DM the resulting sticker-set link or a useful failure message.

This module uses long polling through getUpdates.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

import requests

from . import config, prefs
from .emoji_util import extract_emojis, strip_emojis
from .jobs import STORE
from .pipeline import dm, forge_and_upload


log = logging.getLogger("sticker_forge.bot")

DOWNLOAD_DIR = Path(config.WORK_ROOT) / "bot_downloads"


def _api(method: str, **kwargs):
    """Call a Telegram Bot API method and return its result."""

    response = requests.post(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}",
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{method}: Telegram returned invalid JSON"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(
            f"{method}: {data.get('description', 'unknown Telegram error')}"
        )

    return data["result"]


def set_menu_button() -> None:
    """Make the Telegram chat menu button open the Mini App."""

    if not config.PUBLIC_URL:
        log.warning(
            "PUBLIC_URL not set; skipping menu-button setup."
        )
        return

    _api(
        "setChatMenuButton",
        json={
            "menu_button": {
                "type": "web_app",
                "text": "Forge Stickers",
                "web_app": {
                    "url": f"{config.PUBLIC_URL.rstrip('/')}/"
                },
            }
        },
    )

    log.info(
        "Menu button configured for %s",
        config.PUBLIC_URL,
    )


def _download(file_id: str) -> str:
    """Download a Telegram-hosted file to the bot work directory."""

    metadata = _api(
        "getFile",
        json={"file_id": file_id},
    )

    file_path = metadata["file_path"]
    download_url = (
        f"https://api.telegram.org/file/"
        f"bot{config.BOT_TOKEN}/{file_path}"
    )

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    suffix = Path(file_path).suffix or ".mp4"
    local_path = DOWNLOAD_DIR / f"{file_id}{suffix}"

    with requests.get(
        download_url,
        stream=True,
        timeout=120,
    ) as response:
        response.raise_for_status()

        with local_path.open("wb") as file_handle:
            for chunk in response.iter_content(
                chunk_size=8192
            ):
                if chunk:
                    file_handle.write(chunk)

    return str(local_path)


def _video_file_id(message: dict) -> Optional[str]:
    """Return the file ID for a video-like Telegram message."""

    if "video" in message:
        return message["video"]["file_id"]

    if "animation" in message:
        return message["animation"]["file_id"]

    document = message.get("document")

    if document:
        mime_type = str(
            document.get("mime_type", "")
        )

        if mime_type.startswith("video/"):
            return document["file_id"]

        filename = str(
            document.get("file_name", "")
        ).lower()

        if filename.endswith(
            (
                ".mp4",
                ".mov",
                ".m4v",
                ".webm",
                ".avi",
                ".mkv",
            )
        ):
            return document["file_id"]

    return None


def _send_theme_keyboard(
    chat_id: int,
    current: str,
) -> None:
    """Show the saved-theme picker."""

    def label(name: str) -> str:
        prefix = "✅ " if name == current else ""
        return prefix + name.capitalize()

    _api(
        "sendMessage",
        json={
            "chat_id": chat_id,
            "text": (
                f"Your current theme is *{current}*. "
                "Pick one:"
            ),
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": label("default"),
                            "callback_data": "theme:default",
                        },
                        {
                            "text": label("degen"),
                            "callback_data": "theme:degen",
                        },
                    ]
                ]
            },
        },
    )


def _handle_callback(callback: dict) -> None:
    """Handle inline-keyboard callbacks."""

    data = callback.get("data", "")
    user_id = callback["from"]["id"]

    message = callback.get("message", {})
    chat_id = (
        message.get("chat", {}).get("id")
    )
    message_id = message.get("message_id")

    if data.startswith("theme:"):
        requested = data.split(":", 1)[1]

        chosen = prefs.set_theme_pref(
            user_id,
            requested,
        )

        try:
            _api(
                "editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": (
                        f"Theme set to *{chosen}* ✅"
                    ),
                    "parse_mode": "Markdown",
                },
            )
        except Exception:
            log.exception(
                "Could not edit theme confirmation message"
            )

    try:
        _api(
            "answerCallbackQuery",
            json={
                "callback_query_id": callback["id"]
            },
        )
    except Exception:
        log.exception(
            "Could not answer callback query"
        )


def _handle_message(message: dict) -> None:
    """Handle commands and direct video uploads."""

    chat = message.get("chat", {})
    sender = message.get("from", {})

    chat_id = chat.get("id")
    user_id = sender.get("id")

    if chat_id is None or user_id is None:
        return

    text = message.get("text", "") or ""

    if text.startswith("/start"):
        dm(
            chat_id,
            "Welcome to Sticker Forge! 🔨\n\n"
            "Tap the menu button to open the Mini App, "
            "or send me a green-screen video and I’ll "
            "forge a sticker set.\n\n"
            "Commands:\n"
            "/theme - choose your default look "
            "(default / degen)\n\n"
            "Tip: caption the video to name the set. "
            "Any emoji in the caption become that "
            "sticker’s tags. Include “degen” to "
            "override the saved theme for one upload.",
        )
        return

    if text.startswith("/theme"):
        current = prefs.get_theme_pref(user_id)

        _send_theme_keyboard(
            chat_id,
            current,
        )
        return

    file_id = _video_file_id(message)

    if not file_id:
        return

    caption = message.get("caption", "") or ""

    caption_emojis = extract_emojis(caption)
    title_text = strip_emojis(caption)

    saved_theme = prefs.get_theme_pref(user_id)

    if "degen" in title_text.lower():
        theme = "degen"
    else:
        theme = saved_theme

    cleaned_title = title_text.replace(
        "degen",
        "",
    ).strip()

    title = cleaned_title or "My Stickers"

    status_text = "Forging your stickers… 🔨"

    if caption_emojis:
        status_text += (
            "\nTagging with "
            + " ".join(caption_emojis)
        )

    dm(
        chat_id,
        status_text,
    )

    try:
        local_path = _download(file_id)
    except Exception as exc:
        log.exception(
            "Telegram file download failed"
        )

        dm(
            chat_id,
            "Couldn’t download that video.\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Try the Mini App for larger files.",
        )
        return

    job = STORE.submit(
        forge_and_upload,
        [local_path],
        user_id,
        theme,
        title,
        chat_id=chat_id,
        emojis=caption_emojis or None,
    )

    _watch_job(
        job,
        chat_id,
    )


def _watch_job(
    job,
    chat_id: int,
    timeout_s: int = 600,
) -> None:
    """Report asynchronous job failures and stalls."""

    def _wait() -> None:
        waited = 0

        while waited < timeout_s:
            if job.status == "error":
                dm(
                    chat_id,
                    "That clip couldn’t be forged ⚠️\n"
                    f"{job.error or 'Unknown error'}\n\n"
                    "Try a shorter clip, or one with a "
                    "solid, evenly-lit green background.",
                )
                return

            if job.status == "done":
                return

            time.sleep(2)
            waited += 2

        dm(
            chat_id,
            "That clip is taking unusually long and "
            "may have stalled ⏳\n"
            "Try a shorter clip, preferably 2 to 3 "
            "seconds, or a smaller file.",
        )

    threading.Thread(
        target=_wait,
        daemon=True,
        name=f"watch-job-{job.id[:8]}",
    ).start()


def run_bot(
    poll_timeout: int = 25,
) -> None:
    """Run the long-polling Telegram bot loop."""

    if not config.BOT_TOKEN:
        log.warning(
            "BOT_TOKEN not set; bot disabled."
        )
        return

    try:
        _api(
            "deleteWebhook",
            json={
                "drop_pending_updates": False,
            },
        )

        log.info(
            "Existing Telegram webhook removed."
        )
    except Exception as exc:
        log.warning(
            "Webhook removal failed: %s",
            exc,
        )

    try:
        set_menu_button()
    except Exception as exc:
        log.warning(
            "Menu button setup failed: %s",
            exc,
        )

    log.info("Bot polling started.")

    offset = 0

    while True:
        try:
            updates = _api(
                "getUpdates",
                json={
                    "offset": offset,
                    "timeout": poll_timeout,
                    "allowed_updates": [
                        "message",
                        "callback_query",
                    ],
                },
                timeout=poll_timeout + 10,
            )
        except Exception as exc:
            log.warning(
                "getUpdates error: %s",
                exc,
            )

            time.sleep(3)
            continue

        for update in updates:
            offset = update["update_id"] + 1

            try:
                if "message" in update:
                    _handle_message(
                        update["message"]
                    )

                elif "callback_query" in update:
                    _handle_callback(
                        update["callback_query"]
                    )

            except Exception as exc:
                log.exception(
                    "Telegram update handler failed: %s",
                    exc,
                )
