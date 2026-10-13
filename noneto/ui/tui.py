"""Mouse and keyboard interface; all account and playback work is injected."""
import asyncio
import bisect
import re

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (Button, DataTable, Footer, Input, Label, ListItem, ListView, ProgressBar,
                             Static, TabbedContent, TabPane)

from ..diagnostics import emit, failure
from ..errors import AppError, error_label
from ..i18n import _
from ..security import terminal_text
from .themes import THEMES, TIDAL_DARK
from .visualizer import Visualizer


# Requested tier: a ceiling, not a promise. The badge shows what mpv decodes once a track loads.
QUALITY_LABELS = {
    "LOW": ("AAC 96k", "AAC 96 kbps"),
    "HIGH": ("AAC 320k", "AAC 320 kbps"),
    "LOSSLESS": ("Lossless", "FLAC 16-bit / 44.1 kHz"),
    "HI_RES_LOSSLESS": ("Hi-Res", _("FLAC up to 24-bit / 192 kHz")),
}
TIER_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}
TIER_NAMES = {"HIGH": "AAC", "LOSSLESS": _("Lossless 16-bit / 44.1 kHz"), "HI_RES_LOSSLESS": "Hi-Res"}
REPEAT_LABELS = {"off": _("off"), "all": _("queue"), "one": _("one")}
VOLUME_CELLS = 10
# Noneto symbol (docs/assets/noneto-symbol-*.svg) in half blocks: 6×6 pixels over the 3-row top bar.
# The symbol is the N of NONETO, as in the logo.
BRAND = "██▄  █\n█ ▀▄ █ ONETO\n█  ▀██"
LYRICS_MIN_ROWS = 10  # the visualizer only takes rows the lyrics can spare
VISUALIZER_ROWS = (4, 10)


def clean(value):
    return terminal_text(str(value or ""))


def artist(track):
    return clean(getattr(getattr(track, "artist", None), "name", ""))


def album(track):
    return clean(getattr(getattr(track, "album", None), "name", ""))


def duration(value):
    seconds = max(0, int(value or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


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


def cover_text(pixels, cells=12):
    """Draw the 12×12 artwork with half blocks; `cells` (a divisor of 12) shrinks it."""
    if not pixels:
        return Text("\n".join([" " * cells] * (cells // 2)))
    step = 12 // cells
    output = Text()
    for y in range(0, 12, 2 * step):
        if y:
            output.append("\n")
        for x in range(0, 12, step):
            top, bottom = pixels[y][x], pixels[y + step][x]
            output.append("▀", style=Style(color=f"rgb({top[0]},{top[1]},{top[2]})",
                                            bgcolor=f"rgb({bottom[0]},{bottom[1]},{bottom[2]})"))
    return output


def level_bar(volume):
    filled = round(max(0, min(100, volume)) / 100 * VOLUME_CELLS)
    output = Text("━" * filled, style="bold")
    output.append("─" * (VOLUME_CELLS - filled), style="dim")
    return output


class LibraryScreen(ModalScreen):
    """Playlist picker that replaces the old sidebar; creating and refreshing live here too."""
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, playlists, writing=False):
        super().__init__()
        self.playlists = playlists
        self.writing = writing

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(_("Your library"), classes="dialog-title")
            if self.playlists:
                yield ListView(*(ListItem(Label(Text(clean(p.name)))) for p in self.playlists), id="playlists")
            else:
                yield Static(_("No playlists. Create one or refresh."), id="empty-library", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(_("+ New playlist"), id="new-playlist", variant="primary", disabled=self.writing)
                yield Button(_("Refresh"), id="refresh")
                yield Button(_("Close"), id="cancel")

    def on_mount(self):
        if self.playlists:
            self.query_one(ListView).focus()

    @on(ListView.Selected)
    def choose(self, event):
        if event.list_view.index is not None:
            self.dismiss(("open", self.playlists[event.list_view.index]))

    @on(Button.Pressed)
    def pressed(self, event):
        event.stop()
        self.dismiss({"new-playlist": ("new", None), "refresh": ("refresh", None)}.get(event.button.id))


class CreatePlaylist(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(_("New playlist"), classes="dialog-title")
            yield Input(placeholder=_("Your playlist's name"), max_length=100, id="playlist-name")
            with Horizontal(classes="dialog-actions"):
                yield Button(_("Cancel"), id="cancel")
                yield Button(_("Create"), id="confirm", variant="primary")

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#confirm")
    def confirm(self):
        title = self.query_one(Input).value.strip()
        if title:
            self.dismiss(title)

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)


class ChoosePlaylist(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, playlists):
        super().__init__()
        self.playlists = playlists

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(_("Add to one of your playlists"), classes="dialog-title")
            yield ListView(*(ListItem(Label(Text(clean(p.name)))) for p in self.playlists), id="destinations")
            yield Button(_("Cancel"), id="cancel")

    def on_mount(self):
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def choose(self, event):
        if event.list_view.index is not None:
            self.dismiss(self.playlists[event.list_view.index])

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)


class TrackTable(DataTable):
    """Play a track with one mouse click while keeping row selection for actions."""

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


class SeekProgress(ProgressBar):
    class SeekRequested(Message):
        def __init__(self, fraction):
            super().__init__()
            self.fraction = fraction

    def on_click(self, event):
        if event.button != 1:
            return
        bar = self.query_one("#bar")
        region = bar.content_region
        if region.width:
            fraction = (event.screen_x - region.x) / max(1, region.width - 1)
            self.post_message(self.SeekRequested(max(0.0, min(1.0, fraction))))
            event.stop()


class VolumeBar(Static):
    """Click a cell to jump to that volume, in steps of 10%."""

    class VolumeRequested(Message):
        def __init__(self, volume):
            super().__init__()
            self.volume = volume

    def on_click(self, event):
        if event.button == 1 and self.content_region.width:
            cell = event.screen_x - self.content_region.x
            self.post_message(self.VolumeRequested(max(0, min(VOLUME_CELLS, cell + 1)) * 100 // VOLUME_CELLS))
            event.stop()



def offered_tier(track):
    """Best tier TIDAL lists for a track, or None when it does not say."""
    tags = getattr(track, "media_metadata_tags", None) or ()
    if "HIRES_LOSSLESS" in tags:
        return "HI_RES_LOSSLESS"
    if "LOSSLESS" in tags:
        return "LOSSLESS"
    quality = getattr(track, "audio_quality", None)
    return quality if quality in TIER_NAMES else None

class Views(TabbedContent):
    """Tabs that report their size after layout, unlike App resize events."""

    class Resized(Message):
        pass

    def on_resize(self):
        self.post_message(self.Resized())


class NonetoApp(App):
    TITLE = "Noneto CLI"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { background: $background; color: $foreground; }
    Button { min-width: 3; height: 1; border: none; margin-right: 1; }
    Button.-primary { background: $primary; color: $button-color-foreground; }
    #top { height: 5; background: $panel; padding: 1 0; }
    #top > #quality { margin-top: 1; }
    #top > Button { margin: 1 1 0 0; }
    #brand { width: auto; height: 3; padding: 0 1 0 2; text-style: bold; }
    #library { max-width: 32; background: $surface; }
    #search { width: 1fr; height: 3; border: round $border; padding: 0 1; margin-right: 1; background: $panel; }
    #search:focus { border: round $primary; background: $panel; }
    #search-button { min-width: 8; }
    #quality { width: auto; padding: 0 1; text-style: bold; color: $primary; }
    #quality.degraded { color: $warning; }
    #views-wrap, #views { height: 1fr; }
    #views-wrap { margin-top: 1; }
    #hint { position: absolute; height: 1; padding: 0 1; color: $text-muted;
            text-wrap: nowrap; text-overflow: ellipsis; }
    #views ContentSwitcher { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #tracks { height: 1fr; background: $background; }
    #playlist-results { height: 1fr; background: $background; }
    DataTable > .datatable--header { background: $background; color: $text-muted; text-style: bold; }
    #more { width: 100%; margin: 0; }
    #rail { height: 1fr; padding: 0 1; scrollbar-size: 1 1; }
    #up-next { height: auto; color: $text-muted; }
    #lyrics-pane { height: 1fr; }
    #cover { width: 14; height: 6; margin: 1 1 0 1; }
    #lyrics-body { width: 1fr; padding: 0 1; }
    #lyrics-scroll { height: 1fr; scrollbar-size: 1 1; }
    #lyrics-text { height: auto; }
    #visualizer { height: 6; padding: 0 1; color: $foreground; }
    #song-actions { height: 1; background: $surface; padding: 0 1; }
    #action-target { width: 1fr; height: 1; color: $text-muted; }
    #song-actions Button { background: $panel; }
    #player { height: 5; background: $panel; padding: 1 1; }
    #mini-cover { width: 6; height: 3; margin-right: 2; }
    #player-body { width: 1fr; }
    #now-row, #now-detail, #seek { height: 1; }
    #now-detail { color: $text-muted; }
    #now-playing { width: 1fr; height: 1; }
    #controls { width: auto; height: 1; }
    #controls Button { min-width: 4; margin: 0 1; background: $panel; }
    #controls Button.-primary { min-width: 6; background: $primary; }
    #shuffle, #repeat { color: $text-muted; }
    #controls Button.active { color: $foreground; text-style: bold; }
    #volume { width: 1fr; height: 1; align-horizontal: right; }
    #volume Button { min-width: 3; margin: 0; background: $panel; color: $text-muted; }
    #volume-bar { width: 10; }
    #volume-label { width: 5; text-align: right; }
    #elapsed, #total { width: 6; color: $text-muted; }
    #total { text-align: right; }
    #progress { width: 1fr; height: 1; }
    #progress > #bar { width: 1fr; }
    #progress:hover { background: $surface-lighten-1; }
    #progress > .bar--bar, #progress > .bar--complete { color: $primary; }
    #statusbar { height: 1; padding: 0 1; }
    #message { width: 1fr; height: 1; color: $text-muted; }
    #mode { width: auto; height: 1; color: $text-muted; padding-left: 2; }
    Footer { background: $background; }
    ModalScreen { align: center middle; background: $background 65%; }
    #dialog { width: 60; max-width: 90%; height: auto; max-height: 85%; padding: 1 2; background: $panel; border: solid $border; }
    .dialog-title { height: 2; text-style: bold; }
    .dialog-actions { height: 1; margin-top: 1; }
    #playlists, #destinations { height: auto; max-height: 18; margin-bottom: 1; }
    #dialog ListItem { padding: 0 1; }
    """
    BINDINGS = [Binding("ctrl+q,ctrl+c", "quit", _("Quit"), priority=True),
                Binding("ctrl+f", "search", _("Search"), priority=True),
                Binding("ctrl+l", "library", _("Library"), priority=True),
                Binding("ctrl+n", "new_playlist", _("New playlist")),
                Binding("space", "pause", _("Pause")), Binding("ctrl+right", "next", _("Next")),
                Binding("ctrl+t", "next_theme", _("Theme"))]

    def __init__(self, library, playback, quality="HI_RES_LOSSLESS", initial_playlist=None, user_name=None,
                 theme=None, on_theme=None):
        super().__init__()
        self.register_theme(TIDAL_DARK)
        self.theme = theme if theme in THEMES else TIDAL_DARK.name
        self.on_theme = on_theme  # persists the choice; the UI itself never writes files
        self.library, self.playback = library, playback
        self.quality, self.initial_playlist = quality, initial_playlist
        self.user_name = user_name
        self.playlists = []
        self.tracks = []
        self.current_playlist = None
        self.playlist_results = []
        self._loading_playlists = False
        self.search_query = ""
        self._generation = 0
        self._loading_more = False
        self._writing = False
        self._refreshing = False
        self._last_error = ""
        self._current_track_id = None
        self._lyrics = []
        self._timed_lyrics = False
        self._active_line = None
        self._media_generation = 0
        self._source_name = ""
        self._queue_signature = None
        self._visualizer_on = False
        self._shown = {}
        self._hint_text = ""

    def compose(self):
        with Horizontal(id="top"):
            yield Static(BRAND, id="brand", markup=False)
            library = Button(_("Library ▾"), id="library")
            library.tooltip = _("{name}'s playlists · Ctrl+L", name=clean(self.user_name or _("User")))
            yield library
            yield Input(placeholder=_("Search songs or playlists…"), max_length=200, id="search")
            yield Button(_("Search"), id="search-button", variant="primary")
            yield Static("◆", id="quality", markup=False)
        with Vertical(id="views-wrap"):
            with Views(id="views", initial="songs-view"):
                with TabPane(_("Songs"), id="songs-view"):
                    yield TrackTable(id="tracks", cursor_type="row", zebra_stripes=True)
                    yield Button(_("More results"), id="more")
                with TabPane("Playlists", id="playlists-view"):
                    yield ListView(id="playlist-results")
                    yield Static(_("No playlists in this search."), id="empty-results", markup=False)
                    yield Button(_("More results"), id="more-playlists")
                with TabPane(_("Queue"), id="queue-view"):
                    with VerticalScroll(id="rail"):
                        yield Static(_("No songs in the queue."), id="up-next", markup=False)
                with TabPane(_("Lyrics"), id="lyrics-view"):
                    with Horizontal(id="lyrics-pane"):
                        yield Static(cover_text(None), id="cover")
                        with Vertical(id="lyrics-body"):
                            with VerticalScroll(id="lyrics-scroll"):
                                yield Static(_("Play a song to see its lyrics."), id="lyrics-text", markup=False)
                    yield Visualizer(lambda count: self.playback.bands(count), id="visualizer")
            # Overlaid on the free end of the tab row; _place_hint keeps it clear of the tabs.
            yield Static("", id="hint", markup=False)
        with Horizontal(id="song-actions"):
            yield Static(_("Select a song"), id="action-target", markup=False)
            yield Button(_("+ Queue"), id="track-enqueue")
            yield Button("+ Playlist", id="track-add")
        with Horizontal(id="player"):
            yield Static(cover_text(None, 6), id="mini-cover")
            with Vertical(id="player-body"):
                with Horizontal(id="now-row"):
                    yield Static(_("No song"), id="now-playing", markup=False)
                    with Horizontal(id="controls"):
                        for identifier, label, tooltip in (
                                ("shuffle", "⇄", _("Shuffle")), ("previous", "◁◁", _("Previous")),
                                ("pause", "▷", _("Play or pause")), ("next", "▷▷", _("Next")),
                                ("repeat", "↻", _("Repeat: off / queue / song"))):
                            button = Button(label, id=identifier,
                                            variant="primary" if identifier == "pause" else "default")
                            button.tooltip = tooltip
                            yield button
                    with Horizontal(id="volume"):
                        yield Button("−", id="quieter")
                        yield VolumeBar(level_bar(60), id="volume-bar")
                        yield Button("+", id="louder")
                        yield Static("60%", id="volume-label", markup=False)
                yield Static("", id="now-detail", markup=False)
                with Horizontal(id="seek"):
                    yield Static("0:00", id="elapsed", markup=False)
                    yield SeekProgress(total=1, show_percentage=False, show_eta=False, id="progress")
                    yield Static("0:00", id="total", markup=False)
        with Horizontal(id="statusbar"):
            yield Static(_("Loading library…"), id="message", markup=False)
            yield Static("", id="mode", markup=False)
        yield Footer()

    def _handle_exception(self, error):
        # Textual's default fatal screen includes locals. Account objects must never
        # reach that renderer, even when an unexpected programming error occurs.
        failure("ui.fatal", error)
        if self._exception is None:
            self._exception = error
            self._exception_event.set()
        self.exit(return_code=1, message=_("The interface closed because of an error. See the Noneto CLI logs."))

    async def on_mount(self):
        self._now_playing = self.query_one("#now-playing", Static)
        self._now_detail = self.query_one("#now-detail", Static)
        self._mini_cover = self.query_one("#mini-cover", Static)
        self._hint = self.query_one("#hint", Static)
        self._cover = self.query_one("#cover", Static)
        self._lyrics_text = self.query_one("#lyrics-text", Static)
        self._progress = self.query_one("#progress", ProgressBar)
        self._volume_bar = self.query_one("#volume-bar", Static)
        self._volume_label = self.query_one("#volume-label", Static)
        self._up_next = self.query_one("#up-next", Static)
        self._elapsed = self.query_one("#elapsed", Static)
        self._total = self.query_one("#total", Static)
        self._mode = self.query_one("#mode", Static)
        self._quality = self.query_one("#quality", Static)
        self._show_quality((), None)
        self._pause_button = self.query_one("#pause", Button)
        self._shuffle_button = self.query_one("#shuffle", Button)
        self._repeat_button = self.query_one("#repeat", Button)
        self._action_target = self.query_one("#action-target", Static)
        self._message_widget = self.query_one("#message", Static)
        self._views = self.query_one("#views", TabbedContent)
        table = self.query_one(TrackTable)
        self._mark_column, *_rest = table.add_columns("#", _("Title"), _("Duration"))
        self._update_action_target()
        self.query_one("#more", Button).display = False
        self.query_one("#more-playlists", Button).display = False
        self._views.hide_tab("playlists-view")
        self.set_interval(0.25, self.update_player)
        self.refresh_library(initial=True)

    def hint(self, value):
        self._hint_text = clean(value)
        self._hint.update(self._hint_text)
        self._place_hint()

    def _place_hint(self):
        wrap = self.query_one("#views-wrap").region
        tabs_end = max((tab.region.right for tab in self.query("#views Tab")), default=wrap.x)
        width = min(max(0, wrap.right - tabs_end - 1), cell_len(self._hint_text) + 2)
        self._hint.styles.width = width
        self._hint.styles.offset = (wrap.width - width, 0)
        self._hint.display = width > 4

    @on(Views.Resized)
    def _views_resized(self):
        self._place_hint()
        self._update_visualizer()

    @on(TabbedContent.TabActivated)
    def _tab_changed(self):
        self.call_after_refresh(self._update_visualizer)
        if self._views.active == "songs-view":
            self.call_after_refresh(self.query_one("#tracks", DataTable).focus)
        elif self._views.active == "playlists-view":
            self.call_after_refresh(self.query_one("#playlist-results", ListView).focus)

    def _update_visualizer(self):
        visualizer = self.query_one(Visualizer)
        area = self.query_one("#views ContentSwitcher").size
        rows = min(VISUALIZER_ROWS[1], area.height - LYRICS_MIN_ROWS)
        show = self._views.active == "lyrics-view" and rows >= VISUALIZER_ROWS[0] and area.width >= 40
        visualizer.display = show
        if show:
            visualizer.styles.height = rows
            visualizer.start()
        else:
            visualizer.stop()
        if show != self._visualizer_on:
            self._visualizer_on = show
            self.control("visualizer", show)

    async def api(self, operation, *args):
        return await asyncio.wrap_future(self.library.submit(operation, *args))

    def message(self, value):
        self._message_widget.update(clean(value))

    def report(self, error):
        failure("ui.failed", error)
        self.message(str(error) if isinstance(error, AppError) else _("Could not complete: {label}. You can try again.", label=error_label(error)))

    @work
    async def refresh_library(self, initial=False, select=None):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            playlists = await self.api("playlists")
            self.playlists = playlists
            self.message(_("{count} playlists · Click Library to open another", count=len(playlists)))
            if select is not None:
                self.open_playlist(select)
            elif initial and self.initial_playlist:
                self.open_playlist(await self.api("playlist", self.initial_playlist))
            elif initial and playlists:
                self.open_playlist(playlists[0])
        except Exception as error:
            self.report(error)
        finally:
            self._refreshing = False

    def action_library(self):
        if not isinstance(self.screen, ModalScreen):
            self.push_screen(LibraryScreen(self.playlists, self._writing), self.library_choice)

    def library_choice(self, choice):
        action, playlist = choice or (None, None)
        if action == "open":
            self.open_playlist(playlist)
        elif action == "new":
            self.action_new_playlist()
        elif action == "refresh":
            self.refresh_library()

    def begin_view(self, heading):
        self._generation += 1
        self._loading_more = False
        self._loading_playlists = False
        self.tracks = []
        self.query_one(DataTable).clear()
        self._update_action_target()
        self.query_one("#library", Button).label = Text(f"{clean(heading)[:26]} ▾")
        self.hint(_("Loading…"))
        self.query_one("#more", Button).display = False
        self.query_one("#more", Button).disabled = False
        self.query_one("#more-playlists", Button).disabled = False
        self._views.active = "songs-view"
        return self._generation

    def open_playlist(self, playlist):
        self.current_playlist = playlist
        self.search_query = ""
        self._views.hide_tab("playlists-view")
        generation = self.begin_view(playlist.name)
        self.call_after_refresh(self.query_one("#tracks", DataTable).focus)
        self.load_tracks(playlist, generation)

    @work
    async def load_tracks(self, playlist, generation):
        try:
            tracks = await self.api("tracks", playlist)
            if generation == self._generation:
                self.show_tracks(tracks)
        except Exception as error:
            if generation == self._generation:
                self.report(error)
                self.hint(_("Could not load. Open it again to retry."))

    def _mark(self, index, track):
        if self._current_track_id is not None and getattr(track, "id", None) == self._current_track_id:
            return Text("▷", style="bold")
        return Text(str(index + 1), style="dim")

    def _refresh_marks(self, *track_ids):
        table = self.query_one(DataTable)
        for index, track in enumerate(self.tracks):
            if getattr(track, "id", None) in track_ids:
                table.update_cell_at(Coordinate(index, 0), self._mark(index, track))

    def show_tracks(self, tracks, append=False):
        table = self.query_one(DataTable)
        if not append:
            table.clear()
            self.tracks = []
        start = len(self.tracks)
        self.tracks.extend(tracks)
        for index, track in enumerate(tracks, start):
            title = Text(clean(track.name), style="bold")
            title.append(f"\n{artist(track)} · {album(track)}", style="dim")
            table.add_row(self._mark(index, track), title,
                          Text(duration(getattr(track, "duration", 0)), style="dim"), height=2)
        source = clean(self.current_playlist.name) if self.current_playlist else _("Search results")
        self.hint(_("{source} · {count} songs", source=source[:30], count=len(self.tracks)) if self.tracks
                  else _("No songs. Search for music and add it to a playlist."))
        self._update_action_target()

    @on(Input.Submitted, "#search")
    @on(Button.Pressed, "#search-button")
    def search(self):
        query = self.query_one("#search", Input).value.strip()
        if query:
            self.current_playlist = None
            self.search_query = query
            generation = self.begin_view(_("Results: {query}", query=query))
            self.playlist_results = []
            self.query_one("#playlist-results", ListView).clear()
            self.query_one("#empty-results", Static).update(_("Loading…"))
            self.query_one("#empty-results").display = True
            self.query_one("#more-playlists", Button).display = False
            self._views.show_tab("playlists-view")
            self.search_page(query, generation, 0)
            self.search_playlist_page(query, generation, 0)

    @work
    async def search_playlist_page(self, query, generation, offset):
        self._loading_playlists = True
        try:
            playlists = await self.api("search_playlists", query, offset)
            if generation != self._generation:
                return
            self.playlist_results.extend(playlists)
            results = self.query_one("#playlist-results", ListView)
            await results.extend(ListItem(Label(Text(clean(p.name)))) for p in playlists)
            if generation != self._generation:
                return
            if self.playlist_results and results.index is None:
                results.index = 0
            self.query_one("#empty-results", Static).update(_("No playlists in this search."))
            self.query_one("#empty-results").display = not self.playlist_results
            self.query_one("#more-playlists", Button).display = len(playlists) == 50
        except Exception as error:
            if generation == self._generation:
                self.report(error)
                self.query_one("#empty-results", Static).update(_("Search failed. Press Search to retry."))
        finally:
            if generation == self._generation:
                self._loading_playlists = False
                self.query_one("#more-playlists", Button).disabled = False

    @on(ListView.Selected, "#playlist-results")
    def open_search_playlist(self, event):
        index = event.list_view.index
        if index is not None and 0 <= index < len(self.playlist_results):
            self.open_playlist(self.playlist_results[index])

    @work
    async def search_page(self, query, generation, offset):
        try:
            tracks = await self.api("search", query, offset)
            if generation == self._generation:
                self.show_tracks(tracks, append=offset > 0)
                self.query_one("#more", Button).display = len(tracks) == 50
        except Exception as error:
            if generation == self._generation:
                self.report(error)
                self.hint(_("Search failed. Press Search to retry."))
        finally:
            if generation == self._generation:
                self._loading_more = False
                self.query_one("#more", Button).disabled = False

    def selected_track(self):
        index = self.query_one(DataTable).cursor_row
        return self.tracks[index] if 0 <= index < len(self.tracks) else None

    def _update_action_target(self):
        track = self.selected_track()
        target = Text()
        if track:
            target.append(_("Selected  "))
            target.append(clean(track.name), style="bold")
            target.append(f" · {artist(track)}")
        else:
            target.append(_("Select a song"))
        self._action_target.update(target)
        self.query_one("#track-add", Button).disabled = track is None or self._writing
        self.query_one("#track-enqueue", Button).disabled = track is None

    def control(self, action, value=None):
        try:
            self.playback.send(action, value)
        except Exception as error:
            self.report(error)

    @on(DataTable.RowSelected)
    def play_selected(self):
        self._update_action_target()
        if self.selected_track() is not None:
            self._source_name = (clean(self.current_playlist.name) if self.current_playlist else
                                 _("Search results") if self.search_query else _("Queue"))
            self.control("play", (tuple(self.tracks), self.query_one(DataTable).cursor_row))

    @on(DataTable.RowHighlighted)
    def selected_row_changed(self):
        self._update_action_target()

    @on(SeekProgress.SeekRequested)
    def seek_requested(self, event):
        state = self.playback.snapshot()
        if state.status in ("Playing", "Paused") and state.duration > 0:
            self.control("seek_absolute", round(state.duration * event.fraction, 2))

    @on(VolumeBar.VolumeRequested)
    def volume_requested(self, event):
        change = event.volume - self.playback.snapshot().volume
        if change:
            self.control("volume", change)

    def action_search(self):
        if not isinstance(self.screen, ModalScreen):
            self.query_one("#search", Input).focus()

    def action_pause(self):
        state = self.playback.snapshot()
        if state.status in ("Playing", "Paused"):
            self.control("pause")
        elif self.selected_track() is not None:
            self.play_selected()

    def action_next(self):
        self.control("next")

    def action_next_theme(self):
        self.theme = THEMES[(THEMES.index(self.theme) + 1) % len(THEMES)] if self.theme in THEMES else THEMES[0]
        self.message(_("Theme: {name} · Ctrl+T to change", name=self.theme))
        if self.on_theme is not None:
            try:
                self.on_theme(self.theme)
            except OSError as error:
                failure("settings.failed", error)

    def action_new_playlist(self):
        if not self._writing:
            self.push_screen(CreatePlaylist(), self.create_playlist)

    @work
    async def create_playlist(self, title):
        if title is None or self._writing:
            return
        self._writing = True
        self._update_action_target()
        try:
            playlist = await self.api("create", title)
            self.message(_("Playlist created in your TIDAL account."))
            self.refresh_library(select=playlist)
        except Exception as error:
            self.report(error)
            self.message(_("The creation was not confirmed. Refresh the library before retrying."))
        finally:
            self._writing = False
            self._update_action_target()

    @work
    async def add_track(self, track):
        if self._writing:
            return
        self._writing = True
        self._update_action_target()
        try:
            own = [p for p in self.playlists if self.library.catalog.editable(p)]
            if not own:
                self.message(_("First create a playlist of your own from Library."))
                return
            playlist = await self.push_screen_wait(ChoosePlaylist(own))
            if playlist is not None:
                result = await self.api("add", playlist, track)
                self.message(_("Song added.") if result else _("No changes: the song is already there or unavailable."))
                if self.current_playlist and str(self.current_playlist.id) == str(playlist.id):
                    self.open_playlist(playlist)
        except Exception as error:
            self.report(error)
        finally:
            self._writing = False
            self._update_action_target()

    @on(Button.Pressed)
    def button(self, event):
        identifier = event.button.id
        if identifier == "library":
            self.action_library()
        elif identifier == "more-playlists" and not self._loading_playlists:
            event.button.disabled = True
            self.search_playlist_page(self.search_query, self._generation, len(self.playlist_results))
        elif identifier == "more" and not self._loading_more:
            self._loading_more = True
            event.button.disabled = True
            self.search_page(self.search_query, self._generation, len(self.tracks))
        elif identifier == "pause":
            self.action_pause()
        elif identifier in ("next", "previous"):
            self.control(identifier)
        elif identifier in ("shuffle", "repeat"):
            self.control(identifier)
        elif identifier == "track-add":
            track = self.selected_track()
            if track is not None:
                self.add_track(track)
        elif identifier == "track-enqueue":
            track = self.selected_track()
            if track is not None:
                self.control("enqueue", track)
                self.message(_("Song added to the end of the queue."))
        elif identifier in ("quieter", "louder"):
            self.control("volume", -5 if identifier == "quieter" else 5)

    def _show(self, widget, value, layout=False):
        """Runs 4 times a second: skip unchanged widgets, and fixed-size ones skip the screen-wide layout pass."""
        if self._shown.get(widget.id) != value:
            self._shown[widget.id] = value
            widget.update(value, layout=layout)

    def _show_quality(self, audio, track):
        """Badge: what mpv decodes now; before that, only what was asked for, never as a promise.

        Getting less than the track offers (within what was asked) is flagged: that is a real loss,
        unlike a track that only exists in 16-bit / 44.1 kHz.
        """
        key = (audio, getattr(track, "id", None))
        if self._shown.get("quality-key") == key:
            return
        self._shown["quality-key"] = key
        short, detail = QUALITY_LABELS.get(self.quality, (clean(self.quality), clean(self.quality)))
        asked = _("Requested: {short} ({detail}).", short=short, detail=detail)
        best = offered_tier(track)
        degraded = False
        if track is None:
            text, tip = _("Requested: {short}", short=short), f"{asked} {_("While playing, the quality that arrives is shown.")}"
        elif not audio:
            text, tip = "◆ …", f"{asked} {_("Waiting for audio.")}"
        else:
            label, received, tier = audio
            expected = min(TIER_RANK.get(self.quality, 0), TIER_RANK[best]) if best else None
            degraded = expected is not None and tier is not None and TIER_RANK[tier] < expected
            text = f"◇ {label} ↓" if degraded else f"◆ {label}"
            tip = f"{asked} {_("Received: {received}.", received=received)}"
            if best:
                tip += " " + _("This track is available in: {tier}.", tier=TIER_NAMES[best])
            if degraded:
                tip += " " + _("TIDAL delivered less than this track offers.")
                emit("playback.degraded", track_id=getattr(track, "id", None), quality=received)
        self._quality.update(text, layout=True)
        self._quality.tooltip = tip
        self._quality.set_class(degraded, "degraded")

    def _label(self, button, value):
        if self._shown.get(button.id) != value:
            self._shown[button.id] = value
            button.label = value

    def update_player(self):
        state = self.playback.snapshot()
        track = None
        if 0 <= state.index < len(state.tracks):
            track = state.tracks[state.index]
        track_id = getattr(track, "id", None)
        if track_id != self._current_track_id:
            previous, self._current_track_id = self._current_track_id, track_id
            self._refresh_marks(previous, track_id)
            self._media_generation += 1
            self._lyrics, self._active_line = [], None
            self._lyrics_text.update(_("Loading lyrics…") if track else _("Play a song to see its lyrics."))
            self._cover.update(cover_text(None))
            self._mini_cover.update(cover_text(None, 6))
            if track:
                self.load_media(track, self._media_generation)
        now = Text()
        detail = ""
        if track:
            now.append(f"{'❚❚' if state.paused else '▷'} {clean(track.name)}", style="bold")
            detail = " · ".join(part for part in (artist(track), album(track), self._source_name) if part)
        else:
            now.append(_("No song"), style="dim")
        self._show(self._now_playing, now)
        self._show(self._now_detail, detail)
        self._show(self._elapsed, duration(state.position))
        self._show(self._total, duration(state.duration or getattr(track, "duration", 0)))
        self._progress.update(total=max(1, state.duration or getattr(track, "duration", 0) or 1),
                              progress=max(0, state.position))
        self._show(self._volume_bar, level_bar(state.volume))
        self._show(self._volume_label, f"{state.volume}%")
        queue_signature = (id(state.tracks), state.index)
        if queue_signature != self._queue_signature:
            self._queue_signature = queue_signature
            next_tracks = state.tracks[max(0, state.index + 1):]
            self._views.get_tab("queue-view").label = _("Queue · {count}", count=len(next_tracks)) if next_tracks else _("Queue")
            self.call_after_refresh(self._place_hint)
            if next_tracks:
                queue_text = Text()
                for number, queued in enumerate(next_tracks[:100], 1):
                    if number > 1:
                        queue_text.append("\n")
                    queue_text.append(f"{number:>3}  ", style="dim")
                    queue_text.append(clean(queued.name), style="bold")
                    queue_text.append(f" · {artist(queued)}", style="dim")
                if len(next_tracks) > 100:
                    queue_text.append("\n\n" + _("+ {count} songs", count=len(next_tracks) - 100))
                self._up_next.update(queue_text)
            else:
                self._up_next.update(_("No songs in the queue."))
        self._update_lyrics(state.position)
        self._label(self._pause_button, "▷" if state.paused or state.index < 0 else "❚❚")
        self._shuffle_button.set_class(state.shuffle, "active")
        self._repeat_button.set_class(state.repeat != "off", "active")
        self._label(self._repeat_button, "↻1" if state.repeat == "one" else "↻")
        mode = f"{_(state.status)} · {_("Repeat: {mode}", mode=REPEAT_LABELS[state.repeat])}"
        self._show(self._mode, mode + (" · " + _("Shuffle") if state.shuffle else ""), layout=True)  # auto width
        self._show_quality(state.audio, track)
        if state.error and state.error != self._last_error:
            self.message(state.error)
        self._last_error = state.error

    @work
    async def load_media(self, track, generation):
        try:
            plain, subtitles, rtl = await self.api("lyrics", track)
            if generation == self._media_generation:
                self._lyrics, self._timed_lyrics = lyric_lines(plain, subtitles)
                self._active_line = None
                self._update_lyrics(self.playback.snapshot().position)
        except Exception as error:
            if generation == self._media_generation:
                failure("lyrics.unavailable", error)
                self._lyrics_text.update(_("No lyrics available for this song."))
        try:
            pixels = await self.api("cover", track)
            if generation == self._media_generation:
                self._cover.update(cover_text(pixels))
                self._mini_cover.update(cover_text(pixels, 6))
        except Exception as error:
            if generation == self._media_generation:
                failure("cover.unavailable", error)

    def _update_lyrics(self, position):
        if not self._lyrics:
            return
        active = (bisect.bisect_right([when for when, _line in self._lyrics], position) - 1
                  if self._timed_lyrics else -1)
        if active == self._active_line:
            return
        self._active_line = active
        output = Text()
        for index, (_when, line) in enumerate(self._lyrics):
            output.append(line or " ", style="bold" if index == active else "dim")
            if index < len(self._lyrics) - 1:
                output.append("\n")
        self._lyrics_text.update(output)
        if active >= 0:
            self.query_one("#lyrics-scroll", VerticalScroll).scroll_to(y=max(0, active - 3), animate=False)
