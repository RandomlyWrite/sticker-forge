from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

import requests

from sticker_forge.batch_processor import forge_sticker_set
from sticker_forge.telegram_uploader import StickerUploader

from . import config
from .sets_store import save_set


log = logging.getLogger("sticker_forge.pipeline")


def dm(chat_id: int, text: str) -> None:
    """Send a direct message through the Telegram Bot API."""

    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured.")

    response = requests.post(
        f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Telegram returned an invalid response."
        ) from exc

    if not response.ok or not payload.get("ok"):
        raise RuntimeError(
            "Telegram sendMessage failed: "
            f"{payload.get('description', response.text)}"
        )


def _safe_title(value: str) -> str:
    title = " ".join(value.strip().split())

    if not title:
        return "My Stickers"

    return title[:64]


def _job_directory(user_id: int) -> Path:
    root = Path(config.WORK_ROOT)
    unique = f"bot_{user_id}_{int(time.time() * 1000)}"
    directory = root / unique

    directory.mkdir(
        parents=True,
        exist_ok=False,
    )

    return directory


def forge_and_upload(
    input_files: list[str],
    user_id: int,
    theme: str,
    title: str,
    chat_id: Optional[int] = None,
    emojis: Optional[list[str]] = None,
    job=None,
) -> dict:
    """
    Process uploaded clips and create a Telegram sticker set.

    This powers the bot's one-shot path:
    download → encode → upload → DM result.
    """

    if not input_files:
        raise ValueError("No input files were supplied.")

    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured.")

    selected_theme = (
        theme
        if theme in {"default", "degen"}
        else config.DEFAULT_THEME
    )

    set_title = _safe_title(title)
    work_dir = _job_directory(user_id)
    input_dir = work_dir / "input"
    output_dir = work_dir / "output"

    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if job is not None:
            job.phase = "publish"
            job.stage = "preparing clips"

        # forge_sticker_set currently scans only *.mp4 files.
        # FFmpeg identifies media from its contents, so clips are copied
        # into numbered .mp4 filenames before batch processing.
        for index, source_name in enumerate(input_files, start=1):
            source = Path(source_name)

            if not source.exists():
                raise FileNotFoundError(
                    f"Input file does not exist: {source}"
                )

            destination = input_dir / f"clip_{index:03d}.mp4"

            shutil.copy2(
                source,
                destination,
            )

        if job is not None:
            job.stage = "removing green screen"

        forged = forge_sticker_set(
            input_folder=str(input_dir),
            output_folder=str(output_dir),
            theme=selected_theme,
            overwrite=True,
            verbose=False,
        )

        count = int(forged.get("count", 0))

        if count < 1:
            raise RuntimeError(
                "The processor completed without producing stickers."
            )

        if job is not None:
            job.stage = "creating Telegram sticker set"

        unique_base = (
            f"sticker_forge_{user_id}_{int(time.time())}"
        )

        uploader = StickerUploader(
            bot_token=config.BOT_TOKEN,
            user_id=user_id,
            theme=selected_theme,
        )

        uploaded = uploader.upload_from_folder(
            folder=str(output_dir),
            set_name_base=unique_base,
            set_title=set_title,
            chat_id=None,
            savage=False,
            verbose=False,
            emojis=emojis,
        )

        set_name = uploaded["set_name"]
        share_link = uploaded["share_link"]
        uploaded_count = int(uploaded["count"])

        save_set(
            user_id=user_id,
            name=set_name,
            title=set_title,
            count=uploaded_count,
        )

        links = [
            {
                "name": set_name,
                "title": set_title,
                "link": share_link,
                "count": uploaded_count,
            }
        ]

        if job is not None:
            job.stage = "sending result"
            job.links = links

        if chat_id is not None:
            dm(
                chat_id,
                "Sticker set forged ✅\n\n"
                f"{share_link}",
            )

        return {
            "phase": "published",
            "stage": "complete",
            "set_name": set_name,
            "title": set_title,
            "link": share_link,
            "links": links,
            "count": uploaded_count,
        }

    except Exception as exc:
        log.exception(
            "Forge-and-upload pipeline failed for user %s",
            user_id,
        )

        if job is not None:
            job.stage = "failed"

        raise RuntimeError(str(exc)) from exc

    finally:
        # Keep failed job files briefly only while debugging by commenting
        # out this line. Normal operation cleans temporary files.
        shutil.rmtree(
            work_dir,
            ignore_errors=True,
        )
