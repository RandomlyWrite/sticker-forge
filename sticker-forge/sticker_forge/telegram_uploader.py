from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Optional

import requests

from .themes import get_theme


class StickerUploader:
    """Create and extend Telegram video-sticker sets."""

    def __init__(
        self,
        bot_token: str,
        user_id: int,
        theme: str = "default",
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")

        self.token = bot_token
        self.user_id = int(user_id)
        self.theme = get_theme(theme)
        self.base_url = (
            f"https://api.telegram.org/bot{bot_token}"
        )
        self.bot_username = self._get_bot_username()

    def _make_request(
        self,
        method: str,
        data: Optional[dict] = None,
        files: Optional[dict] = None,
        timeout: int = 90,
    ):
        """Call Telegram and preserve its useful error description."""

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
                f"Network error on {method}: {exc}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            body = response.text[:500]

            raise RuntimeError(
                f"{method} returned invalid JSON "
                f"(HTTP {response.status_code}): {body}"
            ) from exc

        if not payload.get("ok"):
            description = payload.get(
                "description",
                "Unknown Telegram error",
            )

            error_code = payload.get(
                "error_code",
                response.status_code,
            )

            raise RuntimeError(
                f"Telegram error on {method} "
                f"({error_code}): {description}"
            )

        return payload["result"]

    def _get_bot_username(self) -> str:
        result = self._make_request("getMe")
        username = result.get("username")

        if not username:
            raise RuntimeError(
                "The Telegram bot does not have a username."
            )

        return str(username)

    def _safe_set_name(
        self,
        value: str,
        unique: bool = True,
    ) -> str:
        """
        Build a legal Telegram sticker-set short name.

        It must:
        - begin with a letter
        - contain only letters, digits, and underscores
        - avoid consecutive underscores
        - end with _by_<bot username>
        - stay within 64 characters
        """

        cleaned = value.lower().strip()
        cleaned = re.sub(
            r"[^a-z0-9_]+",
            "_",
            cleaned,
        )
        cleaned = re.sub(
            r"_+",
            "_",
            cleaned,
        )
        cleaned = cleaned.strip("_")

        if not cleaned:
            cleaned = "sticker_forge"

        if not cleaned[0].isalpha():
            cleaned = f"set_{cleaned}"

        if unique:
            cleaned = (
                f"{cleaned}_{self.user_id}_{int(time.time())}"
            )

        suffix = f"_by_{self.bot_username}"
        max_prefix_length = 64 - len(suffix)

        cleaned = cleaned[
            :max_prefix_length
        ].rstrip("_")

        if not cleaned:
            cleaned = "stickers"

        return f"{cleaned}{suffix}"

    @staticmethod
    def _normalize_emojis(
        emojis: Optional[list[str]],
        file_count: int,
    ) -> list[str]:
        values = [
            str(value).strip()
            for value in (emojis or [])
            if str(value).strip()
        ]

        if not values:
            values = ["🔥"]

        while len(values) < file_count:
            values.append(values[-1])

        return values[:file_count]

    def _create_new_set(
        self,
        webm_file: Path,
        set_name: str,
        set_title: str,
        emoji: str,
    ) -> None:
        attachment_name = "sticker_file"

        input_sticker = {
            "sticker": f"attach://{attachment_name}",
            "format": "video",
            "emoji_list": [emoji],
        }

        data = {
            "user_id": str(self.user_id),
            "name": set_name,
            "title": set_title,
            "stickers": json.dumps(
                [input_sticker],
                ensure_ascii=False,
            ),
            "sticker_type": "regular",
        }

        with webm_file.open("rb") as file_handle:
            files = {
                attachment_name: (
                    webm_file.name,
                    file_handle,
                    "video/webm",
                )
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
        attachment_name = "sticker_file"

        input_sticker = {
            "sticker": f"attach://{attachment_name}",
            "format": "video",
            "emoji_list": [emoji],
        }

        data = {
            "user_id": str(self.user_id),
            "name": set_name,
            "sticker": json.dumps(
                input_sticker,
                ensure_ascii=False,
            ),
        }

        with webm_file.open("rb") as file_handle:
            files = {
                attachment_name: (
                    webm_file.name,
                    file_handle,
                    "video/webm",
                )
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
    ) -> dict:
        """Upload every WebM in a folder to one new sticker set."""

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

        # Regular sticker sets currently support up to 120 items.
        if len(webm_files) > 120:
            raise ValueError(
                "This uploader currently handles one set at a "
                "time, with a maximum of 120 stickers."
            )

        title = " ".join(set_title.split())[:64]

        if not title:
            title = "Sticker Forge"

        set_name = self._safe_set_name(
            set_name_base,
            unique=True,
        )

        emoji_values = self._normalize_emojis(
            emojis,
            len(webm_files),
        )

        if verbose:
            print(
                f"Creating set {set_name} with "
                f"{len(webm_files)} sticker(s)"
            )

        self._create_new_set(
            webm_file=webm_files[0],
            set_name=set_name,
            set_title=title,
            emoji=emoji_values[0],
        )

        uploaded = 1

        for index, webm_file in enumerate(
            webm_files[1:],
            start=1,
        ):
            self._add_to_set(
                webm_file=webm_file,
                set_name=set_name,
                emoji=emoji_values[index],
            )

            uploaded += 1

            if verbose:
                print(
                    f"Uploaded {uploaded}/"
                    f"{len(webm_files)}: "
                    f"{webm_file.name}"
                )

        share_link = (
            f"https://t.me/addstickers/{set_name}"
        )

        if (
            chat_id is not None
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
                        "[WARN] Could not send themed "
                        f"message: {exc}"
                    )

        return {
            "set_name": set_name,
            "set_title": title,
            "share_link": share_link,
            "count": uploaded,
        }
