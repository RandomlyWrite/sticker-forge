import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import Optional


def process_green_screen_to_sticker(
    input_video: str,
    output_webm: Optional[str] = None,
    target_size: int = 512,
    max_duration_s: float = 3.0,
    bitrate: str = "200K",
    similarity: float = 0.10,
    blend: float = 0.20,
    overwrite: bool = False,
    verbose: bool = False
) -> str:
    """
    Core function that converts a green-screen video into a Telegram-ready WebM sticker.
    """
    input_path = Path(input_video).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_path}")

    if output_webm is None:
        output_webm = str(input_path.with_suffix(".webm"))
    output_path = Path(output_webm).resolve()

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="sticker_forge_"))
    tmp_output = tmp_dir / "processed.webm"

    try:
        vf_filter = (
            f"chromakey=0x00FF00:{similarity}:{blend},"
            f"scale={target_size}:{target_size}:force_original_aspect_ratio=decrease,"
            f"fps=30"
        )

        cmd = [
            "ffmpeg", "-n",
            "-i", str(input_path),
            "-vf", vf_filter,
            "-c:v", "libvpx-vp9",
            "-b:v", bitrate,
            "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",
            "-an",
            "-t", str(max_duration_s),
            str(tmp_output)
        ]

        if verbose:
            print(f"[CMD] {' '.join(cmd)}")

        subprocess.run(cmd, check=True, capture_output=not verbose)

        # Verify the output file
        subprocess.run(["ffprobe", "-v", "error", str(tmp_output)], check=True)

        if output_path.exists() and overwrite:
            output_path.unlink()

        shutil.move(str(tmp_output), str(output_path))
        return str(output_path)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)