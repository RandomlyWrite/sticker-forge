from __future__ import annotations

import json
import math
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageFilter

TELEGRAM_MAX_BYTES = 256 * 1024
TELEGRAM_MAX_DURATION = 3.0
TELEGRAM_MAX_FPS = 30.0
TELEGRAM_MAX_SIDE = 512

LOOP_MODES = {"trim", "ping_pong", "speed_fit"}


class StickerProcessingError(RuntimeError):
    pass


class StickerValidationError(ValueError):
    pass


def _run(command: list[str], *, verbose: bool = False, binary: bool = False):
    result = subprocess.run(
        command,
        capture_output=not verbose,
        text=not binary,
    )
    if result.returncode != 0:
        stderr = result.stderr if isinstance(result.stderr, str) else ""
        raise StickerProcessingError(stderr[-3000:] or f"Command failed: {' '.join(command)}")
    return result


def _ffprobe_json(path: str | Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise StickerValidationError(result.stderr[-2000:] or "ffprobe failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise StickerValidationError("ffprobe returned invalid JSON") from exc


def _fps(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    try:
        num, den = value.split("/", 1)
        return float(num) / float(den)
    except Exception:
        return 0.0


def inspect_video(path: str | Path) -> dict:
    p = Path(path)
    data = _ffprobe_json(p)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise StickerValidationError("No video stream found")
    duration = video.get("duration") or data.get("format", {}).get("duration") or 0
    try:
        duration_f = float(duration)
    except Exception:
        duration_f = 0.0
    return {
        "codec": video.get("codec_name"),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "duration": duration_f,
        "has_audio": audio is not None,
        "pix_fmt": video.get("pix_fmt"),
        "alpha_mode": str((video.get("tags") or {}).get("ALPHA_MODE") or (video.get("tags") or {}).get("alpha_mode") or ""),
        "bytes": p.stat().st_size if p.exists() else 0,
    }


def _alpha_stats(path: str | Path, sample_fps: int = 2) -> dict:
    # Decode a few frames to RGBA. WebM/VP9 alpha is exposed by the decoder even
    # when ffprobe reports a yuv420p pixel format for the stream.
    cmd = [
        "ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", str(path),
        "-vf", f"fps={sample_fps},scale=96:-2",
        "-t", "3", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return {"transparent_fraction": 0.0, "min_alpha": 255, "max_alpha": 255}
    data = result.stdout
    alphas = data[3::4]
    if not alphas:
        return {"transparent_fraction": 0.0, "min_alpha": 255, "max_alpha": 255}
    transparent = sum(1 for a in alphas if a < 245)
    return {
        "transparent_fraction": transparent / len(alphas),
        "min_alpha": min(alphas),
        "max_alpha": max(alphas),
    }


def validate_telegram_webm(path: str | Path, max_bytes: int = TELEGRAM_MAX_BYTES) -> dict:
    info = inspect_video(path)
    errors: list[str] = []
    if info["bytes"] > max_bytes:
        errors.append(f"file is {info['bytes'] / 1024:.1f} KB; Telegram limit is {max_bytes / 1024:.0f} KB")
    if info["codec"] != "vp9":
        errors.append(f"codec is {info['codec']!r}; expected VP9")
    if info["duration"] > TELEGRAM_MAX_DURATION + 0.08:
        errors.append(f"duration is {info['duration']:.3f}s; maximum is 3s")
    if info["fps"] > TELEGRAM_MAX_FPS + 0.2:
        errors.append(f"frame rate is {info['fps']:.2f}; maximum is 30 FPS")
    if info["has_audio"]:
        errors.append("audio stream is present")
    w, h = info["width"], info["height"]
    if not w or not h or w > 512 or h > 512 or max(w, h) != 512:
        errors.append(f"dimensions are {w}x{h}; one side must be exactly 512 and the other <= 512")
    alpha = _alpha_stats(path)
    info["alpha"] = alpha
    if alpha["transparent_fraction"] < 0.005:
        errors.append("output contains almost no transparent pixels; chroma key likely failed")
    if errors:
        raise StickerValidationError("; ".join(errors))
    return info


def _sample_frames(input_path: Path, temp_dir: Path, max_duration: float, start: float = 0.0) -> list[Path]:
    pattern = temp_dir / "sample_%02d.png"
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{max(0.0, start):.3f}", "-i", str(input_path),
        "-t", str(min(max_duration, 3.0)),
        "-vf", "fps=2,scale=192:-2:flags=area",
        "-frames:v", "6", str(pattern),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return sorted(temp_dir.glob("sample_*.png"))


def _border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    band_x = max(2, int(w * 0.10))
    band_y = max(2, int(h * 0.10))
    pixels: list[tuple[int, int, int]] = []
    for y in range(h):
        for x in range(w):
            if x < band_x or x >= w - band_x or y < band_y or y >= h - band_y:
                r, g, b = rgb.getpixel((x, y))
                if g >= 55 and g > r * 1.10 and g > b * 1.08:
                    pixels.append((r, g, b))
    return pixels


def _estimate_key(frames: list[Path], mode: str) -> tuple[str, float, float]:
    samples: list[tuple[int, int, int]] = []
    for frame in frames:
        try:
            samples.extend(_border_pixels(Image.open(frame)))
        except Exception:
            pass
    if len(samples) < 100:
        # Fallback to classic digital green.
        return "0x00FF00", {"gentle": 0.16, "strong": 0.30}.get(mode, 0.23), 0.035

    # Median is robust to a foreground object briefly touching a corner.
    r = int(statistics.median(v[0] for v in samples))
    g = int(statistics.median(v[1] for v in samples))
    b = int(statistics.median(v[2] for v in samples))
    key = f"0x{r:02X}{g:02X}{b:02X}"

    distances = [math.sqrt((rr-r)**2 + (gg-g)**2 + (bb-b)**2) / 441.673 for rr, gg, bb in samples]
    distances.sort()
    p90 = distances[int(0.90 * (len(distances)-1))]
    auto_similarity = min(0.28, max(0.16, p90 * 1.35 + 0.055))
    if mode == "gentle":
        similarity = min(auto_similarity, 0.18)
        blend = 0.025
    elif mode == "strong":
        similarity = max(auto_similarity, 0.30)
        blend = 0.045
    else:
        similarity = auto_similarity
        blend = 0.035
    return key, similarity, blend


def _foreground_green_risk(frames: list[Path], key_rgb: tuple[int, int, int], similarity: float) -> float:
    """Estimate how much *foreground* is genuinely green.

    A high value means aggressive despill would discolor the subject (money,
    green clothing, plants, etc.), so Auto mode backs off edge neutralization.
    """
    kr, kg, kb = key_rgb
    key_threshold = similarity * 441.673 * 1.05
    foreground = 0
    green_foreground = 0
    for frame in frames:
        try:
            im = Image.open(frame).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        x0, x1 = int(w * 0.15), int(w * 0.85)
        y0, y1 = int(h * 0.15), int(h * 0.85)
        px = im.load()
        for y in range(y0, y1, 2):
            for x in range(x0, x1, 2):
                r, g, b = px[x, y]
                dist = math.sqrt((r-kr)**2 + (g-kg)**2 + (b-kb)**2)
                if dist <= key_threshold:
                    continue
                foreground += 1
                if g >= 50 and g > r * 1.12 and g > b * 1.08:
                    green_foreground += 1
    return green_foreground / foreground if foreground else 0.0


def _estimate_crop(frames: list[Path], key_rgb: tuple[int, int, int], similarity: float) -> Optional[tuple[float, float, float, float]]:
    # Return a normalized union crop (x, y, w, h). This trims empty green space
    # while leaving generous motion padding. If confidence is low, don't crop.
    boxes = []
    threshold = similarity * 441.673 * 0.92
    kr, kg, kb = key_rgb
    for frame in frames:
        try:
            im = Image.open(frame).convert("RGB")
        except Exception:
            continue
        w, h = im.size
        mask = Image.new("L", (w, h), 0)
        mp = mask.load()
        px = im.load()
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                dist = math.sqrt((r-kr)**2 + (g-kg)**2 + (b-kb)**2)
                # Foreground = sufficiently different from key. Suppress very dark
                # compression noise near frame borders with a tiny morphology pass.
                if dist > threshold:
                    mp[x, y] = 255
        mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(5))
        bbox = mask.getbbox()
        if bbox:
            boxes.append((bbox, w, h))
    if not boxes:
        return None
    left = min(b[0][0] / b[1] for b in boxes)
    top = min(b[0][1] / b[2] for b in boxes)
    right = max(b[0][2] / b[1] for b in boxes)
    bottom = max(b[0][3] / b[2] for b in boxes)
    # 6% breathing room around union bbox.
    pad_x = max(0.025, (right-left) * 0.06)
    pad_y = max(0.025, (bottom-top) * 0.06)
    left = max(0.0, left - pad_x)
    top = max(0.0, top - pad_y)
    right = min(1.0, right + pad_x)
    bottom = min(1.0, bottom + pad_y)
    if (right-left) > 0.96 and (bottom-top) > 0.96:
        return None
    return left, top, right-left, bottom-top


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lower().replace("0x", "").replace("#", "")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def extract_alpha_preview_png(webm_path: str | Path, output_png: str | Path, at_seconds: float = 0.5) -> str:
    """Decode a representative transparent PNG using libvpx's alpha-aware decoder."""
    source = Path(webm_path).resolve()
    target = Path(output_png).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    attempts = [max(0.0, float(at_seconds)), 0.0]
    last_error: Exception | None = None
    for position in attempts:
        command = [
            "ffmpeg", "-v", "error", "-y", "-c:v", "libvpx-vp9", "-i", str(source),
            "-ss", f"{position:.3f}", "-frames:v", "1", "-pix_fmt", "rgba", str(target),
        ]
        try:
            _run(command)
            if target.is_file() and target.stat().st_size > 0:
                return str(target)
        except Exception as exc:
            last_error = exc
    raise StickerProcessingError(f"Could not create transparent preview PNG: {last_error}")


def _resolve_window(
    source_duration: float,
    clip_start: float,
    clip_end: Optional[float],
    loop_mode: str,
) -> tuple[float, float, float]:
    """Validate and normalize a clip selection. Raises StickerValidationError
    on any of the weird-case inputs (start>end, zero-length, beyond duration)."""
    clip_start = max(0.0, float(clip_start or 0.0))
    if source_duration > 0 and clip_start >= source_duration:
        raise StickerValidationError(
            f"clip_start ({clip_start:.2f}s) is beyond the source duration ({source_duration:.2f}s)"
        )
    if clip_end is None:
        clip_end = min(source_duration, clip_start + TELEGRAM_MAX_DURATION) if source_duration > 0 else clip_start + TELEGRAM_MAX_DURATION
    clip_end = float(clip_end)
    if source_duration > 0:
        clip_end = min(clip_end, source_duration)
    if clip_end <= clip_start:
        raise StickerValidationError(
            f"clip_end ({clip_end:.2f}s) must be greater than clip_start ({clip_start:.2f}s)"
        )
    selected = clip_end - clip_start
    if selected < 0.05:
        raise StickerValidationError(f"Selection is too short ({selected:.3f}s); minimum is 0.05s")
    if loop_mode == "trim" and selected > TELEGRAM_MAX_DURATION + 0.05:
        raise StickerValidationError(
            f"Selection is {selected:.2f}s; trim mode allows at most {TELEGRAM_MAX_DURATION}s. "
            f"Use loop_mode='speed_fit' to compress it, or shorten the selection."
        )
    return clip_start, clip_end, selected


def _build_processed_source(
    input_path: Path,
    temp_dir: Path,
    clip_start: float,
    duration: float,
    key_color: str,
    similarity: float,
    blend: float,
    crop: Optional[tuple[float, float, float, float]],
    green_risk: float,
    key_mode: str,
    target_size: int,
    verbose: bool,
) -> Path:
    """Stage 1: crop + chroma key + despill + scale the selected window into a
    high-quality alpha intermediate. This runs the expensive filter chain
    exactly once, before any loop-mode transform or bitrate search."""
    filters: list[str] = ["format=rgba"]
    if crop:
        x, y, w, h = crop
        filters.append(f"crop=iw*{w:.6f}:ih*{h:.6f}:iw*{x:.6f}:ih*{y:.6f}")
    filters.append(f"colorkey=color={key_color}:similarity={similarity:.5f}:blend={blend:.5f}")

    if key_mode == "strong":
        despill_mix, despill_expand = 0.46, 0.11
    elif key_mode == "gentle":
        despill_mix, despill_expand = 0.0, 0.0
    elif green_risk >= 0.035:
        despill_mix, despill_expand = 0.0, 0.0
    else:
        despill_mix, despill_expand = 0.28, 0.07
    if despill_mix > 0:
        filters.append(f"despill=type=green:mix={despill_mix:.2f}:expand={despill_expand:.2f}")

    filters.extend([
        f"scale={target_size}:{target_size}:force_original_aspect_ratio=decrease:flags=lanczos",
        "setsar=1",
        "fps=30",
        "format=yuva420p",
    ])
    vf = ",".join(filters)

    intermediate = temp_dir / "intermediate.webm"
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{clip_start:.3f}", "-i", str(input_path),
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
        "-auto-alt-ref", "0", "-b:v", "3M",
        "-deadline", "good", "-cpu-used", "2", "-row-mt", "1",
        "-metadata:s:v:0", "alpha_mode=1",
        str(intermediate),
    ]
    _run(cmd, verbose=verbose)
    return intermediate


def _apply_loop_mode(
    source: Path,
    temp_dir: Path,
    loop_mode: str,
    selected_duration: float,
    verbose: bool,
) -> tuple[Path, float]:
    """Stage 2: turn the already-processed alpha clip into the final
    pre-compression clip according to loop_mode. Returns (path, duration)."""
    if loop_mode == "trim":
        return source, min(selected_duration, TELEGRAM_MAX_DURATION)

    if loop_mode == "ping_pong":
        # Forward + reverse of the SAME selection = 2x the selected duration.
        # If that exceeds Telegram's 3s ceiling, retime (speed up) uniformly
        # rather than silently truncating the reverse half.
        combined_duration = selected_duration * 2
        out = temp_dir / "pingpong.webm"
        filter_complex = (
            "[0:v]split=2[fwd][rev_src];"
            "[rev_src]reverse[rev];"
            "[fwd][rev]concat=n=2:v=1:a=0[looped]"
        )
        if combined_duration > TELEGRAM_MAX_DURATION + 0.02:
            speed = combined_duration / TELEGRAM_MAX_DURATION
            filter_complex += f";[looped]setpts=PTS/{speed:.6f},fps=30[out]"
            output_duration = TELEGRAM_MAX_DURATION
        else:
            filter_complex += ";[looped]fps=30[out]"
            output_duration = combined_duration
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-filter_complex", filter_complex, "-map", "[out]",
            "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0", "-b:v", "3M",
            "-deadline", "good", "-cpu-used", "2", "-row-mt", "1",
            "-metadata:s:v:0", "alpha_mode=1",
            str(out),
        ]
        _run(cmd, verbose=verbose)
        return out, output_duration

    if loop_mode == "speed_fit":
        # Only accelerate an over-long selection. Never slow down a short one.
        if selected_duration <= TELEGRAM_MAX_DURATION + 0.02:
            return source, selected_duration
        speed = selected_duration / TELEGRAM_MAX_DURATION
        out = temp_dir / "speedfit.webm"
        cmd = [
            "ffmpeg", "-v", "error", "-y", "-i", str(source),
            "-vf", f"setpts=PTS/{speed:.6f},fps=30",
            "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0", "-b:v", "3M",
            "-deadline", "good", "-cpu-used", "2", "-row-mt", "1",
            "-metadata:s:v:0", "alpha_mode=1",
            str(out),
        ]
        _run(cmd, verbose=verbose)
        return out, TELEGRAM_MAX_DURATION

    raise StickerValidationError(f"Unknown loop_mode: {loop_mode!r}")


def process_green_screen_to_sticker(
    input_video: str,
    output_webm: Optional[str] = None,
    target_size: int = 512,
    max_duration_s: float = 3.0,
    bitrate: str = "220K",
    similarity: Optional[float] = None,
    blend: Optional[float] = None,
    key_mode: str = "auto",
    auto_crop: bool = True,
    clip_start: float = 0.0,
    clip_end: Optional[float] = None,
    loop_mode: str = "trim",
    overwrite: bool = False,
    verbose: bool = False,
) -> str:
    """Convert a green-screen clip to a Telegram-ready transparent VP9 WebM.

    key_mode: "gentle" preserves green foreground detail, "auto" estimates the
    screen color/variance from frame borders, and "strong" removes dirtier screens.

    clip_start/clip_end: seconds within the source video to select (Loop Studio).
    Defaults to the whole clip capped at TELEGRAM_MAX_DURATION, matching the
    old behavior exactly when left unset.

    loop_mode:
      "trim"       - use the selection as-is (old default behavior)
      "ping_pong"  - forward + reverse of the selection, retimed to fit 3s
      "speed_fit"  - if selection > 3s, accelerate to fit; otherwise unchanged
    """
    input_path = Path(input_video).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    output_path = Path(output_webm).resolve() if output_webm else input_path.with_suffix(".webm")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    loop_mode = (loop_mode or "trim").strip().lower()
    if loop_mode not in LOOP_MODES:
        raise StickerValidationError(f"Unknown loop_mode: {loop_mode!r}; expected one of {sorted(LOOP_MODES)}")

    source_info = inspect_video(input_path)
    clip_start, clip_end, selected_duration = _resolve_window(
        source_info["duration"], clip_start, clip_end, loop_mode
    )

    temp_dir = Path(tempfile.mkdtemp(prefix="sticker_forge_"))
    try:
        frames = _sample_frames(input_path, temp_dir, selected_duration, start=clip_start)
        key_color, auto_similarity, auto_blend = _estimate_key(frames, key_mode)
        sim = float(similarity) if similarity is not None else auto_similarity
        bl = float(blend) if blend is not None else auto_blend
        key_rgb = _hex_to_rgb(key_color)
        crop = _estimate_crop(frames, key_rgb, sim) if auto_crop else None
        green_risk = _foreground_green_risk(frames, key_rgb, sim)

        processed = _build_processed_source(
            input_path, temp_dir, clip_start, selected_duration,
            key_color, sim, bl, crop, green_risk, key_mode, target_size, verbose,
        )
        looped, _output_duration = _apply_loop_mode(
            processed, temp_dir, loop_mode, selected_duration, verbose
        )

        # Bitrate search only re-encodes the already-processed, already-sized,
        # already-looped clip. No crop/colorkey/scale work happens again here.
        candidates = [bitrate, "190K", "160K", "130K", "105K", "85K", "65K"]
        seen = set()
        last_error: Exception | None = None
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            temp_output = temp_dir / f"final_{candidate}.webm"
            cmd = [
                "ffmpeg", "-v", "error", "-y", "-i", str(looped),
                "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-auto-alt-ref", "0", "-b:v", candidate,
                "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
                "-metadata:s:v:0", "alpha_mode=1",
                str(temp_output),
            ]
            try:
                _run(cmd, verbose=verbose)
                if temp_output.stat().st_size > TELEGRAM_MAX_BYTES:
                    continue
                validate_telegram_webm(temp_output)
                if output_path.exists():
                    output_path.unlink()
                shutil.move(str(temp_output), str(output_path))
                return str(output_path)
            except Exception as exc:
                last_error = exc
                if verbose:
                    print(f"[WARN] {candidate} failed: {exc}")
                continue
        raise StickerProcessingError(f"Could not produce a valid Telegram WebM: {last_error}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
