from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

import requests

from sticker_forge.telegram_uploader import SET_CAP, StickerUploader
from sticker_forge.video_processor import extract_alpha_preview_png, inspect_video, process_green_screen_to_sticker, validate_telegram_webm

from . import config, prefs
from .jobs import STORE, Job
from .auth import preview_token
from .sets_store import get_set, save_set, update_set_count

log = logging.getLogger("sticker_forge.pipeline")
SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def _redact(value: object) -> str:
    text = str(value)
    return text.replace(config.BOT_TOKEN, "<bot-token>") if config.BOT_TOKEN else text


def _telegram(method: str, *, data: Optional[dict] = None, files: Optional[dict] = None, timeout: int = 90):
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    try:
        if files:
            response = requests.post(url, data=data or {}, files=files, timeout=timeout)
        else:
            response = requests.post(url, json=data or {}, timeout=timeout)
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram network error on {method}: {_redact(exc)}") from exc
    except ValueError as exc:
        raise RuntimeError(f"Telegram returned invalid JSON on {method}") from exc
    if not response.ok or not payload.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {payload.get('description', response.text[:300])}")
    return payload["result"]


def dm(chat_id: int, text: str) -> None:
    _telegram("sendMessage", data={"chat_id": int(chat_id), "text": text, "disable_web_page_preview": False})


def dm_document(chat_id: int, path: Path, caption: str = "") -> None:
    with path.open("rb") as fh:
        _telegram(
            "sendDocument",
            data={"chat_id": int(chat_id), "caption": caption[:1024]},
            files={"document": (path.name, fh, "video/webm")},
            timeout=120,
        )


def _safe_title(value: str) -> str:
    return " ".join((value or "").strip().split())[:64] or "My Stickers"


def _safe_base(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip()).strip("_")
    return cleaned or "sticker_forge"


def _job_root(job: Job) -> Path:
    root = Path(config.WORK_ROOT) / job.id
    root.mkdir(parents=True, exist_ok=True)
    job.work_dir = str(root)
    return root


def _select_theme(theme: str) -> str:
    return theme if theme in {"default", "degen"} else config.DEFAULT_THEME


def _select_key_mode(mode: str | None) -> str:
    mode = (mode or config.DEFAULT_KEY_MODE).strip().lower()
    return mode if mode in {"auto", "gentle", "strong"} else "auto"


def encode_preview(
    input_files: list[str],
    original_names: list[str],
    user_id: int,
    theme: str,
    title: str,
    key_mode: str = "auto",
    clip_edits: dict[int, dict] | None = None,
    job: Job | None = None,
) -> dict:
    if job is None:
        raise RuntimeError("encode_preview requires a job")
    if not input_files:
        raise ValueError("No clips uploaded")
    root = _job_root(job)
    source_dir = root / "source"
    output_dir = root / "preview"
    source_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    selected_theme = _select_theme(theme)
    key_mode = _select_key_mode(key_mode)
    title = _safe_title(title)
    prefs.set_theme_pref(user_id, selected_theme)
    clips: list[dict] = []

    job.phase = "preview"
    job.stage = "preparing clips"
    job.result.update({"title": title, "theme": selected_theme, "key_mode": key_mode})

    try:
        for idx, source_name in enumerate(input_files):
            incoming = Path(source_name)
            display_name = original_names[idx] if idx < len(original_names) else incoming.name
            suffix = incoming.suffix.lower() if incoming.suffix.lower() in SUPPORTED_EXTENSIONS else ".mp4"
            source = source_dir / f"clip_{idx:03d}{suffix}"
            shutil.move(str(incoming), source)
            output = output_dir / f"clip_{idx:03d}.webm"
            thumbnail = output_dir / f"clip_{idx:03d}.png"
            job.stage = f"encoding clip {idx + 1}/{len(input_files)}"
            job.touch()
            clip = {
                "idx": idx,
                "name": display_name,
                "status": "encoding",
                "error": None,
                "preview": None,
                "_source_path": str(source),
                "_output_path": str(output),
                "_thumbnail_path": str(thumbnail),
            }
            clips.append(clip)
            job.clips = clips
            try:
                edit = (clip_edits or {}).get(idx, {})
                try:
                    src_duration = inspect_video(source)["duration"]
                except Exception:
                    src_duration = 0.0
                process_green_screen_to_sticker(
                    str(source),
                    str(output),
                    overwrite=True,
                    key_mode=key_mode,
                    auto_crop=config.AUTO_CROP,
                    clip_start=edit.get("loop_start", 0.0),
                    clip_end=edit.get("loop_end"),
                    loop_mode=edit.get("loop_mode", "trim"),
                )
                info = validate_telegram_webm(output)
                extract_alpha_preview_png(output, thumbnail)
                token = preview_token(job.id, user_id)
                clip.update({
                    "status": "ok",
                    "preview": f"/api/jobs/{job.id}/preview/{idx}?token={token}",
                    "thumbnail": f"/api/jobs/{job.id}/thumbnail/{idx}?token={token}",
                    "size_kb": round(output.stat().st_size / 1024, 1),
                    "width": info["width"],
                    "height": info["height"],
                    "alpha_fraction": round(float(info["alpha"]["transparent_fraction"]), 4),
                    "duration_s": round(float(info["duration"]), 2),
                    "fps": round(float(info["fps"]), 1),
                    "alpha_ok": float(info["alpha"]["transparent_fraction"]) >= 0.005,
                    "telegram_ready": True,
                    "source_duration": round(float(src_duration), 2),
                    "_loop_edit": {
                        "loop_start": edit.get("loop_start", 0.0),
                        "loop_end": edit.get("loop_end"),
                        "loop_mode": edit.get("loop_mode", "trim"),
                    },
                })
            except Exception as exc:
                log.exception("Preview encode failed for clip %s", idx)
                output.unlink(missing_ok=True)
                thumbnail.unlink(missing_ok=True)
                clip.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            job.touch()
    finally:
        # Staging folder containing the moved inputs can now disappear.
        parents = {str(Path(p).parent) for p in input_files}
        for parent in parents:
            try:
                shutil.rmtree(parent, ignore_errors=True)
            except Exception:
                pass

    ok_count = sum(1 for c in clips if c["status"] == "ok")
    job.stage = "preview ready" if ok_count else "all clips need attention"
    return {
        "phase": "preview",
        "stage": job.stage,
        "clips": clips,
        "count": ok_count,
        "failed_count": len(clips) - ok_count,
        "title": title,
        "theme": selected_theme,
        "key_mode": key_mode,
    }


def retry_preview_clip(
    job: Job,
    idx: int,
    key_mode: str | None = None,
    loop_start: float | None = None,
    loop_end: float | None = None,
    loop_mode: str | None = None,
) -> dict:
    if job.phase != "preview":
        raise ValueError("Only preview jobs can be retried")
    if idx < 0 or idx >= len(job.clips):
        raise IndexError("Clip index is out of range")
    clip = job.clips[idx]
    source = Path(clip["_source_path"])
    output = Path(clip["_output_path"])
    thumbnail = Path(clip.get("_thumbnail_path") or output.with_suffix(".png"))
    clip["_thumbnail_path"] = str(thumbnail)
    if not source.exists():
        raise FileNotFoundError("Original clip has already been cleaned up")
    mode = _select_key_mode(key_mode or job.result.get("key_mode"))
    # Loop edits persist per-clip: an explicit arg overrides the stored edit,
    # otherwise reuse whatever was applied last time (reorder/retry must never
    # silently drop or cross-contaminate a clip's trim settings).
    stored = clip.get("_loop_edit", {})
    edit = {
        "loop_start": loop_start if loop_start is not None else stored.get("loop_start", 0.0),
        "loop_end": loop_end if loop_end is not None else stored.get("loop_end"),
        "loop_mode": loop_mode if loop_mode is not None else stored.get("loop_mode", "trim"),
    }
    try:
        src_duration = inspect_video(source)["duration"]
    except Exception:
        src_duration = 0.0
    clip.update({"status": "encoding", "error": None, "preview": None})
    job.status = "running"
    job.stage = f"retrying clip {idx + 1}"
    job.touch()
    try:
        process_green_screen_to_sticker(
            str(source), str(output), overwrite=True, key_mode=mode, auto_crop=config.AUTO_CROP,
            clip_start=edit["loop_start"], clip_end=edit["loop_end"], loop_mode=edit["loop_mode"],
        )
        info = validate_telegram_webm(output)
        extract_alpha_preview_png(output, thumbnail)
        token = preview_token(job.id, job.owner_id)
        version = int(job.updated_at)
        clip.update({
            "status": "ok",
            "preview": f"/api/jobs/{job.id}/preview/{idx}?token={token}&v={version}",
            "thumbnail": f"/api/jobs/{job.id}/thumbnail/{idx}?token={token}&v={version}",
            "size_kb": round(output.stat().st_size / 1024, 1),
            "width": info["width"],
            "height": info["height"],
            "alpha_fraction": round(float(info["alpha"]["transparent_fraction"]), 4),
            "duration_s": round(float(info["duration"]), 2),
            "fps": round(float(info["fps"]), 1),
            "alpha_ok": float(info["alpha"]["transparent_fraction"]) >= 0.005,
            "telegram_ready": True,
            "source_duration": round(float(src_duration), 2),
            "_loop_edit": edit,
        })
    except Exception as exc:
        output.unlink(missing_ok=True)
        thumbnail.unlink(missing_ok=True)
        clip.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        job.status = "done"
        job.stage = "preview ready"
        job.touch()
    return {k: v for k, v in clip.items() if not k.startswith("_")}


def _record_links(user_id: int, links: list[dict]) -> None:
    for link in links:
        total = int(link.get("total_count") or link.get("count") or 0)
        save_set(user_id, str(link["name"]), str(link.get("title") or "Sticker Set"), total)


def publish_preview_job(
    source_job_id: str,
    user_id: int,
    order: list[int],
    set_name: Optional[str],
    emojis: Optional[dict[int, str]] = None,
    job: Job | None = None,
) -> dict:
    if job is None:
        raise RuntimeError("publish_preview_job requires a job")
    source = STORE.require_owned(source_job_id, user_id)
    if source.phase != "preview":
        raise ValueError("Source job is not a preview")
    if not order:
        raise ValueError("Select at least one successful clip")
    if len(order) != len(set(order)):
        raise ValueError("Order contains duplicate clip indices")
    paths: list[Path] = []
    for idx in order:
        if idx < 0 or idx >= len(source.clips):
            raise ValueError(f"Invalid clip index: {idx}")
        clip = source.clips[idx]
        if clip.get("status") != "ok":
            raise ValueError(f"Clip {idx} is not successfully encoded")
        path = Path(clip["_output_path"])
        if not path.exists():
            raise FileNotFoundError(f"Preview file for clip {idx} is missing")
        paths.append(path)

    title = _safe_title(source.result.get("title") or "My Stickers")
    theme = _select_theme(source.result.get("theme") or config.DEFAULT_THEME)
    uploader = StickerUploader(config.BOT_TOKEN, user_id, theme)
    if set_name and get_set(user_id, set_name) is None:
        if not uploader.owns_generated_set_name(set_name):
            raise PermissionError("That sticker set is not recognized as one created for your Telegram account")
        # Verify the recovered set still exists before attempting to append to it.
        uploader.get_set(set_name)

    job.phase = "publish"
    job.stage = "publishing to Telegram"
    emoji_list = [(emojis or {}).get(idx) or "🔥" for idx in order] if emojis else None
    links = uploader.publish_with_spillover(
        paths,
        set_name_base=_safe_base(title),
        set_title=title,
        existing_set_name=set_name,
        emojis=emoji_list,
    )
    _record_links(user_id, links)
    total = sum(int(link.get("count", 0)) for link in links)
    job.links = links
    job.stage = "complete"

    text = "Sticker Forge finished ✅\n\n" + "\n".join(f"{x.get('title', 'Sticker set')}: {x['link']}" for x in links)
    try:
        dm(user_id, text)
    except Exception:
        log.exception("Could not DM Mini App publish result")
    return {"phase": "published", "stage": "complete", "links": links, "link": links[0]["link"], "count": total}


def forge_and_upload(
    input_files: list[str],
    user_id: int,
    theme: str,
    title: str,
    chat_id: Optional[int] = None,
    emojis: Optional[list[str]] = None,
    key_mode: str | None = None,
    job: Job | None = None,
) -> dict:
    if job is None:
        raise RuntimeError("forge_and_upload requires a job")
    if not input_files:
        raise ValueError("No input files supplied")
    selected_theme = _select_theme(theme)
    mode = _select_key_mode(key_mode)
    title = _safe_title(title)
    root = _job_root(job)
    output_dir = root / "output"
    output_dir.mkdir(exist_ok=True)
    successful: list[Path] = []
    failed: list[str] = []
    job.phase = "publish"

    try:
        for idx, name in enumerate(input_files):
            source = Path(name)
            output = output_dir / f"clip_{idx:03d}.webm"
            thumbnail = output_dir / f"clip_{idx:03d}.png"
            job.stage = f"forging clip {idx + 1}/{len(input_files)}"
            job.touch()
            try:
                process_green_screen_to_sticker(
                    str(source), str(output), overwrite=True, key_mode=mode, auto_crop=config.AUTO_CROP,
                )
                validate_telegram_webm(output)
                successful.append(output)
            except Exception as exc:
                failed.append(f"{source.name}: {type(exc).__name__}: {exc}")

        if not successful:
            message = "No clips could be forged.\n\n" + "\n".join(failed[:5])
            if chat_id:
                try:
                    dm(chat_id, message)
                except Exception:
                    pass
            raise RuntimeError(message)

        job.stage = "creating sticker set"
        uploader = StickerUploader(config.BOT_TOKEN, user_id, selected_theme)
        links = uploader.publish_with_spillover(
            successful,
            set_name_base=_safe_base(title),
            set_title=title,
            emojis=emojis,
        )
        _record_links(user_id, links)
        total = sum(int(link.get("count", 0)) for link in links)
        job.links = links

        if chat_id:
            lines = ["Sticker set forged ✅", ""]
            lines.extend(link["link"] for link in links)
            if failed:
                lines.extend(["", f"⚠️ {len(failed)} clip(s) skipped:", *failed[:5]])
            dm(chat_id, "\n".join(lines))
            if config.SEND_DEBUG_WEBM:
                for idx, file in enumerate(successful):
                    try:
                        dm_document(chat_id, file, f"🔬 Debug WebM {idx + 1}/{len(successful)}")
                    except Exception:
                        log.exception("Could not send debug WebM")

        return {
            "phase": "published",
            "stage": "complete",
            "links": links,
            "link": links[0]["link"],
            "count": total,
            "failed": failed,
        }
    finally:
        for source in input_files:
            try:
                Path(source).unlink(missing_ok=True)
            except Exception:
                pass
        # Bot jobs don't need previews after delivery.
        shutil.rmtree(root, ignore_errors=True)
