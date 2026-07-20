from .themes import get_theme


def build_prompt(
    base_description: str,
    theme: str = "default",
    character_name: str = ""
) -> str:
    """
    Builds a complete prompt for generating green-screen animations
    ready for the sticker pipeline.
    
    Args:
        base_description: Your main character/scene description
        theme: "default" or "degen" (or any theme defined in themes.py)
        character_name: Optional name to include in the prompt
    """
    theme_config = get_theme(theme)
    
    parts = []
    
    if character_name:
        parts.append(f"Create a 3-second animated sticker of {character_name}:")
    
    parts.append(base_description.strip())
    
    parts.append("\n\nTechnical requirements (non-negotiable):")
    for rule in theme_config["prompt_additions"]:
        parts.append(f"- {rule}")
    
    parts.append(
        "\n\nCRITICAL: The green background must be a perfectly flat #00FF00 "
        "with zero texture or lighting variation so that chromakey removal "
        "produces clean transparency with no edge halos."
    )
    
    return "\n".join(parts)
