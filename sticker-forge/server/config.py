from __future__ import annotations

import os
import tempfile
from pathlib import Path


BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

PUBLIC_URL = os.environ.get(
    "PUBLIC_URL",
    "",
).strip().rstrip("/")

WORK_ROOT = os.environ.get(
    "WORK_ROOT",
    str(Path(tempfile.gettempdir()) / "sticker_forge_jobs"),
)

MAX_UPLOAD_MB = int(
    os.environ.get("MAX_UPLOAD_MB", "30")
)

DEFAULT_THEME = os.environ.get(
    "DEFAULT_THEME",
    "default",
).strip().lower()

STRICT_PREFLIGHT = (
    os.environ.get("STRICT_PREFLIGHT", "0") == "1"
)

SKIP_PREFLIGHT = (
    os.environ.get("SKIP_PREFLIGHT", "0") == "1"
)
