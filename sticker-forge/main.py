from __future__ import annotations

import logging
import os
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from server import config
from server.bot import run_bot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("sticker_forge.main")

app = FastAPI(
    title="Sticker Forge",
    version="0.4.0",
)

_bot_thread: threading.Thread | None = None


@app.on_event("startup")
def startup() -> None:
    global _bot_thread

    if _bot_thread and _bot_thread.is_alive():
        return

    _bot_thread = threading.Thread(
        target=run_bot,
        name="telegram-bot-poller",
        daemon=True,
    )
    _bot_thread.start()

    log.info("Telegram bot polling thread started.")


@app.get("/")
def mini_app() -> FileResponse:
    return FileResponse(
        path="miniapp/index.html",
        media_type="text/html",
    )


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return JSONResponse(
        {
            "status": "ok",
            "service": "sticker-forge",
            "bot_configured": bool(config.BOT_TOKEN),
            "public_url": config.PUBLIC_URL or None,
            "bot_mode": "polling",
            "miniapp": "/",
        }
    )


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
    )
