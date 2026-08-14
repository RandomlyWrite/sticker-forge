from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
WORK_ROOT = os.environ.get("WORK_ROOT", str(Path(tempfile.gettempdir()) / "sticker_forge_jobs"))
STATE_ROOT = os.environ.get("STATE_ROOT", str(Path(tempfile.gettempdir()) / "sticker_forge_state"))
MAX_UPLOAD_MB = max(1, int(os.environ.get("MAX_UPLOAD_MB", "50")))
MAX_UPLOAD_TOTAL_MB = max(MAX_UPLOAD_MB, int(os.environ.get("MAX_UPLOAD_TOTAL_MB", "200")))
DEFAULT_THEME = os.environ.get("DEFAULT_THEME", "default").strip().lower()
DEFAULT_KEY_MODE = os.environ.get("DEFAULT_KEY_MODE", "auto").strip().lower()
AUTO_CROP = os.environ.get("AUTO_CROP", "1") == "1"
SEND_DEBUG_WEBM = os.environ.get("SEND_DEBUG_WEBM", "1") == "1"
STRICT_PREFLIGHT = os.environ.get("STRICT_PREFLIGHT", "0") == "1"
SKIP_PREFLIGHT = os.environ.get("SKIP_PREFLIGHT", "0") == "1"
JOB_TTL_SECONDS = max(900, int(os.environ.get("JOB_TTL_SECONDS", "21600")))
WEBHOOK_PATH = "/telegram"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
if not WEBHOOK_SECRET and BOT_TOKEN:
    WEBHOOK_SECRET = hashlib.sha256(BOT_TOKEN.encode("utf-8")).hexdigest()[:32]

Path(WORK_ROOT).mkdir(parents=True, exist_ok=True)
Path(STATE_ROOT).mkdir(parents=True, exist_ok=True)
