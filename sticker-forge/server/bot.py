"""The bot: entry point + the "send a video" input path.

Two jobs:
1. On startup, point the chat **menu button** at the Mini App so users get an
   "Open" button beside the message box.
2. Accept videos sent directly to the bot, run the same pipeline, and DM the
   resulting sticker-set link.

Uses plain long polling (getUpdates) to stay dependency-light and consistent
with the rest of the framework. For higher volume, switch to webhooks.

NOTE: the Bot API can only download files up to ~20 MB unless you run a local
Bot API server. Larger clips should go through the Mini App upload path.
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
    resp = requests.post(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}",
        timeout=kwargs.pop("timeout", 30),
        **kwargs,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description')}")
    return data["result"]


def set_menu_button() -> None:
    """Make the chat menu button open the Mini App."""
    if not config.PUBLIC_URL:
        log.warning("PUBLIC_URL not set; skipping menu-button setup.")
        return
    _api(
        "setChatMenuButton",
        json={
            "menu_button": {
                "type": "web_app",
                "text": "Forge Stickers",
                "web_app": {"url": config.PUBLIC_URL + "/"},
            }
        },
    )
    log.info("Menu button -> %s", config.PUBLIC_URL)


def _download(file_id: str) -> str:
    meta = _api("getFile", json={"file_id": file_id})
    file_path = meta["file_path"]
    url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local = DOWNLOAD_DIR / (file_id + Path(file_path).suffix)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with local.open("wb") as fh:
            for chunk in r.iter_content(8192):
                fh.write(chunk)
    return str(local)


def _video_file_id(message: dict) -> Optional[str]:
    if "video" in message:
        return message["video"]["file_id"]
    if "animation" in message:
        return message["animation"]["file_id"]
    doc = message.get("document")
    if doc and str(doc.get("mime_type", "")).startswith("video/"):
        return doc["file_id"]
    return None


def _send_theme_keyboard(chat_id: int, current: str) -> None:
    def label(name: str) -> str:
        return ("\u2705 " if name == current else "") + name.capitalize()
    _api("sendMessage", json={
        "chat_id": chat_id,
        "text": f"Your current theme is *{current}*. Pick one:",
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": [[
            {"text": label("default"), "callback_data": "theme:default"},
            {"text": label("degen"), "callback_data": "theme:degen"},
        ]]},
    })


def _handle_callback(cb: dict) -> None:
    data = cb.get("data", "")
    user_id = cb["from"]["id"]
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    if data.startswith("theme:"):
        chosen = prefs.set_theme_pref(user_id, data.split(":", 1)[1])
        try:
            _api("editMessageText", json={
                "chat_id": chat_id, "message_id": msg.get("message_id"),
                "text": f"Theme set to *{chosen}* \u2705", "parse_mode": "Markdown",
            })
        except Exception:  # noqa: BLE001
            pass
    # Always answer to dismiss the loading spinner on the button.
    try:
        _api("answerCallbackQuery", json={"callback_query_id": cb["id"]})
    except Exception:  # noqa: BLE001
        pass


def _handle_message(message: dict) -> None:
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")

    if text.startswith("/start"):
        dm(chat_id,
           "Welcome to Sticker Forge! \U0001F528\n\n"
           "Tap the menu button to open the app, or just send me a green-screen "
           "video and I'll forge a sticker set.\n\n"
           "Commands:\n"
           "/theme – choose your default look (default / degen)\n\n"
           "Tip: caption the video to name the set. Any emoji in the caption become\n"
           "that sticker's tags (e.g. \"Sassy 💅😏\"). Include 'degen' to override the theme once.")
        return

    if text.startswith("/theme"):
        current = prefs.get_theme_pref(user_id)
        _send_theme_keyboard(chat_id, current)
        return

    file_id = _video_file_id(message)
    if not file_id:
        return

    caption = message.get("caption", "") or ""
    # Emoji in the caption become this sticker's tags; the rest becomes the title.
    caption_emojis = extract_emojis(caption)
    text_part = strip_emojis(caption)
    theme = "degen" if "degen" in text_part.lower() else prefs.get_theme_pref(user_id)
    title = text_part.replace("degen", "").strip() or "My Stickers"

    dm(chat_id, "Forging your stickers\u2026 \U0001F528"
                + (f"\nTagging with {' '.join(caption_emojis)}" if caption_emojis else ""))
    try:
        local = _download(file_id)
    except Exception as e:  # noqa: BLE001
        dm(chat_id, f"Couldn't download that video ({e}). Try the Mini App for larger files.")
        return

    job = STORE.submit(forge_and_upload, [local], user_id, theme, title,
                       chat_id=chat_id, emojis=caption_emojis or None)
    _watch_job(job, chat_id)


def _watch_job(job, chat_id: int, timeout_s: int = 600) -> None:
    """Report failures back to the user.

    Without this a failed job is silent: the "Forging your stickers..." message
    goes out and nothing ever follows, which is indistinguishable from a hang.
    """
    def _wait() -> None:
        waited = 0
        while waited < timeout_s:
            if job.status == "error":
                dm(chat_id,
                   "That clip couldn't be forged \u26a0\ufe0f\n"
                   f"{job.error or 'unknown error'}\n\n"
                   "Try a shorter clip, or one with a solid, evenly-lit green "
                   "background.")
                return
            if job.status == "done":
                return
            time.sleep(2)
            waited += 2
        dm(chat_id,
           "That clip is taking unusually long and may have stalled \u23f3\n"
           "Try a shorter clip (2-3 seconds) or a smaller file.")

    threading.Thread(target=_wait, daemon=True).start()


def run_bot(poll_timeout: int = 25) -> None:
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN not set; bot disabled.")
        return
    try:
        set_menu_button()
    except Exception as e:  # noqa: BLE001
        log.warning("menu button setup failed: %s", e)

    log.info("Bot polling started.")
    offset = 0
    while True:
        try:
            updates = _api(
                "getUpdates",
                json={"offset": offset, "timeout": poll_timeout,
                      "allowed_updates": ["message", "callback_query"]},
                timeout=poll_timeout + 10,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("getUpdates error: %s", e)
            time.sleep(3)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    _handle_message(upd["message"])
                elif "callback_query" in upd:
                    _handle_callback(upd["callback_query"])
            except Exception as e:  # noqa: BLE001
                log.exception("handler error: %s", e)
