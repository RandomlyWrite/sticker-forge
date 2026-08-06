import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telegram import Update

from bot import build_bot


telegram_app = build_bot()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()

    webhook_url = os.environ.get("WEBHOOK_URL")

    if webhook_url:
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
        "bot_initialized": telegram_app.bot is not None,
    }


@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()

    update = Update.de_json(
        data=data,
        bot=telegram_app.bot,
    )

    await telegram_app.process_update(update)

    return {"ok": True}
