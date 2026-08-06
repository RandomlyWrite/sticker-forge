from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


class StickerProcessingError(RuntimeError):
    """Raised when FFmpeg cannot create a valid sticker file."""


def _run_command(
    command: list[str],
    verbose: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        text=True,
        capture_output=not verbose,
    )

    if result.returncode != 0:
        stderr = result.stderr or "No FFmpeg error details returned."

        raise StickerProcessingError(
            stderr[-2000:]
        )

    return result


def process_green_screen_to_sticker(
    input_video: str,
    output_webm: Optional[str] = None,
    target_size: int = 512,
    max_duration_s: float = 3.0,
    bitrate: str = "200K",
    similarity: float = 0.26,
    blend: float = 0.08,
    overwrite: bool = False,
    verbose: bool = False,
) -> str:
    """
    Convert a green-screen video into a transparent VP9 WebM sticker.

    The result:
    - removes a variable green background
    - preserves transparency
    - fits inside a 512 × 512 transparent canvas
    - uses VP9 with no audio
    - is limited to 30 FPS and 3 seconds
    """

    input_path = Path(input_video).resolve()

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input video not found: {input_path}"
        )

    if not input_path.is_file():
        raise ValueError(
            f"Input path is not a file: {input_path}"
        )

    if output_webm is None:
        output_path = input_path.with_suffix(".webm")
    else:
        output_path = Path(output_webm).resolve()

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {output_path}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="sticker_forge_"
        )
    )

    temp_output = temp_dir / "processed.webm"

    try:
        filter_chain = ",".join(
            [
                # Convert to a format that carries an alpha channel.
                "format=rgba",

                # Remove green. Higher similarity handles compression,
                # shadows, and uneven lighting.
                (
                    f"chromakey="
                    f"color=0x00FF00:"
                    f"similarity={similarity}:"
                    f"blend={blend}"
                ),

                # Fit the subject inside Telegram's sticker canvas.
                (
                    f"scale="
                    f"{target_size}:{target_size}:"
                    f"force_original_aspect_ratio=decrease"
                ),

                # Add transparent padding instead of stretching the video.
                (
                    f"pad="
                    f"{target_size}:{target_size}:"
                    f"(ow-iw)/2:(oh-ih)/2:"
                    f"color=0x00000000"
                ),

                "fps=30",

                # Preserve alpha for the VP9 encoder.
                "format=yuva420p",
            ]
        )

        command = [
            "ffmpeg",
            "-y" if overwrite else "-n",
            "-i",
            str(input_path),
            "-t",
            str(max_duration_s),
            "-vf",
            filter_chain,
            "-an",
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-auto-alt-ref",
            "0",
            "-b:v",
            bitrate,
            "-deadline",
            "good",
            "-cpu-used",
            "4",
            "-metadata:s:v:0",
            "alpha_mode=1",
            str(temp_output),
        ]

        if verbose:
            print(
                "[FFMPEG]",
                " ".join(command),
            )

        _run_command(
            command,
            verbose=verbose,
        )

        if not temp_output.exists():
            raise StickerProcessingError(
                "FFmpeg finished without creating an output file."
            )

        if temp_output.stat().st_size == 0:
            raise StickerProcessingError(
                "FFmpeg created an empty output file."
            )

        probe_command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate",
            "-of",
            "default=noprint_wrappers=1",
            str(temp_output),
        ]

        _run_command(
            probe_command,
            verbose=verbose,
        )

        if output_path.exists():
            output_path.unlink()

        shutil.move(
            str(temp_output),
            str(output_path),
        )

        return str(output_path)

    finally:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )
