from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class SavedStickerSet:
    name: str
    title: str
    count: int = 0


_SETS_FILE = Path(config.WORK_ROOT) / "sets.json"
_LOCK = threading.Lock()


def _load_all() -> dict[str, list[dict]]:
    if not _SETS_FILE.exists():
        return {}

    try:
        data = json.loads(
            _SETS_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    cleaned: dict[str, list[dict]] = {}

    for user_id, sets in data.items():
        if not isinstance(sets, list):
            continue

        cleaned[str(user_id)] = [
            item
            for item in sets
            if isinstance(item, dict)
            and item.get("name")
            and item.get("title")
        ]

    return cleaned


def _save_all(data: dict[str, list[dict]]) -> None:
    _SETS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = _SETS_FILE.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    temporary.replace(_SETS_FILE)


def list_sets(user_id: int) -> list[SavedStickerSet]:
    with _LOCK:
        data = _load_all()

    records = data.get(str(user_id), [])

    return [
        SavedStickerSet(
            name=str(item["name"]),
            title=str(item["title"]),
            count=int(item.get("count", 0)),
        )
        for item in records
    ]


def get_set(
    user_id: int,
    set_name: str,
) -> SavedStickerSet | None:
    for saved_set in list_sets(user_id):
        if saved_set.name == set_name:
            return saved_set

    return None


def save_set(
    user_id: int,
    name: str,
    title: str,
    count: int = 0,
) -> SavedStickerSet:
    saved_set = SavedStickerSet(
        name=name,
        title=title,
        count=max(0, int(count)),
    )

    with _LOCK:
        data = _load_all()
        user_key = str(user_id)
        records = data.setdefault(user_key, [])

        records = [
            item
            for item in records
            if item.get("name") != name
        ]

        records.append(asdict(saved_set))
        data[user_key] = records

        _save_all(data)

    return saved_set


def update_set_count(
    user_id: int,
    set_name: str,
    count: int,
) -> SavedStickerSet | None:
    existing = get_set(user_id, set_name)

    if existing is None:
        return None

    return save_set(
        user_id=user_id,
        name=existing.name,
        title=existing.title,
        count=count,
    )


def owns_set(
    user_id: int,
    set_name: str,
) -> bool:
    return get_set(user_id, set_name) is not None
