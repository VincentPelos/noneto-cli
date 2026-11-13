"""Color palettes and theme extension API for the terminal interface.

The layout uses Textual's semantic CSS variables, so registering another Theme
can change its colors without rewriting the screen or its widgets.

People and agents can register new themes in two ways:
1. Programmatically via `register_theme(ThemeSpec(...))` or `register_theme(theme, stops=...)`.
2. Declaratively by dropping a JSON file into `<config_dir>/themes/*.json` (loaded via `load_user_themes()`).
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from textual.theme import BUILTIN_THEMES, Theme


NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,49}$")
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_THEME_FILE_BYTES = 16384
MAX_STOPS = 16
ALLOWED_VARIABLES = frozenset({
    "text",
    "text-muted",
    "border",
    "border-blurred",
    "block-cursor-background",
    "block-cursor-foreground",
    "block-cursor-blurred-background",
    "block-cursor-blurred-foreground",
    "block-hover-background",
    "button-foreground",
    "button-color-foreground",
    "input-selection-background",
    "input-selection-foreground",
    "footer-background",
    "footer-key-foreground",
    "scrollbar",
    "scrollbar-background",
})


def _valid_name(name):
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ValueError(f"Invalid name: {name!r}")
    return name


def _valid_hex(color):
    if not isinstance(color, str) or not HEX_COLOR_PATTERN.match(color):
        raise ValueError(f"Invalid #rrggbb color: {color!r}")
    return color.lower()


def _valid_stops(stops, fallback):
    if not stops:
        return (_valid_hex(fallback),)
    if not isinstance(stops, (list, tuple)) or len(stops) > MAX_STOPS:
        raise ValueError("Visualizer stops must be a sequence of 1 to 16 #rrggbb colors")
    return tuple(_valid_hex(stop) for stop in stops)


@dataclass(frozen=True)
class ThemeSpec:
    """Declarative specification for a Noneto CLI theme and its visualizer gradient."""

    name: str
    primary: str
    background: str
    foreground: str = "#f5f5f5"
    secondary: str | None = None
    accent: str | None = None
    surface: str | None = None
    panel: str | None = None
    warning: str | None = None
    error: str | None = None
    success: str | None = None
    dark: bool = True
    stops: tuple[str, ...] = ()
    variables: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "name", _valid_name(self.name))
        for field_name in ("primary", "background", "foreground"):
            object.__setattr__(self, field_name, _valid_hex(getattr(self, field_name)))
        for optional_name in ("secondary", "accent", "surface", "panel", "warning", "error", "success"):
            value = getattr(self, optional_name)
            if value is not None:
                object.__setattr__(self, optional_name, _valid_hex(value))
        if not isinstance(self.dark, bool):
            raise ValueError("dark must be a boolean")
        object.__setattr__(self, "stops", _valid_stops(self.stops, self.primary))
        cleaned_vars = {}
        if not isinstance(self.variables, dict):
            raise ValueError("variables must be a dict")
        for key, val in self.variables.items():
            if key in ALLOWED_VARIABLES:
                cleaned_vars[key] = _valid_hex(val)
        object.__setattr__(self, "variables", cleaned_vars)

    def to_textual_theme(self) -> Theme:
        kwargs = {
            "name": self.name,
            "primary": self.primary,
            "secondary": self.secondary or self.primary,
            "accent": self.accent or self.primary,
            "foreground": self.foreground,
            "background": self.background,
            "surface": self.surface or self.background,
            "panel": self.panel or self.surface or self.background,
            "dark": self.dark,
            "variables": dict(self.variables),
        }
        for optional_key in ("warning", "error", "success"):
            val = getattr(self, optional_key)
            if val is not None:
                kwargs[optional_key] = val
        return Theme(**kwargs)

    @classmethod
    def from_dict(cls, data: dict) -> "ThemeSpec":
        if not isinstance(data, dict):
            raise ValueError("Theme definition must be a JSON object")
        return cls(
            name=data.get("name"),
            primary=data.get("primary"),
            background=data.get("background"),
            foreground=data.get("foreground", "#f5f5f5" if data.get("dark", True) else "#1c1c1c"),
            secondary=data.get("secondary"),
            accent=data.get("accent"),
            surface=data.get("surface"),
            panel=data.get("panel"),
            warning=data.get("warning"),
            error=data.get("error"),
            success=data.get("success"),
            dark=data.get("dark", True),
            stops=tuple(data.get("stops") or ()),
            variables=data.get("variables") or {},
        )


TIDAL_DARK = Theme(
    name="noneto-dark",
    primary="#f5f5f5",
    secondary="#b3b3b3",
    accent="#f5f5f5",
    foreground="#f5f5f5",
    background="#000000",
    surface="#121212",
    panel="#1c1c1c",
    dark=True,
    variables={
        "text": "#f5f5f5",
        "text-muted": "#a6a6a6",
        "border": "#383838",
        "border-blurred": "#292929",
        "block-cursor-background": "#f5f5f5",
        "block-cursor-foreground": "#000000",
        "block-cursor-blurred-background": "#303030",
        "block-cursor-blurred-foreground": "#f5f5f5",
        "block-hover-background": "#292929",
        "button-foreground": "#f5f5f5",
        "button-color-foreground": "#000000",
        "input-selection-background": "#4a4a4a",
        "input-selection-foreground": "#ffffff",
        "footer-background": "#000000",
        "footer-key-foreground": "#f5f5f5",
        "scrollbar": "#555555",
        "scrollbar-background": "#121212",
    },
)

KANAGAWA = Theme(
    name="kanagawa",
    primary="#7e9cd8",
    secondary="#957fb8",
    accent="#ffa066",
    foreground="#dcd7ba",
    background="#1f1f28",
    surface="#2a2a37",
    panel="#16161d",
    warning="#ff9e3b",
    error="#e82424",
    success="#98bb6c",
    dark=True,
)

EVERFOREST = Theme(
    name="everforest",
    primary="#a7c080",
    secondary="#7fbbb3",
    accent="#e67e80",
    foreground="#d3c6aa",
    background="#2d353b",
    surface="#343f44",
    panel="#232a2e",
    warning="#dbbc7f",
    error="#e67e80",
    success="#a7c080",
    dark=True,
)

VESPER = Theme(
    name="vesper",
    primary="#ffc799",
    secondary="#99ffe4",
    accent="#ffc799",
    foreground="#ffffff",
    background="#101010",
    surface="#1c1c1c",
    panel="#161616",
    warning="#ffc799",
    error="#ff8080",
    success="#99ffe4",
    dark=True,
)

SYNTHWAVE_84 = Theme(
    name="synthwave-84",
    primary="#ff7edb",
    secondary="#36f9f6",
    accent="#fede5d",
    foreground="#ffffff",
    background="#262335",
    surface="#34294f",
    panel="#1e1a2b",
    warning="#fede5d",
    error="#fe4450",
    success="#72f1b8",
    dark=True,
)

CUSTOM_THEMES = {
    theme.name: theme
    for theme in (TIDAL_DARK, KANAGAWA, EVERFOREST, VESPER, SYNTHWAVE_84)
}

# Themes offered in the app, in Ctrl+T order.
THEMES = (
    "noneto-dark",
    "catppuccin-mocha",
    "dracula",
    "tokyo-night",
    "nord",
    "rose-pine",
    "flexoki",
    "catppuccin-latte",
    "rose-pine-dawn",
    "kanagawa",
    "everforest",
    "vesper",
    "synthwave-84",
    "gruvbox",
    "monokai",
)

# Visualizer gradient, left to right across the bars, from each palette's own accent colors.
VISUALIZER_STOPS = {
    "noneto-dark": ("#4dd0e1", "#5c7cfa", "#b197fc", "#f783ac"),
    "catppuccin-mocha": ("#89b4fa", "#74c7ec", "#94e2d5", "#a6e3a1", "#f9e2af", "#fab387", "#f38ba8", "#cba6f7"),
    "dracula": ("#8be9fd", "#50fa7b", "#f1fa8c", "#ffb86c", "#ff79c6", "#bd93f9"),
    "tokyo-night": ("#7aa2f7", "#7dcfff", "#73daca", "#9ece6a", "#e0af68", "#ff9e64", "#f7768e", "#bb9af7"),
    "nord": ("#5e81ac", "#81a1c1", "#88c0d0", "#8fbcbb", "#a3be8c", "#ebcb8b", "#d08770", "#bf616a", "#b48ead"),
    "rose-pine": ("#31748f", "#9ccfd8", "#c4a7e7", "#ebbcba", "#f6c177", "#eb6f92"),
    "flexoki": ("#4385be", "#3aa99f", "#879a39", "#d0a215", "#da702c", "#d14d41", "#ce5d97", "#8b7ec8"),
    "catppuccin-latte": ("#1e66f5", "#209fb5", "#179299", "#40a02b", "#df8e1d", "#fe640b", "#d20f39", "#8839ef"),
    "rose-pine-dawn": ("#286983", "#56949f", "#907aa9", "#d7827e", "#ea9d34", "#b4637a"),
    "kanagawa": ("#7e9cd8", "#7fb4ca", "#98bb6c", "#dca561", "#ffa066", "#e46876", "#957fb8"),
    "everforest": ("#7fbbb3", "#83c092", "#a7c080", "#dbbc7f", "#e69875", "#e67e80", "#d699b6"),
    "vesper": ("#5a5a5a", "#8c8c8c", "#d4b08c", "#ffc799", "#ffe2cc", "#99ffe4"),
    "synthwave-84": ("#36f9f6", "#72f1b8", "#fede5d", "#ff8b39", "#fe4450", "#ff7edb", "#b893ce"),
    "gruvbox": ("#83a598", "#8ec07c", "#b8bb26", "#fabd2f", "#fe8019", "#fb4934", "#d3869b"),
    "monokai": ("#66d9ef", "#a6e22e", "#e6db74", "#fd971f", "#f92672", "#ae81ff"),
}


def register_theme(theme_or_spec, stops=None) -> Theme:
    """Register a custom theme and its visualizer stops into Noneto CLI.

    Accepts either a `ThemeSpec`, a `dict` matching `ThemeSpec`, or a Textual `Theme` instance.
    Updates `CUSTOM_THEMES`, `VISUALIZER_STOPS`, and `THEMES` in place so `NonetoApp` picks it up.
    """
    global THEMES
    if isinstance(theme_or_spec, dict):
        theme_or_spec = ThemeSpec.from_dict(theme_or_spec)
    if isinstance(theme_or_spec, ThemeSpec):
        theme = theme_or_spec.to_textual_theme()
        resolved_stops = _valid_stops(stops, theme.primary) if stops is not None else theme_or_spec.stops
    elif isinstance(theme_or_spec, Theme):
        _valid_name(theme_or_spec.name)
        theme = theme_or_spec
        resolved_stops = _valid_stops(stops, theme.primary)
    else:
        raise TypeError("Expected ThemeSpec, dict, or textual.theme.Theme")

    if theme.name not in BUILTIN_THEMES:
        CUSTOM_THEMES[theme.name] = theme
    VISUALIZER_STOPS[theme.name] = resolved_stops
    if theme.name not in THEMES:
        THEMES = (*THEMES, theme.name)
    return theme


def available_themes() -> tuple[str, ...]:
    """Return the current tuple of registered theme names in Ctrl+T cycle order."""
    return THEMES


def load_user_themes(directory: Path | str | None = None) -> tuple[str, ...]:
    """Load declarative `*.json` theme files from `<config_dir>/themes` (or `directory`).

    Invalid or oversized files are skipped without crashing startup. Returns the names loaded.
    """
    if directory is None:
        from ..platform.paths import config_dir
        directory = config_dir() / "themes"
    path = Path(directory)
    if not path.is_dir():
        return ()
    loaded = []
    for item in sorted(path.glob("*.json"))[:50]:
        try:
            if not item.is_file():
                continue
            with item.open("rb") as handle:
                raw = handle.read(MAX_THEME_FILE_BYTES + 1)
            if len(raw) > MAX_THEME_FILE_BYTES:
                continue
            spec = ThemeSpec.from_dict(json.loads(raw.decode("utf-8")))
            register_theme(spec)
            loaded.append(spec.name)
        except (OSError, ValueError, TypeError):
            continue
    return tuple(loaded)


def gradient(stops, count):
    """`count` colors evenly spread over the stops, as #rrggbb."""
    if count <= 0:
        return []
    points = [tuple(int(stop[i:i + 2], 16) for i in (1, 3, 5)) for stop in stops]
    if len(points) == 1 or count == 1:
        return [stops[0]] * count
    colors = []
    for index in range(count):
        position = index / (count - 1) * (len(points) - 1)
        left = min(int(position), len(points) - 2)
        share = position - left
        mixed = (round(a + (b - a) * share) for a, b in zip(points[left], points[left + 1]))
        colors.append("#" + "".join(f"{channel:02x}" for channel in mixed))
    return colors
