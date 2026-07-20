import random
import time
from pathlib import Path
from typing import Optional, List
import requests
from .themes import get_theme

class StickerUploader:
    def __init__(self, bot_token: str, user_id: int, theme: str = "default"):
        self.token = bot_token
        self.user_id = user_id
        self.theme = get_theme(theme)
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def _make_request(self, method: str, data: dict = None, files: dict = None):
        url = f"{self.base_url}/{method}"
        try:
            if files:
                resp = requests.post(url, data=data, files=files, timeout=60)
            else:
                resp = requests.post(url, json=data, timeout=30)
            result = resp.json()
            if not result.get("ok"):
                raise RuntimeError(f"Telegram error on {method}: {result.get('description')}")
            return result["result"]
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Network error on {method}: {e}")

    def upload_from_folder(
        self,
        folder: str,
        set_name_base: str,
        set_title: str,
        chat_id: Optional[int] = None,
        savage: bool = False,
        verbose: bool = True
    ):
        folder_path = Path(folder).resolve()
        webm_files = sorted(folder_path.glob("*.webm"))
        if not webm_files:
            raise ValueError("No .webm files found.")

        # TODO: Full createNewStickerSet + addStickerToSet implementation
        # (Can be added later — core structure is ready)

        share_link = f"https://t.me/addstickers/{set_name_base}_by_YOURBOT"

        if chat_id and savage and self.theme.get("savage_roasts"):
            roast = random.choice(self.theme["savage_roasts"]).format(link=share_link)
            try:
                self._make_request("sendMessage", {
                    "chat_id": chat_id,
                    "text": roast
                })
            except Exception as e:
                if verbose:
                    print(f"[WARN] Could not send savage message: {e}")

        return {
            "share_link": share_link,
            "count": len(webm_files)
        }
