import os

import uvicorn
from fastapi import FastAPI, Response

app = FastAPI(
    title="Sticker Forge",
    version="0.4.0",
)


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {
        "service": "Sticker Forge",
        "status": "running",
        "health": "/health",
    }


@app.api_route("/health", methods=["GET", "HEAD"])
def health():
    return {
        "status": "ok",
        "service": "sticker-forge",
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )
