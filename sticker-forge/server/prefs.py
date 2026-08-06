from __future__ import annotations

import json
import threading
from pathlib import Path

from . import config


_PREFS_FILE = Path(config.WORK_ROOT) / "prefs.json"
_LOCK = threading.Lock()


def _load() -> dict[str, str]:
    if not _PREFS_FILE.exists():
        return {}

    try:
        data = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    return {
        str(user_id): str(theme)
        for user_id, theme in data.items()
    }


def _save(data: dict[str, str]) -> None:
    _PREFS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = _PREFS_FILE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    temporary.replace(_PREFS_FILE)


def get_theme_pref(user_id: int) -> str:
    with _LOCK:
        prefs = _load()

    theme = prefs.get(
        str(user_id),
        config.DEFAULT_THEME,
    )

    if theme not in {"default", "degen"}:
        return config.DEFAULT_THEME

    return theme


def set_theme_pref(
    user_id: int,
    theme: str,
) -> str:
    normalized = theme.strip().lower()

    if normalized not in {"default", "degen"}:
        normalized = config.DEFAULT_THEME

    with _LOCK:
        prefs = _load()
        prefs[str(user_id)] = normalized
        _save(prefs)

    return normalized
