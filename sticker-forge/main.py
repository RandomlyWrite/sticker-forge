import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import Update

from bot import build_bot


telegram_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app

    token = os.environ.get("BOT_TOKEN")
    webhook_url = os.environ.get("WEBHOOK_URL")

    if not token:
        raise RuntimeError("BOT_TOKEN is missing from Render environment variables")

    if not webhook_url:
        raise RuntimeError("WEBHOOK_URL is missing from Render environment variables")

    telegram_app = build_bot()

    await telegram_app.initialize()
    await telegram_app.start()

    await telegram_app.bot.set_webhook(
        url=f"{webhook_url.rstrip('/')}/telegram"
    )

    yield

    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(
    title="Sticker Forge",
    version="0.4.0",
    lifespan=lifespan,
)


@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {
        "service": "Sticker Forge",
        "status": "running",
        "health": "/health",
    }


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "bot_ready": telegram_app is not None,
    }


@app.post("/telegram")
async def telegram_webhook(request: Request):
    if telegram_app is None:
        return {"ok": False, "error": "bot not initialized"}

    data = await request.json()
    update = Update.de_json(data=data, bot=telegram_app.bot)
    await telegram_app.process_update(update)

    return {"ok": True}
