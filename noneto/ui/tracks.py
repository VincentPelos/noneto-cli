"""Songs tab: the track table, filled in batches, with ▷ on the song that is playing."""
import asyncio

from rich.text import Text
from textual.coordinate import Coordinate
from textual.widgets import DataTable

from ..i18n import _
from .common import album, artist, clean, duration

CSS = """
#tracks { height: 1fr; background: $background; }
DataTable > .datatable--header { background: $background; color: $text-muted; text-style: bold; }
"""


class TrackTable(DataTable):
    """Play a track with one mouse click while keeping row selection for actions.

    `tracks` always holds the whole list so Play gets every song. Rows appear 100 at first and
    then in batches of up to 500, yielding the loop between them; `reset()` drops pending batches.
    The title column takes the width the others leave and cuts long names with "…", so the
    table never scrolls sideways and Duration stays visible.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tracks = []
        self._current_id = None
        self._revision = 0
        self._fill_task = None

    def on_mount(self):
        self._mark_key, self._title_key, self._duration_key = self.add_columns("#", _("Title"), _("Duration"))
        self.columns[self._title_key].auto_width = False
        self._fit_title()

    def on_resize(self):
        self._fit_title()

    def _fit_title(self):
        # The vertical scrollbar is always reserved so the width doesn't jump when it appears.
        mark = max(1, len(str(len(self.tracks)))) + 2 * self.cell_padding
        others = mark + self.columns[self._duration_key].get_render_width(self)
        free = self.size.width - self.styles.scrollbar_size_vertical - others - 2 * self.cell_padding
        title = self.columns[self._title_key]
        if title.width != max(10, free):
            title.width = max(10, free)
            # Textual has no public call to resize a column: drop its render caches, which are not
            # keyed by width, and let it recompute the virtual size (checked against textual 8.2.8).
            self._clear_caches()
            self._require_update_dimensions = True
            self.refresh()

    async def _on_click(self, event):
        row = event.style.meta.get("row")
        column = event.style.meta.get("column")
        if (event.button == 1 and isinstance(row, int) and 0 <= row < self.row_count
                and isinstance(column, int) and column >= 0):
            coordinate = Coordinate(row, column)
            self.cursor_coordinate = coordinate
            row_key, _column = self.coordinate_to_cell_key(coordinate)
            self.post_message(DataTable.RowSelected(self, row, row_key))
            event.stop()
            return
        await super()._on_click(event)

    def selected(self):
        index = self.cursor_row
        return self.tracks[index] if 0 <= index < len(self.tracks) else None

    def reset(self):
        self._revision += 1
        if self._fill_task is not None:
            self._fill_task.cancel()
            self._fill_task = None
        self.tracks = []
        self.clear()

    def show(self, tracks, append=False):
        if not append:
            self._revision += 1
            if self._fill_task is not None:
                self._fill_task.cancel()
                self._fill_task = None
            self.clear()
            self.tracks = []
        self.tracks.extend(tracks)
        if self._fill_task is None:
            self._fill(self._revision)

    def set_current(self, track_id):
        previous, self._current_id = self._current_id, track_id
        for index, track in enumerate(self.tracks):
            if index < self.row_count and getattr(track, "id", None) in (previous, track_id):
                self.update_cell_at(Coordinate(index, 0), self._mark(index, track))

    async def cancel_fill(self):
        if self._fill_task is not None:
            self._fill_task.cancel()
            try:
                await self._fill_task
            except asyncio.CancelledError:
                pass

    def _mark(self, index, track):
        if self._current_id is not None and getattr(track, "id", None) == self._current_id:
            return Text("▷", style="bold")
        return Text(str(index + 1), style="dim")

    def _fill(self, revision):
        if revision != self._revision:
            return
        start = self.row_count
        batch = 100 if self._fill_task is None else 500
        for index in range(start, min(start + batch, len(self.tracks))):
            track = self.tracks[index]
            title = Text(clean(track.name), style="bold", no_wrap=True, overflow="ellipsis")
            title.append(f"\n{artist(track)} · {album(track)}", style="dim")
            self.add_row(self._mark(index, track), title,
                         Text(duration(getattr(track, "duration", 0)), style="dim"), height=2)
        self._fit_title()  # more rows can mean one more digit in the # column
        if self.row_count < len(self.tracks) and self._fill_task is None:
            self._fill_task = asyncio.create_task(self._fill_remaining(revision))

    async def _fill_remaining(self, revision):
        try:
            while self.is_mounted and revision == self._revision:
                await asyncio.sleep(0)  # Yield without a per-batch Windows timer wakeup.
                if revision != self._revision or not self.is_mounted:
                    return
                self._fill(revision)
                if self.row_count >= len(self.tracks):
                    return
        except Exception as error:
            self.app._handle_exception(error)
        finally:
            if revision == self._revision:
                self._fill_task = None
