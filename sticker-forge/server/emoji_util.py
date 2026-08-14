from __future__ import annotations
import re
_EMOJI_PATTERN = re.compile("[\U0001F1E0-\U0001F1FF\U0001F300-\U0001FAFF\U00002600-\U000027BF]", flags=re.UNICODE)
def extract_emojis(text: str) -> list[str]:
    seen=[]
    for m in _EMOJI_PATTERN.finditer(text or ""):
        if m.group(0) not in seen: seen.append(m.group(0))
    return seen
def strip_emojis(text: str) -> str:
    return " ".join(_EMOJI_PATTERN.sub("", text or "").replace("\ufe0f","").replace("\u200d","").split())
