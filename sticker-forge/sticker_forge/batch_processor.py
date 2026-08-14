from __future__ import annotations

from pathlib import Path

from .video_processor import process_green_screen_to_sticker

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}


def forge_sticker_set(
    input_folder: str,
    output_folder: str,
    theme: str = "default",
    overwrite: bool = False,
    verbose: bool = False,
    key_mode: str = "auto",
):
    """Batch-process supported video clips and surface per-clip failures."""
    in_dir = Path(input_folder).resolve()
    out_dir = Path(output_folder).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    clips = sorted(p for p in in_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if not clips:
        raise ValueError(f"No supported video files found in {in_dir}")
    processed: list[str] = []
    failed: list[dict] = []
    for clip in clips:
        stem = clip.stem.replace("_green", "").replace("_greenscreen", "")
        out_webm = out_dir / f"{stem}.webm"
        try:
            result = process_green_screen_to_sticker(
                input_video=str(clip),
                output_webm=str(out_webm),
                overwrite=overwrite,
                verbose=verbose,
                key_mode=key_mode,
            )
            processed.append(result)
        except Exception as exc:
            failed.append({"name": clip.name, "error": f"{type(exc).__name__}: {exc}"})
        if verbose:
            print(f"Processed: {clip.name} -> {out_webm.name}" if out_webm.exists() else f"Skipped: {clip.name}")
    return {
        "output_folder": str(out_dir),
        "stickers": processed,
        "count": len(processed),
        "failed": failed,
    }
