from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

from . import config, prefs
from .emoji_util import extract_emojis, strip_emojis
from .jobs import STORE
from .pipeline import dm, forge_and_upload

log = logging.getLogger("sticker_forge.bot")
DOWNLOAD_DIR = Path(config.WORK_ROOT) / "bot_downloads"
BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024

def _redact(value: object) -> str:
    text = str(value)
    return text.replace(config.BOT_TOKEN, "<bot-token>") if config.BOT_TOKEN else text



def _api(method: str, **kwargs):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}",
            timeout=kwargs.pop("timeout", 30),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"{method}: network error: {_redact(exc)}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method}: Telegram returned invalid JSON") from exc
    if not response.ok or not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', response.text[:300])}")
    return data["result"]


def set_menu_button() -> None:
    if not config.PUBLIC_URL:
        log.warning("PUBLIC_URL not set; skipping menu button")
        return
    _api(
        "setChatMenuButton",
        json={
            "menu_button": {
                "type": "web_app",
                "text": "Forge Stickers",
                "web_app": {"url": f"{config.PUBLIC_URL}/"},
            }
        },
    )
    log.info("Menu button -> %s", config.PUBLIC_URL)


def configure_webhook() -> None:
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN not set; bot disabled")
        return
    if not config.PUBLIC_URL:
        log.warning("PUBLIC_URL not set; webhook setup skipped")
        return
    payload = {
        "url": f"{config.PUBLIC_URL}{config.WEBHOOK_PATH}",
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": False,
    }
    if config.WEBHOOK_SECRET:
        payload["secret_token"] = config.WEBHOOK_SECRET
    _api("setWebhook", json=payload)
    set_menu_button()
    log.info("Webhook configured -> %s%s", config.PUBLIC_URL, config.WEBHOOK_PATH)


def get_webhook_info() -> dict:
    return _api("getWebhookInfo") if config.BOT_TOKEN else {}


def _download(file_id: str) -> str:
    meta = _api("getFile", json={"file_id": file_id})
    size = int(meta.get("file_size") or 0)
    if size and size > BOT_DOWNLOAD_LIMIT:
        raise RuntimeError("file is over Telegram's 20 MB bot-download limit; use the Mini App")
    file_path = meta.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram did not return a file path")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    local = DOWNLOAD_DIR / (file_id + (Path(file_path).suffix or ".mp4"))
    total = 0
    url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{file_path}"
    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with local.open("wb") as fh:
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > BOT_DOWNLOAD_LIMIT:
                        local.unlink(missing_ok=True)
                        raise RuntimeError("file exceeds Telegram's 20 MB bot-download limit; use the Mini App")
                    fh.write(chunk)
    except requests.RequestException as exc:
        local.unlink(missing_ok=True)
        raise RuntimeError(f"file download network error: {_redact(exc)}") from exc
    return str(local)


def _video_file_id(message: dict) -> Optional[str]:
    if "video" in message:
        return message["video"]["file_id"]
    if "animation" in message:
        return message["animation"]["file_id"]
    doc = message.get("document")
    if doc:
        mime = str(doc.get("mime_type", ""))
        name = str(doc.get("file_name", "")).lower()
        if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")):
            return doc["file_id"]
    return None


def _send_theme_keyboard(chat_id: int, current: str) -> None:
    def label(name: str) -> str:
        return ("â " if name == current else "") + name.capitalize()
    _api(
        "sendMessage",
        json={
            "chat_id": chat_id,
            "text": f"Your current theme is *{current}*. Pick one:",
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": [[
                    {"text": label("default"), "callback_data": "theme:default"},
                    {"text": label("degen"), "callback_data": "theme:degen"},
                ]]
            },
        },
    )


def _handle_callback(callback: dict) -> None:
    data = callback.get("data", "")
    user_id = int(callback["from"]["id"])
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    if data.startswith("theme:"):
        chosen = prefs.set_theme_pref(user_id, data.split(":", 1)[1])
        if chat_id:
            try:
                _api(
                    "editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": message.get("message_id"),
                        "text": f"Theme set to *{chosen}* â",
                        "parse_mode": "Markdown",
                    },
                )
            except Exception:
                log.exception("Could not edit theme confirmation")
    try:
        _api("answerCallbackQuery", json={"callback_query_id": callback["id"]})
    except Exception:
        log.exception("Could not answer callback query")


def _handle_message(message: dict) -> None:
    chat_id = int(message["chat"]["id"])
    user_id = int(message["from"]["id"])
    text = message.get("text", "") or ""
    if text.startswith("/start"):
        dm(
            chat_id,
            "Welcome to Sticker Forge! ð¨\n\n"
            "Tap Forge Stickers to open the Mini App, or send a green-screen video directly.\n\n"
            "/theme - Default / Degen\n\n"
            "Caption a video to name the set. Caption emoji become sticker tags.",
        )
        return
    if text.startswith("/theme"):
        _send_theme_keyboard(chat_id, prefs.get_theme_pref(user_id))
        return
    file_id = _video_file_id(message)
    if not file_id:
        return
    caption = message.get("caption", "") or ""
    caption_emojis = extract_emojis(caption)
    text_part = strip_emojis(caption)
    theme = "degen" if "degen" in text_part.lower() else prefs.get_theme_pref(user_id)
    title = text_part.replace("degen", "").strip() or "My Stickers"
    dm(chat_id, "Forging your stickersâ¦ ð¨" + (f"\nTagging with {' '.join(caption_emojis)}" if caption_emojis else ""))
    try:
        local = _download(file_id)
    except Exception as exc:
        dm(chat_id, f"Couldn't download that video ({_redact(exc)}). Try the Mini App for larger files.")
        return
    STORE.submit(
        forge_and_upload,
        [local],
        user_id,
        theme,
        title,
        chat_id=chat_id,
        emojis=caption_emojis or None,
        owner_id=user_id,
    )


def handle_update(update: dict) -> None:
    try:
        if "message" in update:
            _handle_message(update["message"])
        elif "callback_query" in update:
            _handle_callback(update["callback_query"])
    except Exception:
        log.exception("Telegram update handler failed")
