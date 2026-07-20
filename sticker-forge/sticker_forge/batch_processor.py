from pathlib import Path
from typing import Optional
from .video_processor import process_green_screen_to_sticker

def forge_sticker_set(
    input_folder: str,
    output_folder: str,
    theme: str = "default",
    overwrite: bool = False,
    verbose: bool = False
):
    """Batch process a folder of green-screen clips into Telegram stickers."""
    in_dir = Path(input_folder).resolve()
    out_dir = Path(output_folder).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    clips = sorted([p for p in in_dir.glob("*.mp4") if p.is_file()])
    if not clips:
        raise ValueError(f"No .mp4 files found in {in_dir}")

    processed = []
    for clip in clips:
        stem = clip.stem.replace("_green", "").replace("_greenscreen", "")
        out_webm = out_dir / f"{stem}.webm"

        result = process_green_screen_to_sticker(
            input_video=str(clip),
            output_webm=str(out_webm),
            overwrite=overwrite,
            verbose=verbose
        )
        processed.append(result)
        if verbose:
            print(f"Processed: {clip.name} → {out_webm.name}")

    return {
        "output_folder": str(out_dir),
        "stickers": processed,
        "count": len(processed)
    }
