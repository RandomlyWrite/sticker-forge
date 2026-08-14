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


def validate_init_data(init_data: str, max_age_seconds: int = MAX_INIT_DATA_AGE_SECONDS) -> TelegramUser:
    if not config.BOT_TOKEN:
        raise InitDataError("BOT_TOKEN is not configured")
    if not init_data:
        raise InitDataError("Telegram initData is missing. Open Sticker Forge from the bot")
    pairs = parse_qsl(init_data, keep_blank_values=True)
    values = dict(pairs)
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise InitDataError("Telegram initData has no hash")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise InitDataError("Telegram initData signature is invalid")
    try:
        auth_date = int(values.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataError("Telegram initData auth_date is invalid") from exc
    age = int(time.time()) - auth_date
    if age < -60:
        raise InitDataError("Telegram initData appears to come from the future")
    if age > max_age_seconds:
        raise InitDataError("Telegram initData expired. Reopen the Mini App")
    try:
        user_data = json.loads(values["user"])
        user_id = int(user_data["id"])
        first_name = str(user_data["first_name"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InitDataError("Telegram initData contains invalid user data") from exc
    return TelegramUser(
        id=user_id,
        first_name=first_name,
        last_name=user_data.get("last_name"),
        username=user_data.get("username"),
        language_code=user_data.get("language_code"),
        is_premium=bool(user_data.get("is_premium", False)),
    )


def _scoped_token(scope: str, job_id: str, user_id: int) -> str:
    key = (config.BOT_TOKEN or "sticker-forge").encode("utf-8")
    message = f"{scope}:{job_id}:{int(user_id)}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:32]


def preview_token(job_id: str, user_id: int) -> str:
    return _scoped_token("preview", job_id, user_id)


def verify_preview_token(token: str, job_id: str, user_id: int) -> bool:
    return hmac.compare_digest(str(token or ""), preview_token(job_id, user_id))


def job_token(job_id: str, user_id: int) -> str:
    return _scoped_token("job", job_id, user_id)


def verify_job_token(token: str, job_id: str, user_id: int) -> bool:
    return hmac.compare_digest(str(token or ""), job_token(job_id, user_id))
