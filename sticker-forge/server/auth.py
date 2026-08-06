from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl

from . import config


MAX_INIT_DATA_AGE_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TelegramUser:
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool = False


class InitDataError(ValueError):
    pass


def validate_init_data(
    init_data: str,
    max_age_seconds: int = MAX_INIT_DATA_AGE_SECONDS,
) -> TelegramUser:
    """
    Validate Telegram Mini App initData and return the trusted user.

    Never trust a user_id sent separately by the browser.
    """

    if not config.BOT_TOKEN:
        raise InitDataError("BOT_TOKEN is not configured.")

    if not init_data:
        raise InitDataError(
            "Telegram initData is missing. Open this page from the bot."
        )

    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", None)

    if not received_hash:
        raise InitDataError("Telegram initData has no hash.")

    data_check_string = "\n".join(
        f"{key}={values[key]}"
        for key in sorted(values)
    )

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=config.BOT_TOKEN.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise InitDataError("Telegram initData signature is invalid.")

    auth_date_raw = values.get("auth_date")

    if not auth_date_raw:
        raise InitDataError("Telegram initData has no auth_date.")

    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise InitDataError(
            "Telegram initData has an invalid auth_date."
        ) from exc

    age = int(time.time()) - auth_date

    if age < -60:
        raise InitDataError(
            "Telegram initData appears to come from the future."
        )

    if age > max_age_seconds:
        raise InitDataError(
            "Telegram initData has expired. Reopen the Mini App."
        )

    user_raw = values.get("user")

    if not user_raw:
        raise InitDataError("Telegram initData contains no user.")

    try:
        user_data = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise InitDataError(
            "Telegram user data is malformed."
        ) from exc

    try:
        user_id = int(user_data["id"])
        first_name = str(user_data["first_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InitDataError(
            "Telegram user data is incomplete."
        ) from exc

    return TelegramUser(
        id=user_id,
        first_name=first_name,
        last_name=user_data.get("last_name"),
        username=user_data.get("username"),
        language_code=user_data.get("language_code"),
        is_premium=bool(user_data.get("is_premium", False)),
    )
