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
