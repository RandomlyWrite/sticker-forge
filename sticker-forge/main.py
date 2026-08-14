from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from server import config, prefs
from server.auth import InitDataError, job_token, validate_init_data, verify_job_token, verify_preview_token
from server.bot import configure_webhook, get_webhook_info, handle_update
from server.jobs import STORE
from server.pipeline import encode_preview, publish_preview_job, retry_preview_clip
from server.preflight import run_preflight
from server.sets_store import list_sets

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("sticker_forge.main")
_PREFLIGHT: dict = {}


def _auth(init_data: str):
    try:
        return validate_init_data(init_data)
    except InitDataError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _cleanup_old_jobs() -> None:
    for old in STORE.purge(config.JOB_TTL_SECONDS):
        if old.work_dir:
            shutil.rmtree(old.work_dir, ignore_errors=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _PREFLIGHT
    _PREFLIGHT = run_preflight()
    for item in _PREFLIGHT.get("checks", []):
        marker = "â" if item["status"] == "ok" else ("!" if item["status"] == "warn" else "â")
        log.info("preflight %s %-12s %s", marker, item["name"], item["detail"])
    if config.STRICT_PREFLIGHT and not _PREFLIGHT.get("ok", False):
        raise RuntimeError("STRICT_PREFLIGHT enabled and startup checks failed")
    try:
        configure_webhook()
    except Exception:
        log.exception("Webhook/menu-button configuration failed")
        if config.STRICT_PREFLIGHT:
            raise
    yield


app = FastAPI(title="Sticker Forge", version="1.0.0", lifespan=lifespan)


@app.get("/")
def mini_app() -> FileResponse:
    return FileResponse("miniapp/index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@app.api_route("/health", methods=["GET", "HEAD"])
def health(refresh: int = Query(0)):
    global _PREFLIGHT
    if refresh:
        _PREFLIGHT = run_preflight()
    webhook = {}
    if config.BOT_TOKEN:
        try:
            info = get_webhook_info()
            webhook = {
                "url": info.get("url"),
                "pending_update_count": info.get("pending_update_count", 0),
                "last_error_message": info.get("last_error_message"),
            }
        except Exception as exc:
            webhook = {"error": str(exc)}
    return JSONResponse({
        "status": "ok" if _PREFLIGHT.get("ok", True) else "degraded",
        "service": "sticker-forge",
        "public_url": config.PUBLIC_URL or None,
        "bot_configured": bool(config.BOT_TOKEN),
        "bot_mode": "webhook",
        "miniapp": "/",
        "preflight": _PREFLIGHT,
        "webhook": webhook,
    })


@app.post(config.WEBHOOK_PATH)
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    if config.WEBHOOK_SECRET:
        received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(received, config.WEBHOOK_SECRET):
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")
    try:
        update = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from exc
    background_tasks.add_task(handle_update, update)
    return {"ok": True}


@app.post("/api/prefs")
def api_prefs(init_data: str = Form(...), theme: str | None = Form(None)):
    user = _auth(init_data)
    if theme is not None:
        selected = prefs.set_theme_pref(user.id, theme)
    else:
        selected = prefs.get_theme_pref(user.id)
    return {"theme": selected}


@app.post("/api/sets")
def api_sets(init_data: str = Form(...)):
    user = _auth(init_data)
    return {"sets": [{"name": s.name, "title": s.title, "count": s.count} for s in list_sets(user.id)]}


@app.post("/api/forge")
async def api_forge(
    init_data: str = Form(...),
    title: str = Form("My Stickers"),
    theme: str = Form("default"),
    key_mode: str = Form("auto"),
    files: list[UploadFile] = File(...),
):
    user = _auth(init_data)
    _cleanup_old_jobs()
    if not files:
        raise HTTPException(status_code=400, detail="Choose at least one video")
    if len(files) > 120:
        raise HTTPException(status_code=400, detail="Upload at most 120 clips at a time")

    staging = Path(tempfile.mkdtemp(prefix="sf_upload_", dir=config.WORK_ROOT))
    saved: list[str] = []
    names: list[str] = []
    total = 0
    per_limit = config.MAX_UPLOAD_MB * 1024 * 1024
    total_limit = config.MAX_UPLOAD_TOTAL_MB * 1024 * 1024
    try:
        for idx, upload in enumerate(files):
            original = Path(upload.filename or f"clip_{idx}.mp4").name
            suffix = Path(original).suffix.lower()
            if suffix not in {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}:
                suffix = ".mp4"
            path = staging / f"clip_{idx:03d}{suffix}"
            size = 0
            with path.open("wb") as fh:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > per_limit:
                        raise HTTPException(status_code=413, detail=f"{original} exceeds {config.MAX_UPLOAD_MB} MB")
                    if total > total_limit:
                        raise HTTPException(status_code=413, detail=f"Upload exceeds {config.MAX_UPLOAD_TOTAL_MB} MB total")
                    fh.write(chunk)
            if size == 0:
                raise HTTPException(status_code=400, detail=f"{original} is empty")
            saved.append(str(path))
            names.append(original)
        prefs.set_theme_pref(user.id, theme)
        job = STORE.submit(
            encode_preview,
            saved,
            names,
            user.id,
            theme,
            title,
            key_mode,
            owner_id=user.id,
        )
        return {"job_id": job.id, "job_token": job_token(job.id, user.id)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, token: str = Query(...)):
    job = STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if not verify_job_token(token, job.id, job.owner_id):
        raise HTTPException(status_code=403, detail="Invalid job token")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/preview/{idx}")
def api_preview(job_id: str, idx: int, token: str = Query(...)):
    job = STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if not verify_preview_token(token, job.id, job.owner_id):
        raise HTTPException(status_code=403, detail="Invalid preview token")
    if idx < 0 or idx >= len(job.clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = job.clips[idx]
    if clip.get("status") != "ok":
        raise HTTPException(status_code=404, detail="Clip did not encode successfully")
    path = Path(clip.get("_output_path") or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preview expired")
    return FileResponse(path, media_type="video/webm", headers={"Cache-Control": "private, no-store"})


@app.get("/api/jobs/{job_id}/thumbnail/{idx}")
def api_thumbnail(job_id: str, idx: int, token: str = Query(...)):
    job = STORE.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    if not verify_preview_token(token, job.id, job.owner_id):
        raise HTTPException(status_code=403, detail="Invalid preview token")
    if idx < 0 or idx >= len(job.clips):
        raise HTTPException(status_code=404, detail="Clip not found")
    clip = job.clips[idx]
    if clip.get("status") != "ok":
        raise HTTPException(status_code=404, detail="Clip did not encode successfully")
    path = Path(clip.get("_thumbnail_path") or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail expired")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@app.post("/api/retry")
def api_retry(
    init_data: str = Form(...),
    job_id: str = Form(...),
    idx: int = Form(...),
    key_mode: str | None = Form(None),
):
    user = _auth(init_data)
    try:
        job = STORE.require_owned(job_id, user.id)
        return retry_preview_clip(job, idx, key_mode=key_mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found or expired") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, IndexError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/publish")
def api_publish(
    init_data: str = Form(...),
    job_id: str = Form(...),
    order: str = Form(...),
    set_name: str | None = Form(None),
):
    user = _auth(init_data)
    try:
        source = STORE.require_owned(job_id, user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found or expired") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        parsed = json.loads(order)
        if not isinstance(parsed, list) or not all(isinstance(x, int) for x in parsed):
            raise ValueError
    except Exception as exc:
        raise HTTPException(status_code=400, detail="order must be a JSON list of clip indices") from exc
    publish_job = STORE.submit(
        publish_preview_job,
        source.id,
        user.id,
        parsed,
        set_name or None,
        owner_id=user.id,
    )
    return {"job_id": publish_job.id, "job_token": job_token(publish_job.id, user.id)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
