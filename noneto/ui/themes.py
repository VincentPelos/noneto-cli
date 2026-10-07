"""Color palettes for the terminal interface.

The layout uses Textual's semantic CSS variables, so registering another Theme
can change its colors without rewriting the screen or its widgets.
"""

from textual.theme import Theme


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

# Themes offered in the app, in Ctrl+T order (chosen by the user on 2026-10-06).
THEMES = ("noneto-dark", "catppuccin-mocha", "dracula", "tokyo-night", "nord", "rose-pine", "flexoki",
          "catppuccin-latte", "rose-pine-dawn")

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
}


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
