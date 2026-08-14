from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config


def _item(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "ok": status == "ok", "detail": detail}


def run_preflight() -> dict:
    if config.SKIP_PREFLIGHT:
        return {"ok": True, "skipped": True, "checks": []}
    checks: list[dict] = []
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        try:
            line = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
            checks.append(_item("ffmpeg", "ok", line))
        except Exception as exc:
            checks.append(_item("ffmpeg", "fail", str(exc)))
    else:
        checks.append(_item("ffmpeg", "fail", "ffmpeg/ffprobe not found"))

    if ffmpeg:
        try:
            encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=10).stdout
            checks.append(_item("libvpx-vp9", "ok" if "libvpx-vp9" in encoders else "fail", "VP9 encoder available" if "libvpx-vp9" in encoders else "libvpx-vp9 encoder missing"))
        except Exception as exc:
            checks.append(_item("libvpx-vp9", "fail", str(exc)))

        tmp = Path(tempfile.mkdtemp(prefix="sf_preflight_"))
        out = tmp / "alpha.webm"
        try:
            cmd = [
                ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x00ff00:s=32x32:d=0.15:r=10",
                "-vf", "format=rgba,drawbox=x=8:y=8:w=16:h=16:color=red@1:t=fill,colorkey=0x00ff00:0.2:0.02,format=yuva420p",
                "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "80K",
                "-metadata:s:v:0", "alpha_mode=1", str(out),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode("utf-8", "ignore")[-600:])
            raw = subprocess.run([ffmpeg, "-v", "error", "-c:v", "libvpx-vp9", "-i", str(out), "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "pipe:1"], capture_output=True, timeout=15)
            alphas = raw.stdout[3::4]
            has_alpha = bool(alphas) and min(alphas) < 10 and max(alphas) > 240
            checks.append(_item("vp9_alpha", "ok" if has_alpha else "fail", "transparent VP9 encode/decode works" if has_alpha else "VP9 alpha test did not preserve transparency"))
        except Exception as exc:
            checks.append(_item("vp9_alpha", "fail", str(exc)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    checks.append(_item("bot_token", "ok" if bool(config.BOT_TOKEN) else "warn", "BOT_TOKEN configured" if config.BOT_TOKEN else "BOT_TOKEN not set"))
    url_ok = bool(config.PUBLIC_URL and config.PUBLIC_URL.startswith("https://"))
    checks.append(_item("public_url", "ok" if url_ok else "warn", config.PUBLIC_URL if url_ok else "PUBLIC_URL should be an https URL"))
    try:
        root = Path(config.WORK_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        marker = root / ".write_test"
        marker.write_text("ok")
        marker.unlink()
        checks.append(_item("workdir", "ok", str(root)))
    except Exception as exc:
        checks.append(_item("workdir", "fail", str(exc)))

    fatal = [x for x in checks if x["status"] == "fail"]
    return {"ok": not fatal, "skipped": False, "checks": checks}
