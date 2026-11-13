"""Album covers in the Lyrics tab and the player: a real image when the terminal can draw one."""
import logging

from PIL import Image
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ..diagnostics import emit, failure

CSS = """
#cover { width: 14; height: 6; margin: 1 1 0 1; }
#cover.picture { width: 12; margin: 1 3 0 1; }
#mini-cover { width: 6; height: 3; margin-right: 2; }
"""


def cover_text(image, cols=12, rows=6):
    """Draw the artwork with half blocks: each cell shows two pixels, one above the other."""
    if image is None:
        return Text("\n".join([" " * cols] * rows))
    pixels = image.resize((cols, rows * 2), Image.Resampling.LANCZOS).load()
    output = Text()
    for row in range(rows):
        if row:
            output.append("\n")
        for x in range(cols):
            top, bottom = pixels[x, row * 2], pixels[x, row * 2 + 1]
            output.append("▀", style=Style(color=f"rgb({top[0]},{top[1]},{top[2]})",
                                            bgcolor=f"rgb({bottom[0]},{bottom[1]},{bottom[2]})"))
    return output


def terminal_artwork():
    """Image widget for terminals with sixel or kitty graphics; None keeps half blocks.

    Importing textual_image probes the terminal, so this must run before Textual owns stdin.
    """
    logging.getLogger("textual_image").addHandler(logging.NullHandler())
    logging.getLogger("textual_image").propagate = False
    try:
        from textual_image.renderable import HalfcellImage, Image as Renderable, UnicodeImage
        from textual_image.widget import Image as ImageWidget
    except Exception as error:
        failure("cover.graphics_unavailable", error)
        return None
    emit("cover.graphics", method=Renderable.__module__.rsplit(".", 1)[-1])
    return None if Renderable in (HalfcellImage, UnicodeImage) else ImageWidget


class CoverArt:
    """Builds every cover widget and draws the current artwork (a PIL image) on all of them.

    `artwork` comes from terminal_artwork(): an image widget scales the picture to whatever size
    its styles give it; None draws half blocks, redrawn by `resize()` for each size.
    """

    def __init__(self, artwork=None):
        self.artwork = artwork
        self.image = None
        self._sizes = {}  # widget id -> (widget, cols, rows)

    def widget(self, identifier, cols, rows):
        if self.artwork is not None:
            widget = self.artwork(id=identifier, classes="picture")
        else:
            widget = Static(cover_text(None, cols, rows), id=identifier)
        self._sizes[identifier] = (widget, cols, rows)
        return widget

    def show(self, image=None):
        self.image = image
        for identifier in self._sizes:
            self._draw(identifier)

    def resize(self, widget, cols, rows):
        widget, old_cols, old_rows = self._sizes[widget.id]
        if (cols, rows) != (old_cols, old_rows):
            self._sizes[widget.id] = (widget, cols, rows)
            if self.artwork is None:
                self._draw(widget.id)

    def _draw(self, identifier):
        widget, cols, rows = self._sizes[identifier]
        if self.artwork is not None:
            widget.image = self.image
        else:
            widget.update(cover_text(self.image, cols, rows))
