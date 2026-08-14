from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .themes import get_theme
from .video_processor import validate_telegram_webm

SET_CAP = 120
DEFAULT_EMOJI = "\U0001F525"  # 🔥, written as an escape so source-file encoding cannot corrupt it.


def _looks_like_emoji(value: str) -> bool:
    """Lightweight guard against plain text / mojibake being sent as emoji_list."""
    if not value:
        return False
    if any(marker in value for marker in ("ð", "Ã", "Â", "�")):
        return False

    for ch in value:
        cp = ord(ch)
        if (
            0x1F1E6 <= cp <= 0x1F1FF
            or 0x1F300 <= cp <= 0x1FAFF
            or 0x2600 <= cp <= 0x27BF
            or 0x2300 <= cp <= 0x23FF
            or 0x2B00 <= cp <= 0x2BFF
            or cp in {
                0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
                0x3030, 0x303D, 0x3297, 0x3299, 0x20E3,
            }
        ):
            return True
    return False


class StickerUploader:
    def __init__(self, bot_token: str, user_id: int, theme: str = "default") -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self.token = bot_token
        self.user_id = int(user_id)
        self.theme = get_theme(theme)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

        me = self._make_request("getMe")
        self.bot_username = str(me.get("username") or "").strip()
        if not self.bot_username:
            raise RuntimeError("Telegram bot has no username")

    def _redact(self, value: object) -> str:
        text = str(value)
        return text.replace(self.token, "<bot-token>") if self.token else text

    def _make_request(
        self,
        method: str,
        data: Optional[dict] = None,
        files: Optional[dict] = None,
        timeout: int = 90,
    ):
        url = f"{self.base_url}/{method}"
        try:
            if files:
                response = requests.post(
                    url,
                    data=data or {},
                    files=files,
                    timeout=timeout,
                )
            else:
                response = requests.post(
                    url,
                    json=data or {},
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Network error on {method}: {self._redact(exc)}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{method} returned invalid JSON (HTTP {response.status_code})"
            ) from exc

        if not response.ok or not payload.get("ok"):
            raise RuntimeError(
                f"Telegram error on {method} "
                f"({payload.get('error_code', response.status_code)}): "
                f"{payload.get('description', response.text[:300])}"
            )

        return payload["result"]

    def _safe_prefix(self, value: str) -> str:
        cleaned = re.sub(r"[^a-z0-9_]+", "_", value.lower().strip())
        cleaned = re.sub(r"_+", "_", cleaned).strip("_") or "sticker_forge"
        if not cleaned[0].isalpha():
            cleaned = f"set_{cleaned}"
        return cleaned

    def make_set_name(self, base: str, part: int = 1) -> str:
        suffix = f"_by_{self.bot_username}"
        unique = f"_{self.user_id}_{int(time.time() * 1000) % 10_000_000_000}"
        part_suffix = f"_{part}" if part > 1 else ""

        max_prefix = 64 - len(suffix) - len(unique) - len(part_suffix)
        prefix = (
            self._safe_prefix(base)[: max(1, max_prefix)].rstrip("_")
            or "stickers"
        )
        return f"{prefix}{unique}{part_suffix}{suffix}"

    @staticmethod
    def _emoji_list(emojis: Optional[list[str]], count: int) -> list[str]:
        """Return one valid Unicode emoji per sticker.

        Telegram requires InputSticker.emoji_list to contain 1-20 actual emoji
        strings. Bad source encoding previously turned the default 🔥 into mojibake
        ("ð..."), which Telegram correctly rejected.
        """
        values: list[str] = []

        for raw in emojis or []:
            value = str(raw).strip()
            if _looks_like_emoji(value):
                values.append(value)

        if not values:
            values = [DEFAULT_EMOJI]

        while len(values) < count:
            values.append(values[-1])

        return values[:count]

    @staticmethod
    def _input_sticker(attachment: str, emoji: str) -> dict:
        safe_emoji = emoji if _looks_like_emoji(emoji) else DEFAULT_EMOJI
        return {
            "sticker": f"attach://{attachment}",
            "format": "video",
            "emoji_list": [safe_emoji],
        }

    def get_set(self, name: str) -> dict:
        return self._make_request("getStickerSet", {"name": name})

    def get_set_count(self, name: str) -> int:
        return len(self.get_set(name).get("stickers", []))

    def owns_generated_set_name(self, name: str) -> bool:
        value = str(name or "").strip()
        suffix = f"_by_{self.bot_username}"

        if not value.lower().endswith(suffix.lower()):
            return False

        prefix = value[: -len(suffix)]
        return (
            re.search(
                rf"(?:^|_){re.escape(str(self.user_id))}(?:_|$)",
                prefix,
            )
            is not None
        )

    def _create_new_set(
        self,
        webm: Path,
        name: str,
        title: str,
        emoji: str,
    ) -> None:
        validate_telegram_webm(webm)

        attachment = "sticker_file"
        data = {
            "user_id": str(self.user_id),
            "name": name,
            "title": title[:64] or "Sticker Forge",
            "stickers": json.dumps(
                [self._input_sticker(attachment, emoji)],
                ensure_ascii=False,
            ),
            "sticker_type": "regular",
        }

        with webm.open("rb") as fh:
            self._make_request(
                "createNewStickerSet",
                data=data,
                files={
                    attachment: (
                        webm.name,
                        fh,
                        "video/webm",
                    )
                },
            )

    def _add_to_set(
        self,
        webm: Path,
        name: str,
        emoji: str,
    ) -> None:
        validate_telegram_webm(webm)

        attachment = "sticker_file"
        data = {
            "user_id": str(self.user_id),
            "name": name,
            "sticker": json.dumps(
                self._input_sticker(attachment, emoji),
                ensure_ascii=False,
            ),
        }

        with webm.open("rb") as fh:
            self._make_request(
                "addStickerToSet",
                data=data,
                files={
                    attachment: (
                        webm.name,
                        fh,
                        "video/webm",
                    )
                },
            )

    def create_set(
        self,
        files: list[Path],
        set_name_base: str,
        title: str,
        emojis: Optional[list[str]] = None,
        part: int = 1,
    ) -> dict:
        if not files:
            raise ValueError("No stickers to publish")

        if len(files) > SET_CAP:
            raise ValueError(
                f"A regular sticker set can contain at most {SET_CAP} stickers"
            )

        name = self.make_set_name(set_name_base, part=part)
        title = " ".join(title.split())[:64] or "Sticker Forge"

        if part > 1:
            suffix = f" ({part})"
            title = (title[: 64 - len(suffix)] + suffix).strip()

        emoji_values = self._emoji_list(emojis, len(files))

        self._create_new_set(
            files[0],
            name,
            title,
            emoji_values[0],
        )

        for idx, file in enumerate(files[1:], start=1):
            self._add_to_set(
                file,
                name,
                emoji_values[idx],
            )

        return {
            "name": name,
            "title": title,
            "link": f"https://t.me/addstickers/{name}",
            "count": len(files),
        }

    def add_to_existing(
        self,
        name: str,
        files: list[Path],
        emojis: Optional[list[str]] = None,
    ) -> dict:
        if not files:
            return {
                "name": name,
                "link": f"https://t.me/addstickers/{name}",
                "count": 0,
            }

        existing = self.get_set(name)
        current = len(existing.get("stickers", []))

        if current + len(files) > SET_CAP:
            raise ValueError(
                f"Set has {current} stickers; only {SET_CAP - current} more fit"
            )

        emoji_values = self._emoji_list(emojis, len(files))

        for idx, file in enumerate(files):
            self._add_to_set(
                file,
                name,
                emoji_values[idx],
            )

        return {
            "name": name,
            "title": existing.get("title") or name,
            "link": f"https://t.me/addstickers/{name}",
            "count": len(files),
            "total_count": current + len(files),
        }

    def publish_with_spillover(
        self,
        files: list[Path],
        set_name_base: str,
        set_title: str,
        emojis: Optional[list[str]] = None,
        existing_set_name: Optional[str] = None,
    ) -> list[dict]:
        if not files:
            raise ValueError(
                "No successful clips selected for publishing"
            )

        remaining = list(files)
        links: list[dict] = []
        emoji_values = self._emoji_list(emojis, len(files))
        emoji_offset = 0

        if existing_set_name:
            existing = self.get_set(existing_set_name)
            current = len(existing.get("stickers", []))
            room = max(0, SET_CAP - current)
            chunk = remaining[:room]

            if chunk:
                result = self.add_to_existing(
                    existing_set_name,
                    chunk,
                    emoji_values[: len(chunk)],
                )
                links.append(result)
                remaining = remaining[len(chunk):]
                emoji_offset += len(chunk)

        part = 1

        while remaining:
            chunk = remaining[:SET_CAP]
            chunk_emojis = emoji_values[
                emoji_offset:emoji_offset + len(chunk)
            ]

            result = self.create_set(
                chunk,
                set_name_base,
                set_title,
                chunk_emojis,
                part=part,
            )

            links.append(result)
            remaining = remaining[len(chunk):]
            emoji_offset += len(chunk)
            part += 1

        return links

    def upload_from_folder(
        self,
        folder: str,
        set_name_base: str,
        set_title: str,
        chat_id: Optional[int] = None,
        savage: bool = False,
        verbose: bool = True,
        emojis: Optional[list[str]] = None,
    ) -> dict:
        files = sorted(
            p
            for p in Path(folder).resolve().glob("*.webm")
            if p.is_file()
        )

        links = self.publish_with_spillover(
            files,
            set_name_base,
            set_title,
            emojis,
        )

        total = sum(
            int(item.get("count", 0))
            for item in links
        )

        if (
            chat_id
            and savage
            and self.theme.get("savage_roasts")
        ):
            text = random.choice(
                self.theme["savage_roasts"]
            ).format(link=links[0]["link"])

            try:
                self._make_request(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": text,
                    },
                )
            except Exception:
                if verbose:
                    print("[WARN] themed DM failed")

        return {
            "set_name": links[0]["name"],
            "set_title": links[0]["title"],
            "share_link": links[0]["link"],
            "count": total,
            "links": links,
        }
