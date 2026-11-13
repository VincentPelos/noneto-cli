"""Lyrics tab: the cover beside the lyrics, or the cover alone and centered.

Two layouts the user switches between: "lyrics" (small cover + lyrics highlighted line by line
when TIDAL times them) and "cover" (large centered cover). A song without lyrics always shows
the large cover.
"""
import bisect
import re

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Static

from ..i18n import _
from .common import clean

CSS = """
#lyrics-pane { height: 1fr; }
#lyrics-body { width: 1fr; padding: 0 1; }
#lyrics-scroll { height: 1fr; scrollbar-size: 1 1; }
#lyrics-text { height: auto; }
#lyrics-pane.centered { align: center middle; }
#lyrics-pane.centered #lyrics-body { display: none; }
#lyrics-pane.centered #cover { margin: 0; }
"""

LAYOUTS = ("lyrics", "cover")
SMALL_COVER = (12, 6)  # cells: 12 columns × 6 rows is square with 1:2 cells
# The 640 px download fills 32 rows of 20 px (Windows Terminal's sixel cell); larger only blurs it
# and makes each redraw slower (~80 ms to encode at 800 px).
MAX_COVER_ROWS = 32


def lyric_lines(plain, subtitles):
    """Accept bounded LRC lines; use plain text if timing is unavailable."""
    timed = []
    for line in (subtitles or "").splitlines()[:400]:
        match = re.match(r"^\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\](.*)$", line)
        if match:
            fraction = (match[3] or "0").ljust(3, "0")
            second = int(match[1]) * 60 + int(match[2]) + int(fraction) / 1000
            timed.append((second, clean(match[4])[:300]))
    if timed:
        return sorted(timed), True
    return [(None, clean(line)[:300]) for line in (plain or "").splitlines()[:300]], False


class LyricsPanel(Horizontal):
    class CoverClicked(Message):
        """Posted when the cover is clicked, to switch layouts."""

    def __init__(self, covers, layout="lyrics", **kwargs):
        super().__init__(**kwargs)
        self._covers = covers
        self._cover = covers.widget("cover", *SMALL_COVER)
        self.cover_layout = layout if layout in LAYOUTS else "lyrics"
        self.missing = False  # the current song has no lyrics
        self._lines = []
        self._times = ()
        self._timed = False
        self.active_line = None

    def compose(self):
        yield self._cover
        with Vertical(id="lyrics-body"):
            with VerticalScroll(id="lyrics-scroll"):
                yield Static(_("Play a song to see its lyrics."), id="lyrics-text", markup=False)

    def on_mount(self):
        self._text = self.query_one("#lyrics-text", Static)
        self._scroll = self.query_one("#lyrics-scroll", VerticalScroll)
        self._arrange()

    def on_resize(self):
        self._arrange()

    def on_click(self, event):
        if event.button == 1 and self._cover in (event.widget, *event.widget.ancestors):
            self.post_message(self.CoverClicked())
            event.stop()

    def set_layout(self, layout):
        self.cover_layout = layout if layout in LAYOUTS else "lyrics"
        self._arrange()

    @property
    def centered(self):
        return self.cover_layout == "cover" or self.missing

    def _arrange(self):
        self.set_class(self.centered, "centered")
        if not self.centered:
            self._cover.styles.width = self._cover.styles.height = None  # back to the CSS size
            self._covers.resize(self._cover, *SMALL_COVER)
            return
        # As large as the pane allows with one free row above and below, kept square.
        rows = max(1, min(self.size.height - 2, (self.size.width - 2) // 2, MAX_COVER_ROWS))
        self._cover.styles.width, self._cover.styles.height = rows * 2, rows
        self._covers.resize(self._cover, rows * 2, rows)

    def reset(self, playing):
        self._lines, self.active_line = [], None
        self._times = ()
        self._text.update(_("Loading lyrics…") if playing else _("Play a song to see its lyrics."))
        self._set_missing(False)

    def load(self, plain, subtitles, position):
        self._lines, self._timed = lyric_lines(plain, subtitles)
        self._times = tuple(when for when, _line in self._lines)
        self.active_line = None
        if not self._lines:
            self.unavailable()
            return
        self._set_missing(False)
        self.follow(position)

    def unavailable(self):
        self._text.update(_("No lyrics available for this song."))
        self._set_missing(True)

    def _set_missing(self, missing):
        if missing != self.missing:
            self.missing = missing
            self._arrange()

    def follow(self, position):
        if not self._lines:
            return
        active = bisect.bisect_right(self._times, position) - 1 if self._timed else -1
        if active == self.active_line:
            return
        initial = self.active_line is None
        self.active_line = active
        output = Text()
        for index, (_when, line) in enumerate(self._lines):
            output.append(line or " ", style="bold" if index == active else "dim")
            if index < len(self._lines) - 1:
                output.append("\n")
        self._text.update(output, layout=initial)
        if active >= 0:
            self._scroll.scroll_to(y=max(0, active - 3), animate=False)
