"""Helpers shared by the TUI pieces: safe track text and cheap repeated updates."""
from ..security import terminal_text


def clean(value):
    return terminal_text(str(value or ""))


def artist(track):
    return clean(getattr(getattr(track, "artist", None), "name", ""))


def album(track):
    return clean(getattr(getattr(track, "album", None), "name", ""))


def duration(value):
    seconds = max(0, int(value or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Changes:
    """Runs 4 times a second: skip unchanged widgets, and fixed-size ones skip the screen-wide layout pass."""

    def __init__(self):
        self._shown = {}

    def show(self, widget, value, layout=False):
        if self._shown.get(widget.id) != value:
            self._shown[widget.id] = value
            widget.update(value, layout=layout)

    def label(self, button, value):
        if self._shown.get(button.id) != value:
            self._shown[button.id] = value
            button.label = value
