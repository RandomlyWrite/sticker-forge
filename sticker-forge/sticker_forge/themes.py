"""
Theme system for Sticker Forge.
Add or modify themes here without changing core logic.
"""

THEMES = {
    "default": {
        "name": "Default",
        "prompt_additions": [
            "Full-bleed, edge-to-edge composition with zero borders or margins.",
            "Solid, perfectly even green background exactly #00FF00 for clean chromakey.",
            "Exactly 3 seconds duration at 30 fps.",
            "High contrast and clear visual readability."
        ],
        "emoji_pool": ["🖼️", "✨", "🔥", "💫", "🎨"],
        "savage_roasts": []
    },

    "degen": {
        "name": "Degen",
        "prompt_additions": [
            "Full-bleed, edge-to-edge, ZERO borders, margins or frames. Artwork must bleed off the canvas.",
            "Solid, perfectly uniform bright green background exactly #00FF00 with zero texture or lighting variation.",
            "Exactly 3 seconds at 30 fps. Tight, punchy timing.",
            "Rubber-hose animation twisted with Tim Burton + gonzo + underground comic energy.",
            "Dramatic chiaroscuro lighting, heavy shadows, selective toxic color pops."
        ],
        "emoji_pool": [
            "🃏", "♠️", "♥️", "♦️", "♣️", "💀", "🔥", "🍾", "🦊", "👑",
            "😈", "🤡", "🪦", "⚰️", "💸", "🎰", "☠️"
        ],
        "savage_roasts": [
            "Your new sticker set is live, you degenerate. {link}",
            "Fresh cursed stickers just dropped. {link}",
            "The void has accepted your offering. {link}",
            "Another set of emotional support stickers for when the river fucks you. {link}"
        ]
    }
}


def get_theme(theme_name: str = "default"):
    """Returns the theme config. Falls back to 'default' if theme not found."""
    return THEMES.get(theme_name.lower(), THEMES["default"])
