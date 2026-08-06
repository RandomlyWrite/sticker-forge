from __future__ import annotations

import re


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "]",
    flags=re.UNICODE,
)


def extract_emojis(text: str) -> list[str]:
    """Return emojis found in text, preserving their original order."""

    if not text:
        return []

    emojis: list[str] = []

    for match in _EMOJI_PATTERN.finditer(text):
        emoji = match.group(0)

        if emoji not in emojis:
            emojis.append(emoji)

    return emojis


def strip_emojis(text: str) -> str:
    """Remove emojis and normalize the remaining whitespace."""

    if not text:
        return ""

    cleaned = _EMOJI_PATTERN.sub("", text)

    cleaned = cleaned.replace("\ufe0f", "")
    cleaned = cleaned.replace("\u200d", "")

    return " ".join(cleaned.split())
