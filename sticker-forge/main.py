import os

import uvicorn
from fastapi import FastAPI

app = FastAPI(
    title="Sticker Forge",
    version="0.4.0",
)


@app.get("/")
def root() -> dict:
    return {
        "service": "Sticker Forge",
        "status": "running",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ffmpeg_required": True,
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
