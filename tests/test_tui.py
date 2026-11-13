from concurrent.futures import Future
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from PIL import Image
from textual.geometry import Region
from textual.widgets import DataTable, Input, ListView, ProgressBar, Static

from noneto.audio.controller import PlaybackState
from noneto.ui.themes import THEMES
from noneto.ui.cover import cover_text
from noneto.ui.dialogs import ChoosePlaylist, CreatePlaylist, LibraryScreen
from noneto.ui.lyrics import LyricsPanel, lyric_lines
from noneto.ui.tui import NonetoApp


def track(name="Monster", identifier=1):
    return NS(id=identifier, name=name, artist=NS(name="Skillet"),
              album=NS(name="Awake"), duration=178)


class FakeLibrary:
    def __init__(self):
        self.playlist = NS(id="owned", name="Favorites", creator=NS(id=1))
        self.other = NS(id="other", name="Another library", creator=NS(id=2))
        self.catalog = NS(editable=lambda p: p.creator.id == 1)
        self.calls = []
        self.pending = {}
        self.media_pending = {}
        self.playlist_pages = {0: [self.other]}

    def submit(self, operation, *args):
        self.calls.append((operation, args))
        future = Future()
        if operation == "search" and args[0] in self.pending:
            future.set_running_or_notify_cancel()  # The delayed response models in-flight HTTP.
            self.pending[args[0]] = future
            return future
        if operation == "search_playlists" and ("playlists", args[0]) in self.pending:
            future.set_running_or_notify_cancel()
            self.pending[("playlists", args[0])] = future
            return future
        if operation == "lyrics" and args[0].id in self.media_pending:
            future.set_running_or_notify_cancel()
            self.media_pending[args[0].id] = future
            return future
        result = {"playlists": [self.playlist, self.other], "tracks": [track()],
                  "search": [track("[red]Literal[/red]\x1b", 2)],
                  "search_playlists": self.playlist_pages.get(args[-1], []) if operation == "search_playlists" else [],
                  "lyrics": ("", "[00:01.00]Line one\n[00:03.50]Line two", False),
                  "artwork": Image.new("RGB", (320, 320), (20, 90, 140)),
                  "create": self.playlist, "add": [2]}.get(operation)
        future.set_result(result)
        return future


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_l_opens_library_and_playlist_choice_focuses_song_rows(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app._views.active = "lyrics-view"
            await pilot.press("ctrl+f", "ctrl+l")
            await pilot.pause()
            self.assertIsInstance(app.screen, LibraryScreen)
            self.assertIs(app.focused, app.screen.query_one("#playlists", ListView))
            await pilot.press("down", "enter")
            await pilot.pause()
            self.assertEqual(app.current_playlist.id, "other")
            self.assertEqual(app._views.active, "songs-view")
            self.assertIs(app.focused, app.query_one("#tracks"))
            app.show_tracks([track("First", 1), track("Second", 3)])
            await pilot.press("down")
            self.assertEqual(app.selected_track().id, 3)
            app.query_one("#search", Input).value = "music"
            app.search()
            await pilot.pause()
            self.assertIsNone(app.current_playlist)
            await pilot.press("ctrl+l")
            await pilot.pause()
            self.assertIsInstance(app.screen, LibraryScreen)
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.current_playlist.id, "owned")
            self.assertIs(app.focused, app.query_one("#tracks"))
            self.assertEqual(app.selected_track().id, 1)

    async def test_search_playlists_open_with_keyboard_and_focus_songs(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+f", *"music", "enter")
            await pilot.pause()
            self.assertIn(("search_playlists", ("music", 0)), self.library.calls)
            self.assertEqual(app.playlist_results, [self.library.other])
            app._views.active = "playlists-view"
            await pilot.pause()
            results = app.query_one("#playlist-results", ListView)
            results.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.current_playlist.id, "other")
            self.assertEqual(app._views.active, "songs-view")
            self.assertIs(app.focused, app.query_one("#tracks"))
            await pilot.press("enter")
            self.assertEqual(self.player.send.call_args.args[0], "play")

    async def test_ctrl_c_exits_from_search_and_modal(self):
        for modal in (False, True):
            app = self.make_app()
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                if modal:
                    app.action_new_playlist()
                    await pilot.press("ctrl+f", "ctrl+l")
                    self.assertIsInstance(app.screen, CreatePlaylist)
                else:
                    await pilot.press("ctrl+f")
                await pilot.press("ctrl+c")
                await pilot.pause()
                self.assertFalse(app.is_running)

    async def test_playlist_results_paginate_independently_of_songs(self):
        app = self.make_app()
        self.library.playlist_pages = {0: [self.library.other] * 50, 50: [self.library.playlist]}
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.query_one("#search", Input).value = "music"
            app.search()
            await pilot.pause()
            app._views.active = "playlists-view"
            await pilot.pause()
            self.assertTrue(app.query_one("#more-playlists").display)
            await pilot.click("#more-playlists")
            await pilot.pause()
            self.assertIn(("search_playlists", ("music", 50)), self.library.calls)
            self.assertEqual(len(app.playlist_results), 51)
            self.assertEqual(len(app.tracks), 1)
            self.assertFalse(app.query_one("#more-playlists").display)

    async def test_late_playlist_search_cannot_replace_library_or_new_search(self):
        app = self.make_app()
        self.library.pending = {("playlists", "old"): None, ("playlists", "new"): None}
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            for query in ("old", "new"):
                app.query_one("#search", Input).value = query
                app.search()
                await pilot.pause()
            self.library.pending[("playlists", "new")].set_result([self.library.other])
            await pilot.pause()
            self.library.pending[("playlists", "old")].set_result([self.library.playlist])
            await pilot.pause()
            self.assertEqual(app.playlist_results, [self.library.other])
            app.query_one("#search", Input).value = "old"
            app.search()
            await pilot.pause()
            await pilot.press("ctrl+l")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            self.library.pending[("playlists", "old")].set_result([self.library.other])
            await pilot.pause()
            self.assertEqual(app.current_playlist.id, "owned")
            self.assertEqual(app.playlist_results, [])


    def make_app(self):
        self.library = FakeLibrary()
        self.player = Mock(snapshot=Mock(return_value=PlaybackState()))
        return NonetoApp(self.library, self.player)

    async def test_dark_palette_and_runtime_theme_switch(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.theme, "noneto-dark")
            self.assertEqual(app.screen.styles.background.hex, "#000000")
            self.assertEqual(app.query_one("#player").styles.background.hex, "#1C1C1C")
            self.assertEqual(app.query_one("#search-button").styles.background.hex, "#F5F5F5")

            app.theme = "catppuccin-mocha"
            await pilot.pause()
            self.assertEqual(app.screen.styles.background.hex, "#181825")
            self.assertEqual(app.query_one("#player").styles.background.hex, "#45475A")

    async def test_ctrl_t_cycles_the_offered_themes_and_reports_the_choice(self):
        chosen = []
        player = Mock(snapshot=Mock(return_value=PlaybackState()))
        app = NonetoApp(FakeLibrary(), player, theme=THEMES[-1], on_theme=chosen.append)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.theme, THEMES[-1])
            await pilot.press("ctrl+t")
            await pilot.pause()
            self.assertEqual(app.theme, THEMES[0])  # wraps around to noneto-dark
            await pilot.press("ctrl+t")
            await pilot.pause()
            self.assertEqual(chosen, [THEMES[0], THEMES[1]])
            self.assertIn(THEMES[1], str(app.query_one("#message", Static).content))
        self.assertEqual(NonetoApp(FakeLibrary(), player, theme="no-existe").theme, "noneto-dark")

    async def test_mouse_search_play_create_and_add_only_to_owned_playlist(self):
        app = self.make_app()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 1)
            await pilot.click("#search")
            await pilot.press(*"monster")
            await pilot.click("#search-button")
            await pilot.pause()
            self.assertEqual(app.tracks[0].id, 2)
            cell = app.query_one(DataTable).get_row_at(0)[1]
            self.assertEqual(cell.plain.splitlines()[0], "[red]Literal[/red]")
            self.assertFalse(any("red" in str(span.style) for span in cell.spans))
            await pilot.click("#tracks", offset=(8, 1))
            self.assertEqual(self.player.send.call_args.args[0], "play")
            await pilot.click("#track-enqueue")
            self.player.send.assert_called_with("enqueue", app.tracks[0])
            await pilot.click("#track-add")
            await pilot.pause()
            self.assertIsInstance(app.screen, ChoosePlaylist)
            self.assertEqual(app.screen.playlists, [self.library.playlist])
            await pilot.click("#destinations ListItem")
            await pilot.pause()
            self.assertTrue(any(op == "add" for op, _ in self.library.calls))
            await pilot.click("#library")
            await pilot.pause()
            self.assertIsInstance(app.screen, LibraryScreen)
            await pilot.click("#new-playlist")
            await pilot.pause()
            self.assertIsInstance(app.screen, CreatePlaylist)
            await pilot.press(*"Mi musica")
            await pilot.click("#confirm")
            await pilot.pause()
            self.assertIn(("create", ("Mi musica",)), self.library.calls)
            self.player.snapshot.return_value = PlaybackState(tracks=(app.tracks[0],), index=0,
                                                              status="Playing", duration=178)
            await pilot.click("#pause")
            await pilot.pause()  # Wait for Button.Pressed before checking its callback.
            self.player.send.assert_called_with("pause", None)

    async def test_quality_badge_and_library_picker_open_playlists(self):
        app = NonetoApp(FakeLibrary(), Mock(snapshot=Mock(return_value=PlaybackState())),
                        quality="HI_RES_LOSSLESS")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            badge = app.query_one("#quality", Static)
            # Before anything plays only the request is shown, never as a promise of what arrives.
            self.assertEqual(str(badge.content), "Requested: Hi-Res")
            hires, cd_only = track("Hi-Res", 7), track("CD", 8)
            hires.media_metadata_tags, cd_only.media_metadata_tags = ["LOSSLESS", "HIRES_LOSSLESS"], ["LOSSLESS"]
            hi = ("FLAC 24/96", "FLAC 24-bit / 96 kHz", "HI_RES_LOSSLESS")
            cd = ("FLAC 16/44.1", "FLAC 16-bit / 44.1 kHz", "LOSSLESS")
            for tracks, audio, text, degraded in [((hires,), (), "◆ …", False),
                                                  ((hires,), hi, "◆ FLAC 24/96", False),
                                                  ((cd_only,), cd, "◆ FLAC 16/44.1", False),
                                                  ((hires,), cd, "◇ FLAC 16/44.1 ↓", True)]:
                app.playback.snapshot.return_value = PlaybackState(tracks=tracks, index=0, audio=audio)
                await pilot.pause(0.3)
                self.assertEqual(str(badge.content), text)
                self.assertEqual(badge.has_class("degraded"), degraded)
            self.assertIn("Received: FLAC 16-bit / 44.1 kHz", badge.tooltip)
            self.assertIn("This track is available in: Hi-Res", badge.tooltip)
            self.assertIn("less than this track offers", badge.tooltip)
            app.playback.snapshot.return_value = PlaybackState()
            self.assertEqual(app.query_one("#library").label.plain, "Favorites ▾")
            await pilot.click("#library")
            await pilot.pause()
            self.assertIsInstance(app.screen, LibraryScreen)
            await pilot.click("#playlists ListItem", offset=(2, 0))
            await pilot.pause()
            await pilot.pause()
            self.assertNotIsInstance(app.screen, LibraryScreen)
            self.assertEqual(app.current_playlist.id, "owned")

    async def test_volume_bar_click_sets_level(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.click("#volume-bar", offset=(9, 0))
            self.player.send.assert_called_with("volume", 40)
            await pilot.click("#louder")
            self.player.send.assert_called_with("volume", 5)

    async def test_late_search_cannot_replace_newer_results(self):
        app = self.make_app()
        self.library.pending = {"old": None, "new": None}
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.query_one("#search", Input).value = "old"
            app.search()
            await pilot.pause()
            app.query_one("#search", Input).value = "new"
            app.search()
            await pilot.pause()
            self.library.pending["new"].set_result([track("New result")])
            await pilot.pause()
            self.library.pending["old"].set_result([track("Old result")])
            await pilot.pause()
            self.assertEqual(app.tracks[0].name, "New result")

    async def test_typing_spaces_does_not_pause_and_small_terminal_is_usable(self):
        app = self.make_app()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press("ctrl+f", *"hola world")
            self.assertEqual(app.query_one("#search", Input).value, "hola world")
            self.player.send.assert_not_called()
            self.assertGreater(app.query_one("#tracks").size.height, 5)
            self.assertEqual(app.query_one("#player").outer_size.height, 5)
            self.assertFalse(app.query_one("#track-add").disabled)
            self.assertTrue(await pilot.click("#track-enqueue"))
            self.player.send.assert_called_with("enqueue", app.tracks[0])
            self.assertTrue(await pilot.click("#track-add"))
            await pilot.pause()
            self.assertIsInstance(app.screen, ChoosePlaylist)
            await pilot.press("escape")
            await pilot.pause()
            self.assertTrue(await pilot.click("#tracks", offset=(2, 1)))
            self.assertIn("Selected", str(app.query_one("#action-target", Static).content))
            self.assertTrue(await pilot.click("#pause"))
            self.assertEqual(self.player.send.call_args.args[0], "play")
            self.assertTrue(await pilot.click("#next"))
            await pilot.pause()  # Drain Button.Pressed before checking the controller callback.
            self.player.send.assert_called_with("next", None)
            self.assertTrue(await pilot.click("#repeat"))
            await pilot.pause()
            self.player.send.assert_called_with("repeat", None)

    async def test_song_without_lyrics_shows_the_cover_large_and_centered(self):
        app = self.make_app()
        song = track()
        self.library.media_pending = {song.id: None}
        self.player.snapshot.return_value = PlaybackState(tracks=(song,), index=0, status="Playing")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            panel, cover = app.query_one(LyricsPanel), app.query_one("#cover", Static)
            self.assertFalse(panel.centered)  # still loading: the usual layout
            self.assertEqual(cover.size, (14, 6))
            self.library.media_pending[song.id].set_result(("", "", False))
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(panel.centered)
            self.assertFalse(app.query_one("#lyrics-body").display)
            rows = cover.size.height
            self.assertGreater(rows, 6)
            self.assertEqual(cover.size.width, rows * 2)  # square with 1:2 cells
            self.assertEqual(cover.content.plain.splitlines(), ["▀" * rows * 2] * rows)
            # Centered in the pane, with a free row above and below.
            area = panel.content_region
            self.assertEqual(cover.region.y - area.y, area.bottom - cover.region.bottom)
            self.assertLessEqual(abs((cover.region.x - area.x) - (area.right - cover.region.right)), 1)
            # The next song with lyrics goes back to cover + lyrics.
            second = track("Second", 2)
            self.player.snapshot.return_value = PlaybackState(tracks=(second,), index=0, status="Playing")
            app.update_player()
            await pilot.pause()
            await pilot.pause()
            self.assertFalse(panel.centered)
            self.assertEqual(cover.size, (14, 6))
            self.assertEqual(len(cover.content.plain.splitlines()), 6)

    async def test_ctrl_o_or_clicking_the_cover_switches_and_saves_the_lyrics_layout(self):
        chosen = []
        self.library = FakeLibrary()
        self.player = Mock(snapshot=Mock(return_value=PlaybackState(tracks=(track(),), index=0, status="Playing")))
        app = NonetoApp(self.library, self.player, lyrics_layout="cover", on_lyrics_layout=chosen.append)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.update_player()
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            await pilot.pause()
            panel = app.query_one(LyricsPanel)
            self.assertTrue(panel.centered)  # saved choice, even though this song has lyrics
            self.assertFalse(app.query_one("#lyrics-body").display)
            await pilot.press("ctrl+o")
            await pilot.pause()
            self.assertFalse(panel.centered)
            self.assertTrue(app.query_one("#lyrics-body").display)
            self.assertIn("Line one", str(app.query_one("#lyrics-text", Static).content))
            await pilot.click("#cover")
            await pilot.pause()
            self.assertTrue(panel.centered)
            self.assertEqual(chosen, ["lyrics", "cover"])
            self.assertIn("Ctrl+O", str(app.query_one("#message", Static).content))

    async def test_long_titles_are_cut_instead_of_scrolling_the_table_sideways(self):
        app = self.make_app()
        long_song = track("Nine Thou (Grant Mohrman Outta Control Remix) " * 4, 7)
        async with app.run_test(size=(90, 32)) as pilot:
            await pilot.pause()
            app.show_tracks([long_song] + [track(f"Song {i}", i + 10) for i in range(120)])
            await pilot.pause()
            table = app.query_one(DataTable)
            self.assertLessEqual(table.virtual_size.width, table.scrollable_content_region.width)
            self.assertFalse(table.show_horizontal_scrollbar)
            self.assertIn("…", "".join(segment.text for segment in table.render_line(1)))
            header = "".join(segment.text for segment in table.render_line(0))
            self.assertIn("Duration", header)
            await pilot.resize_terminal(140, 32)
            await pilot.pause()
            self.assertLessEqual(table.virtual_size.width, table.scrollable_content_region.width)
            self.assertGreater(table.virtual_size.width, 120)  # widens with the terminal

    async def test_medium_density_rows_context_and_player_cover(self):
        app = self.make_app()
        self.player.snapshot.return_value = PlaybackState(tracks=(track(),), index=0, status="Playing")
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            app.update_player()
            await pilot.pause()
            self.assertEqual(app.query_one("#top").outer_size.height, 5)
            self.assertEqual(app.query_one("#library").region.y, 2)
            self.assertEqual(app.query_one("#search").outer_size.height, 3)
            table = app.query_one(DataTable)
            self.assertEqual(table.ordered_rows[0].height, 2)
            self.assertEqual(table.get_row_at(0)[1].plain, "Monster\nSkillet · Awake")
            hint = app.query_one("#hint")
            self.assertEqual(str(hint.content), "Favorites · 1 songs")
            tabs_end = max(tab.region.right for tab in app.query("#views Tab"))
            self.assertEqual(hint.region.y, app.query_one("#views").region.y)
            self.assertGreater(hint.region.x, tabs_end)
            self.assertEqual(hint.region.right, app.query_one("#views").region.right)
            self.assertEqual(str(app.query_one("#now-detail", Static).content), "Skillet · Awake")
            self.assertEqual(len(app.query_one("#mini-cover", Static).content.plain.splitlines()), 3)
            await pilot.click("#--content-tab-queue-view")
            await pilot.pause()
            self.assertEqual(app.query_one("#views").active, "queue-view")

    async def test_graphics_terminal_draws_full_resolution_cover(self):
        from textual_image.widget import SixelImage
        self.library = FakeLibrary()
        self.player = Mock(snapshot=Mock(return_value=PlaybackState(tracks=(track(),), index=0, status="Playing")))
        app = NonetoApp(self.library, self.player, artwork=SixelImage)
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            app.update_player()
            await pilot.pause()
            self.assertIn(("artwork", (track(),)), self.library.calls)
            cover = app.query_one("#cover")
            self.assertEqual(cover.image.size, (320, 320))
            self.assertIs(app.query_one("#mini-cover").image, cover.image)
            self.assertEqual(app.query_one("#mini-cover").size, (6, 3))
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            self.assertEqual(cover.size, (12, 6))
            drawn = cover.children[0].render_lines(Region(0, 0, 12, 6))
            self.assertIn("\x1bP", "".join(segment.text for segment in drawn[-1]))
            await pilot.press("ctrl+o")  # cover only: the image widget grows, centered
            await pilot.pause()
            await pilot.pause()
            self.assertGreater(cover.size.height, 6)
            self.assertEqual(cover.size.width, cover.size.height * 2)
            self.player.snapshot.return_value = PlaybackState()
            app.update_player()
            await pilot.pause()
            self.assertIsNone(cover.image)

    async def test_backend_error_message_does_not_expose_secret(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.report(RuntimeError("TOKEN_SECRET"))
            self.assertNotIn("TOKEN_SECRET", str(app.query_one("#message", Static).content))

    async def test_sketch_layout_and_synced_lyrics_follow_playback(self):
        app = self.make_app()
        song = track()
        self.player.snapshot.return_value = PlaybackState(tracks=(song,), index=0,
                                                          status="Playing", position=2,
                                                          duration=178, volume=60)
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            app.update_player()
            await pilot.pause()
            self.assertEqual(app.query_one(DataTable).get_row_at(0)[0].plain, "▷")
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            self.assertEqual(app.query_one("#views").active, "lyrics-view")
            self.assertGreater(app.query_one("#lyrics-scroll").size.height, 0)
            self.assertEqual(app.query_one("#progress", ProgressBar).progress, 2)
            self.assertEqual(app.query_one(LyricsPanel).active_line, 0)
            self.assertIn("Line one", str(app.query_one("#lyrics-text", Static).content))
            self.assertTrue(app.query_one("#cover", Static).content.spans)
            self.player.snapshot.return_value = PlaybackState(tracks=(song,), index=0,
                                                              status="Playing", position=4,
                                                              duration=178)
            app.update_player()
            self.assertEqual(app.query_one(LyricsPanel).active_line, 1)
            self.assertEqual(app.query_one("#up-next", Static).content, "No songs in the queue.")

    async def test_queue_only_shows_upcoming_tracks_and_progress_click_seeks(self):
        app = self.make_app()
        songs = (track("Ahora", 1), track("Despues", 2), track("Al final", 3))
        self.player.snapshot.return_value = PlaybackState(tracks=songs, index=0,
                                                          status="Playing", position=20,
                                                          duration=200)
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            app.update_player()
            upcoming = app.query_one("#up-next", Static).content.plain
            self.assertIn("Despues", upcoming)
            self.assertIn("Al final", upcoming)
            self.assertNotIn("Ahora", upcoming)
            self.assertFalse(list(app.query("#rail Button")))
            self.assertEqual(app.query_one("#views").get_tab("queue-view").label.plain, "Queue · 2")
            bar = app.query_one("#progress #bar")
            self.assertEqual(bar.size.width, app.query_one("#progress").size.width)
            wide_width = bar.size.width
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertEqual(bar.size.width, app.query_one("#progress").size.width)
            self.assertLess(bar.size.width, wide_width)
            await pilot.resize_terminal(120, 38)
            await pilot.pause()
            self.assertEqual(bar.size.width, wide_width)
            await pilot.click(bar, offset=(bar.size.width // 2, 0))
            self.assertEqual(self.player.send.call_args.args[0], "seek_absolute")
            self.assertAlmostEqual(self.player.send.call_args.args[1], 100, delta=5)
            self.player.send.reset_mock()
            self.player.snapshot.return_value = PlaybackState()
            await pilot.click(bar, offset=(bar.size.width // 2, 0))
            self.player.send.assert_not_called()

    async def test_shuffle_repeat_and_text_transport_controls(self):
        app = self.make_app()
        song = track()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertEqual(app.query_one("#previous").label.plain, "◁◁")
            self.assertEqual(app.query_one("#next").label.plain, "▷▷")
            self.assertEqual(app.query_one("#pause").label.plain, "▷")
            await pilot.click("#shuffle")
            self.player.send.assert_called_with("shuffle", None)
            await pilot.click("#repeat")
            self.player.send.assert_called_with("repeat", None)
            self.player.snapshot.return_value = PlaybackState(tracks=(song,), index=0,
                                                              status="Playing", shuffle=True,
                                                              repeat="all")
            app.update_player()
            self.assertEqual(app.query_one("#pause").label.plain, "❚❚")
            self.assertEqual(app.query_one("#repeat").label.plain, "↻")
            self.assertTrue(app.query_one("#repeat").has_class("active"))
            self.assertTrue(app.query_one("#shuffle").has_class("active"))
            self.assertIn("Repeat: queue", str(app.query_one("#mode", Static).content))

    async def test_old_song_lyrics_cannot_replace_new_song(self):
        app = self.make_app()
        first, second = track("First", 21), track("Second", 22)
        self.library.media_pending = {21: None, 22: None}
        self.player.snapshot.return_value = PlaybackState(tracks=(first, second), index=0,
                                                          status="Playing")
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            app.update_player()
            await pilot.pause()
            self.player.snapshot.return_value = PlaybackState(tracks=(first, second), index=1,
                                                              status="Playing")
            app.update_player()
            await pilot.pause()
            self.library.media_pending[22].set_result(("New lyrics", "", False))
            await pilot.pause()
            self.library.media_pending[21].set_result(("Old lyrics", "", False))
            await pilot.pause()
            content = str(app.query_one("#lyrics-text", Static).content)
            self.assertIn("New lyrics", content)
            self.assertNotIn("Old lyrics", content)

    def test_shortcuts_avoid_keys_windows_terminal_keeps(self):
        # Windows Terminal binds Ctrl+V to paste by default: the app never receives that key.
        keys = {key.strip() for binding in NonetoApp.BINDINGS for key in binding.key.split(",")}
        self.assertNotIn("ctrl+v", keys)
        self.assertIn("ctrl+g", keys)

    def test_lrc_fallback_and_literal_artwork(self):
        lines, timed = lyric_lines("Solo texto", "")
        self.assertFalse(timed)
        self.assertEqual(lines, [(None, "Solo texto")])
        lines, timed = lyric_lines("", "[00:03.5]Despues\n[00:01.00]Antes")
        self.assertTrue(timed)
        self.assertEqual(lines[0], (1, "Antes"))
        self.assertEqual(lines[1], (3.5, "Despues"))
        picture = Image.new("RGB", (640, 640), (20, 90, 140))
        art = cover_text(picture)
        self.assertEqual(len(art.plain.splitlines()), 6)
        self.assertEqual(len(art.spans), 72)
        self.assertIn("rgb(20,90,140)", str(art.spans[0].style))
        mini = cover_text(picture, 6, 3)
        self.assertEqual(mini.plain.splitlines(), ["▀" * 6] * 3)
        self.assertEqual(cover_text(picture, 40, 20).plain.splitlines(), ["▀" * 40] * 20)
        self.assertEqual(cover_text(None, 4, 2).plain.splitlines(), ["    "] * 2)

    async def test_unexpected_fatal_error_does_not_render_traceback_locals(self):
        app = self.make_app()
        with self.assertRaisesRegex(RuntimeError, "TOKEN_SECRET"):
            async with app.run_test(size=(100, 32)) as pilot:
                await pilot.pause()
                app._handle_exception(RuntimeError("TOKEN_SECRET"))
        self.assertEqual(app.return_code, 1)
        self.assertNotIn("TOKEN_SECRET", str(app._exit_renderables))
