"""Omarchy-aware Textual theme support."""

from __future__ import annotations

try:
    import tomllib
except ImportError:  # Python 3.10
    import tomli as tomllib
from pathlib import Path

from textual.theme import Theme


DEFAULT_COLORS = {
    "accent": "#7daea3",
    "selection": "#504945",
    "muted": "#665c54",
    "background": "#282828",
    "dark_background": "#1e1e1e",
    "lighter_background": "#3c3836",
    "foreground": "#d4be98",
    "red": "#ea6962",
    "yellow": "#d8a657",
    "green": "#a9b665",
    "cyan": "#89b482",
    "mode": "dark",
}


def load_omarchy_colors() -> dict[str, str]:
    """Load the active semantic palette, with a stable fallback elsewhere."""
    colors = dict(DEFAULT_COLORS)
    path = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
        colors.update({key: value for key, value in loaded.items() if isinstance(value, str)})
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        pass
    return colors


def omarchy_theme() -> Theme:
    """Build a restrained theme from Omarchy's semantic color roles."""
    colors = load_omarchy_colors()
    return Theme(
        name="omarchy",
        primary=colors["accent"],
        secondary=colors.get("cyan", colors["accent"]),
        accent=colors["accent"],
        foreground=colors["foreground"],
        background=colors["background"],
        surface=colors.get("dark_background", colors["background"]),
        panel=colors.get("dark_background", colors["background"]),
        boost=colors.get("selection", colors["lighter_background"]),
        warning=colors["yellow"],
        error=colors["red"],
        success=colors["green"],
        dark=colors.get("mode", "dark") != "light",
        variables={
            "text-muted": colors.get("muted", colors["foreground"]),
            "block-cursor-background": colors["accent"],
            "input-selection-background": colors.get("selection", colors["accent"]),
        },
    )
