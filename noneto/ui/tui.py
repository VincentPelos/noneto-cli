"""Mouse and keyboard interface; all account and playback work is injected.

NonetoApp composes the pieces and runs the workflows (library, search, playlists, media
loading). Each piece lives in its own module:

    top.py        logo, Library ▾, search box     quality.py    quality badge
    tracks.py     Songs table                     queue.py      Queue tab
    lyrics.py     Lyrics tab (layouts, lyrics)    visualizer.py track aura under the lyrics
    cover.py      both covers (image or blocks)   player.py     player bar and its controls
    dialogs.py    modal dialogs                   themes.py     palettes
"""
import asyncio

from rich.cells import cell_len
from rich.text import Text
from textual import on, work
from textual.app import App
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Static, TabbedContent, TabPane

from ..diagnostics import failure
from ..errors import AppError, error_label
from ..i18n import _
from . import cover, dialogs, lyrics, player, quality, queue, top, tracks, visualizer
from .common import Changes, artist, clean
from .cover import CoverArt
from .dialogs import ChoosePlaylist, CreatePlaylist, LibraryScreen
from .lyrics import LAYOUTS, LyricsPanel
from .player import PlayerBar, playback_mode
from .quality import QualityBadge
from .queue import QueueList
from .themes import CUSTOM_THEMES, TIDAL_DARK, available_themes
from .top import TopBar
from .tracks import TrackTable
from .visualizer import MODE_LABELS, Visualizer, available_visualizer_modes

BASE_CSS = """
Screen { background: $background; color: $foreground; }
Button { min-width: 3; height: 1; border: none; margin-right: 1; }
Button.-primary { background: $primary; color: $button-color-foreground; }
"""
VIEWS_CSS = """
#views-wrap, #views { height: 1fr; }
#views-wrap { margin-top: 1; }
#hint { position: absolute; height: 1; padding: 0 1; color: $text-muted;
        text-wrap: nowrap; text-overflow: ellipsis; }
#views ContentSwitcher { height: 1fr; }
TabPane { height: 1fr; padding: 0; }
#playlist-results { height: 1fr; background: $background; }
#more { width: 100%; margin: 0; }
"""
STATUS_CSS = """
#song-actions { height: 1; background: $surface; padding: 0 1; }
#action-target { width: 1fr; height: 1; color: $text-muted; }
#song-actions Button { background: $panel; }
#statusbar { height: 1; padding: 0 1; }
#message { width: 1fr; height: 1; color: $text-muted; }
#mode { width: auto; height: 1; color: $text-muted; padding-left: 2; }
Footer { background: $background; }
"""


class Views(TabbedContent):
    """Tabs that report their size after layout, unlike App resize events."""

    class Resized(Message):
        pass

    def on_resize(self):
        self.post_message(self.Resized())


class NonetoApp(App):
    TITLE = "Noneto CLI"
    ENABLE_COMMAND_PALETTE = False
    CSS = "".join((BASE_CSS, top.CSS, quality.CSS, VIEWS_CSS, tracks.CSS, queue.CSS, lyrics.CSS,
                   cover.CSS, visualizer.CSS, player.CSS, STATUS_CSS, dialogs.CSS))
    BINDINGS = [Binding("ctrl+q,ctrl+c", "quit", _("Quit"), priority=True),
                Binding("ctrl+f", "search", _("Search"), priority=True),
                Binding("ctrl+l", "library", _("Library"), priority=True),
                Binding("ctrl+n", "new_playlist", _("New playlist")),
                Binding("space", "pause", _("Pause")), Binding("ctrl+right", "next", _("Next")),
                Binding("ctrl+t", "next_theme", _("Theme")),
                Binding("ctrl+g", "next_visualizer", _("Aura")),
                Binding("ctrl+o", "toggle_lyrics_layout", _("Cover"))]

    def __init__(self, library, playback, quality="HI_RES_LOSSLESS", initial_playlist=None, user_name=None,
                 theme=None, on_theme=None, visualizer_mode=None, on_visualizer_mode=None, artwork=None,
                 lyrics_layout=None, on_lyrics_layout=None):
        super().__init__()
        for custom_theme in CUSTOM_THEMES.values():
            self.register_theme(custom_theme)
        themes = available_themes()
        modes = available_visualizer_modes()
        self.theme = theme if theme in themes else TIDAL_DARK.name
        self.on_theme = on_theme  # persists the choice; the UI itself never writes files
        self.visualizer_mode = visualizer_mode if visualizer_mode in modes else "classic"
        self.on_visualizer_mode = on_visualizer_mode
        self.lyrics_layout = lyrics_layout if lyrics_layout in LAYOUTS else "lyrics"
        self.on_lyrics_layout = on_lyrics_layout
        self.library, self.playback = library, playback
        self.covers = CoverArt(artwork)  # artwork from terminal_artwork(); None draws half blocks
        self.quality, self.initial_playlist = quality, initial_playlist
        self.user_name = user_name
        self.playlists = []
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
        self._view_requests = set()
        self._media_requests = set()
        self._media_generation = 0
        self._source_name = ""
        self._queue_signature = None
        self._visualizer_on = False
        self._changes = Changes()
        self._hint_text = ""
        self._table = None  # set on mount

    def compose(self):
        yield TopBar(self.user_name, self.quality, id="top")
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
                    yield QueueList(id="rail")
                with TabPane(_("Lyrics"), id="lyrics-view"):
                    yield LyricsPanel(self.covers, self.lyrics_layout, id="lyrics-pane")
                    yield Visualizer(lambda count: self.playback.bands(count), mode=self.visualizer_mode, id="visualizer")
            # Overlaid on the free end of the tab row; _place_hint keeps it clear of the tabs.
            yield Static("", id="hint", markup=False)
        with Horizontal(id="song-actions"):
            yield Static(_("Select a song"), id="action-target", markup=False)
            yield Button(_("+ Queue"), id="track-enqueue")
            yield Button("+ Playlist", id="track-add")
        yield PlayerBar(self.covers.widget("mini-cover", 6, 3), id="player")
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
        self._table = self.query_one(TrackTable)
        self._player = self.query_one(PlayerBar)
        self._lyrics = self.query_one(LyricsPanel)
        self._queue = self.query_one(QueueList)
        self._quality = self.query_one(QualityBadge)
        self._hint = self.query_one("#hint", Static)
        self._mode = self.query_one("#mode", Static)
        self._action_target = self.query_one("#action-target", Static)
        self._message_widget = self.query_one("#message", Static)
        self._views = self.query_one("#views", TabbedContent)
        self._update_action_target()
        self.query_one("#more", Button).display = False
        self.query_one("#more-playlists", Button).display = False
        self._views.hide_tab("playlists-view")
        self.set_interval(0.25, self.update_player)
        self.refresh_library(initial=True)

    @property
    def tracks(self):
        return self._table.tracks

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
            self.call_after_refresh(self._table.focus)
        elif self._views.active == "playlists-view":
            self.call_after_refresh(self.query_one("#playlist-results", ListView).focus)

    def _update_visualizer(self):
        show = self.query_one(Visualizer).fit(self.query_one("#views ContentSwitcher").size,
                                              self._views.active == "lyrics-view")
        if show != self._visualizer_on:
            self._visualizer_on = show
            self.control("visualizer", show)

    async def api(self, operation, *args):
        future = self.library.submit(operation, *args)
        requests = (self._media_requests if operation in ("lyrics", "artwork") else
                    self._view_requests if operation in ("tracks", "search", "search_playlists") else None)
        if requests is not None:
            requests.add(future)
        try:
            return await asyncio.wrap_future(future)
        finally:
            if requests is not None:
                requests.discard(future)

    @staticmethod
    def _cancel_requests(requests):
        for future in tuple(requests):
            future.cancel()

    async def on_unmount(self):
        self._cancel_requests(self._view_requests)
        self._cancel_requests(self._media_requests)
        if self._table is not None:  # a failed compose must not hide its own error
            await self._table.cancel_fill()

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
        self._cancel_requests(self._view_requests)
        self._generation += 1
        self._loading_more = False
        self._loading_playlists = False
        self._table.reset()
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
        self.call_after_refresh(self._table.focus)
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

    def show_tracks(self, tracks, append=False):
        self._table.show(tracks, append)
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
        return self._table.selected()

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

    @on(TrackTable.RowSelected)
    def play_selected(self):
        self._update_action_target()
        if self.selected_track() is not None:
            self._source_name = (clean(self.current_playlist.name) if self.current_playlist else
                                 _("Search results") if self.search_query else _("Queue"))
            self.control("play", (tuple(self.tracks), self._table.cursor_row))

    @on(TrackTable.RowHighlighted)
    def selected_row_changed(self):
        self._update_action_target()

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
        themes = available_themes()
        for custom_theme in CUSTOM_THEMES.values():
            if custom_theme.name not in self.available_themes:
                self.register_theme(custom_theme)
        self.theme = themes[(themes.index(self.theme) + 1) % len(themes)] if self.theme in themes else themes[0]
        self.message(_("Theme: {name} · Ctrl+T to change", name=self.theme))
        if self.on_theme is not None:
            try:
                self.on_theme(self.theme)
            except OSError as error:
                failure("settings.failed", error)

    @on(Visualizer.Clicked)
    def action_next_visualizer(self):
        modes = available_visualizer_modes()
        self.visualizer_mode = (modes[(modes.index(self.visualizer_mode) + 1) % len(modes)]
                                if self.visualizer_mode in modes else modes[0])
        self.query_one(Visualizer).set_mode(self.visualizer_mode)
        label = MODE_LABELS.get(self.visualizer_mode)
        self.message(_("Track aura: {name} · Ctrl+G to change",
                       name=_(label) if label else self.visualizer_mode))
        if self.on_visualizer_mode is not None:
            try:
                self.on_visualizer_mode(self.visualizer_mode)
            except OSError as error:
                failure("settings.failed", error)

    @on(LyricsPanel.CoverClicked)
    def action_toggle_lyrics_layout(self):
        self.lyrics_layout = "cover" if self.lyrics_layout == "lyrics" else "lyrics"
        self._lyrics.set_layout(self.lyrics_layout)
        self.message(_("Lyrics tab: cover only · Ctrl+O to change") if self.lyrics_layout == "cover"
                     else _("Lyrics tab: cover and lyrics · Ctrl+O to change"))
        if self.on_lyrics_layout is not None:
            try:
                self.on_lyrics_layout(self.lyrics_layout)
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
        # Player controls are handled by PlayerBar and never reach here.
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
        elif identifier == "track-add":
            track = self.selected_track()
            if track is not None:
                self.add_track(track)
        elif identifier == "track-enqueue":
            track = self.selected_track()
            if track is not None:
                self.control("enqueue", track)
                self.message(_("Song added to the end of the queue."))

    def update_player(self):
        state = self.playback.snapshot()
        track = None
        if 0 <= state.index < len(state.tracks):
            track = state.tracks[state.index]
        track_id = getattr(track, "id", None)
        if track_id != self._current_track_id:
            self._cancel_requests(self._media_requests)
            self._current_track_id = track_id
            self._table.set_current(track_id)
            self._media_generation += 1
            self._lyrics.reset(playing=track is not None)
            self.covers.show()
            if track:
                self.load_media(track, self._media_generation)
        self._player.show(state, track, self._source_name)
        queue_signature = (id(state.tracks), state.index)
        if queue_signature != self._queue_signature:
            self._queue_signature = queue_signature
            next_tracks = state.tracks[max(0, state.index + 1):]
            self._views.get_tab("queue-view").label = _("Queue · {count}", count=len(next_tracks)) if next_tracks else _("Queue")
            self.call_after_refresh(self._place_hint)
            self._queue.show(next_tracks)
        self._lyrics.follow(state.position)
        self._changes.show(self._mode, playback_mode(state), layout=True)  # auto width
        self._quality.show(state.audio, track)
        if state.error and state.error != self._last_error:
            self.message(state.error)
        self._last_error = state.error

    @work(group="media", exclusive=True)
    async def load_media(self, track, generation):
        if generation != self._media_generation:
            return
        # Queue artwork metadata before lyrics, then download independently.
        await asyncio.gather(self._load_cover(track, generation), self._load_lyrics(track, generation))

    async def _load_lyrics(self, track, generation):
        try:
            plain, subtitles, rtl = await self.api("lyrics", track)
            if generation == self._media_generation:
                self._lyrics.load(plain, subtitles, self.playback.snapshot().position)
        except Exception as error:
            if generation == self._media_generation:
                failure("lyrics.unavailable", error)
                self._lyrics.unavailable()

    async def _load_cover(self, track, generation):
        if generation != self._media_generation:
            return
        try:
            art = await self.api("artwork", track)
            if generation == self._media_generation:
                self.covers.show(art)
        except Exception as error:
            if generation == self._media_generation:
                failure("cover.unavailable", error)
