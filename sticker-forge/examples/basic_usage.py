from sticker_forge import build_prompt, forge_sticker_set, StickerUploader, get_theme

print("=== Sticker Forge Framework Demo ===\n")

# Example 1: Default theme
prompt = build_prompt(
    "bored queen character doing a slow swivel animation",
    theme="default"
)
print("DEFAULT THEME PROMPT:\n", prompt[:200], "...\n")

# Example 2: Degen theme
prompt = build_prompt(
    "bored queen character doing a slow swivel animation",
    theme="degen",
    character_name="Spade Queen"
)
print("DEGEN THEME PROMPT:\n", prompt[:250], "...\n")

print("\nTheme system loaded successfully!")
print("Available themes:", list(get_theme.__globals__['THEMES'].keys()))
