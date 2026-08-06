from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Optional

import requests

from .themes import get_theme


class StickerUploader:
    def __init__(
        self,
        bot_token: str,
        user_id: int,
        theme: str = "default",
    ):
        self.token = bot_token
        self.user_id = user_id
        self.theme = get_theme(theme)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.bot_username = self._get_bot_username()

    def _make_request(
        self,
        method: str,
        data: dict | None = None,
        files: dict | None = None,
        timeout: int = 60,
    ):
        url = f"{self.base_url}/{method}"

        try:
            if files:
                response = requests.post(
                    url,
                    data=data,
                    files=files,
                    timeout=timeout,
                )
            else:
                response = requests.post(
                    url,
                    json=data,
                    timeout=timeout,
                )

            response.raise_for_status()
            result = response.json()

        except requests.RequestException as exc:
            raise RuntimeError(
                f"Network error on {method}: {exc}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError(
                f"Telegram returned invalid JSON on {method}"
            ) from exc

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram error on {method}: "
                f"{result.get('description', 'unknown error')}"
            )

        return result["result"]

    def _get_bot_username(self) -> str:
        result = self._make_request("getMe")
        username = result.get("username")

        if not username:
            raise RuntimeError(
                "Telegram bot has no username."
            )

        return username

    def _safe_set_name(self, value: str) -> str:
        cleaned = value.lower()
        cleaned = re.sub(r"[^a-z0-9_]+", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned)
        cleaned = cleaned.strip("_")

        if not cleaned:
            cleaned = "sticker_forge"

        if not cleaned[0].isalpha():
            cleaned = f"set_{cleaned}"

        suffix = f"_by_{self.bot_username}"
        max_prefix = 64 - len(suffix)

        cleaned = cleaned[:max_prefix].rstrip("_")

        return f"{cleaned}{suffix}"

    def _create_new_set(
        self,
        webm_file: Path,
        set_name: str,
        set_title: str,
        emoji: str,
    ) -> None:
        with webm_file.open("rb") as file_handle:
            files = {
                "sticker": (
                    webm_file.name,
                    file_handle,
                    "video/webm",
                )
            }

            data = {
                "user_id": str(self.user_id),
                "name": set_name,
                "title": set_title,
                "sticker_format": "video",
                "emojis": emoji,
            }

            self._make_request(
                "createNewStickerSet",
                data=data,
                files=files,
            )

    def _add_to_set(
        self,
        webm_file: Path,
        set_name: str,
        emoji: str,
    ) -> None:
        with webm_file.open("rb") as file_handle:
            files = {
                "sticker": (
                    webm_file.name,
                    file_handle,
                    "video/webm",
                )
            }

            data = {
                "user_id": str(self.user_id),
                "name": set_name,
                "sticker_format": "video",
                "emojis": emoji,
            }

            self._make_request(
                "addStickerToSet",
                data=data,
                files=files,
            )

    def upload_from_folder(
        self,
        folder: str,
        set_name_base: str,
        set_title: str,
        chat_id: Optional[int] = None,
        savage: bool = False,
        verbose: bool = True,
        emojis: Optional[list[str]] = None,
    ):
        folder_path = Path(folder).resolve()

        webm_files = sorted(
            path
            for path in folder_path.glob("*.webm")
            if path.is_file()
        )

        if not webm_files:
            raise ValueError(
                f"No .webm files found in {folder_path}"
            )

        set_name = self._safe_set_name(set_name_base)
        title = set_title.strip()[:64] or "Sticker Forge"

        emoji_list = emojis or ["🔥"]
        default_emoji = emoji_list[0] if emoji_list else "🔥"

        first = webm_files[0]

        self._create_new_set(
            webm_file=first,
            set_name=set_name,
            set_title=title,
            emoji=default_emoji,
        )

        uploaded = 1

        for index, webm_file in enumerate(
            webm_files[1:],
            start=1,
        ):
            emoji = emoji_list[
                min(index, len(emoji_list) - 1)
            ]

            self._add_to_set(
                webm_file=webm_file,
                set_name=set_name,
                emoji=emoji,
            )

            uploaded += 1

            if verbose:
                print(
                    f"Uploaded {uploaded}/{len(webm_files)}: "
                    f"{webm_file.name}"
                )

        share_link = (
            f"https://t.me/addstickers/{set_name}"
        )

        if (
            chat_id
            and savage
            and self.theme.get("savage_roasts")
        ):
            roast = random.choice(
                self.theme["savage_roasts"]
            ).format(link=share_link)

            try:
                self._make_request(
                    "sendMessage",
                    data={
                        "chat_id": chat_id,
                        "text": roast,
                    },
                )
            except Exception as exc:
                if verbose:
                    print(
                        f"[WARN] Could not send themed message: {exc}"
                    )

        return {
            "set_name": set_name,
            "set_title": title,
            "share_link": share_link,
            "count": uploaded,
        }
