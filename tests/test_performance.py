"""Behavioral regressions for scheduling, persistence and incremental drawing."""
import asyncio
from concurrent.futures import Future
from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from textual.widgets import DataTable, Static

from noneto.audio.controller import PlaybackController, PlaybackState
from noneto.auth.storage import SessionPersistence
from noneto.errors import AppError
from noneto.services.covers import CoverDownloader
from noneto.services.executor import PriorityExecutor
from noneto.services.library import Library
from noneto.services.tidal import Catalog
from noneto.state.queue import QueueStore
from noneto.state.writer import QueueWriter
from noneto.ui.tui import NonetoApp
from noneto.ui.visualizer import Visualizer
from tests.test_library_controller import StubPlayer, wait_for
from tests.test_tui import FakeLibrary, track


class SchedulingTests(unittest.TestCase):
    def test_prefetched_event_batch_is_drained_without_another_poll_wait(self):
        class NotifyingPlayer(StubPlayer):
            def set_wakeup(self, wakeup):
                self.wakeup = wakeup

            def command(self, *args, **kwargs):
                if args == ("get_property", "audio-codec-name"):
                    return "flac"
                if args == ("get_property", "audio-params"):
                    return {"samplerate": 44100, "format": "s16"}
                return 10 if args == ("get_property", "duration") else 1

            def stage(self, target):
                # All notifications arrive together before the controller drains EOF.
                for event in ({"event": "end-file", "reason": "eof"}, {"event": "start-file"},
                              {"event": "file-loaded"}):
                    self.events.put(event)
                self.wakeup()

        def resolve(song):
            ready = Future()
            ready.set_result(song.id)
            return ready

        player = NotifyingPlayer()
        controller = PlaybackController(resolve, lambda: player)
        unnecessary_waits = []
        original_wait = controller._wake.wait

        def wait(timeout):
            if not controller._wake.is_set() and not player.events.empty():
                unnecessary_waits.append(player.events.qsize())
            return original_wait(timeout)

        try:
            with patch.object(controller._wake, "wait", side_effect=wait):
                controller.send("play", ([track("First", 1), track("Next", 2)], 0))
                wait_for(lambda: controller.snapshot().index == 1 and controller.snapshot().status == "Playing")
            self.assertEqual(unnecessary_waits, [])
        finally:
            controller.close()

    def test_stream_runs_between_pages_with_duplicates_and_one_sdk_owner(self):
        entered, release = threading.Event(), threading.Event()
        calls, owners = [], set()
        entries = list(range(150)) + list(range(100))

        def fetch(limit, offset):
            owners.add(threading.get_ident())
            calls.append(offset)
            if offset == 0:
                entered.set()
                self.assertTrue(release.wait(3))
            return entries[offset:offset + limit]

        library = Library(Catalog(NS()))
        try:
            playlist = library.submit("tracks", NS(tracks=fetch))
            self.assertTrue(entered.wait(3))

            def resolve(*args):
                calls.append("stream")
                owners.add(threading.get_ident())
                return "target"

            stream = library.resolve(resolve, None, None)
            release.set()
            self.assertEqual(stream.result(3), "target")
            self.assertEqual(playlist.result(3), entries)
            self.assertEqual(calls[:3], [0, "stream", 100])
            self.assertEqual(len(owners), 1)
        finally:
            release.set()
            library.close()

    def test_canceling_a_playlist_prevents_its_remaining_pages(self):
        entered, release = threading.Event(), threading.Event()
        offsets = []

        def fetch(limit, offset):
            offsets.append(offset)
            entered.set()
            release.wait(3)
            return list(range(limit))

        library = Library(Catalog(NS()))
        try:
            pending = library.submit("tracks", NS(tracks=fetch))
            self.assertTrue(entered.wait(3))
            self.assertTrue(pending.cancel())
            release.set()
            library.resolve(lambda *_: None, None, None).result(3)
            self.assertEqual(offsets, [0])
        finally:
            release.set()
            library.close()

    def test_bounded_scheduler_cancels_obsolete_reads_without_canceling_a_write(self):
        entered, release = threading.Event(), threading.Event()
        worker = PriorityExecutor("test-priority", capacity=2)
        called = []
        try:
            first = worker.submit(lambda: (entered.set(), release.wait(3)))
            self.assertTrue(entered.wait(3))
            obsolete = worker.submit(lambda: called.append("obsolete"))
            write = worker.submit(lambda: called.append("write"), priority=10)
            with self.assertRaises(AppError):
                worker.submit(lambda: None)
            self.assertTrue(obsolete.cancel())
            stream = worker.submit(lambda: called.append("stream"), priority=0)
            release.set()
            first.result(3)
            stream.result(3)
            write.result(3)
            self.assertEqual(called, ["stream", "write"])
        finally:
            release.set()
            worker.shutdown()

    def test_artwork_shares_media_download_and_decodes_off_ui_thread(self):
        data = io.BytesIO()
        Image.new("RGB", (320, 320), "navy").save(data, "JPEG")
        library = Library(Catalog(NS()))
        song = NS(album=NS(cover="x", image=lambda size: f"https://resources.tidal.com/{size}.jpg"))
        try:
            with patch.object(library._covers, "fetch", return_value=data.getvalue()) as fetch:
                art = library.submit("artwork", song).result(3)
                self.assertEqual((art.mode, art.size), ("RGB", (320, 320)))
                self.assertEqual(library.submit("media_cover", song).result(3), data.getvalue())
                self.assertEqual({call.args[0] for call in fetch.call_args_list},
                                 {("https://resources.tidal.com/640.jpg", 640)})
        finally:
            library.close()

    def test_slow_cover_does_not_block_streams_or_lyrics(self):
        entered, release = threading.Event(), threading.Event()
        library = Library(Catalog(NS()))
        song = NS(album=NS(cover="x", image=lambda size: "https://resources.tidal.com/x.jpg"),
                  lyrics=lambda: NS(text="text", subtitles="", right_to_left=False))

        def transfer(request):
            entered.set()
            release.wait(3)
            return None

        try:
            with patch.object(library._covers, "fetch", side_effect=transfer):
                cover = library.submit("artwork", song)
                self.assertTrue(entered.wait(3))
                self.assertEqual(library.resolve(lambda *_: "target", song, None).result(1), "target")
                self.assertEqual(library.submit("lyrics", song).result(1), ("text", "", False))
                release.set()
                self.assertIsNone(cover.result(3))
        finally:
            release.set()
            library.close()


class PersistenceTests(unittest.TestCase):
    def test_slow_disk_does_not_block_controls_and_close_flushes_latest_state(self):
        entered, release = threading.Event(), threading.Event()
        states = []

        def save(state, tracks, order):
            entered.set()
            release.wait(3)
            states.append(state)

        store = NS(load=lambda: None, save=save)
        resolved = Future()
        resolved.set_result("local-target")
        controller = PlaybackController(lambda _: resolved, StubPlayer, queue_store=store)
        try:
            controller.send("play", ([track()], 0))
            self.assertTrue(entered.wait(3))
            controller.send("volume", 10)
            wait_for(lambda: controller.snapshot().volume == 70, timeout=1)
            controller.send("repeat")
            wait_for(lambda: controller.snapshot().repeat == "all", timeout=1)
        finally:
            release.set()
            controller.close()
        self.assertEqual((states[-1].volume, states[-1].repeat), (70, "all"))

    def test_writer_coalesces_pending_snapshots_and_survives_failed_save(self):
        entered, release = threading.Event(), threading.Event()
        written, errors = [], []

        def save(state, *args):
            written.append(state.position)
            if len(written) == 1:
                entered.set()
                release.wait(3)
                raise OSError("synthetic disk failure")

        writer = QueueWriter(NS(save=save), errors.append)
        writer.submit(PlaybackState(position=1), (), ())
        self.assertTrue(entered.wait(3))
        for position in range(2, 21):
            writer.submit(PlaybackState(position=position), (), ())
        release.set()
        writer.close()
        self.assertEqual(written, [1, 20])
        self.assertEqual(len(errors), 1)

    def test_cached_queue_roundtrips_latest_position_order_and_changed_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore("test", Path(directory) / "queue.json")
            songs = (track("First", 1), track("Duplicate", 1), track("Third", 3))
            store.save(PlaybackState(index=1), songs, (2, 0, 1))
            store.save(PlaybackState(index=2, position=18, volume=42), songs, (0, 2, 1))
            saved = store.load()
            self.assertEqual((saved["position"], saved["volume"], saved["order"]), (18, 42, [0, 2, 1]))
            self.assertEqual([item.id for item in saved["tracks"]], [1, 1, 3])
            store.save(PlaybackState(index=0), (track("Renamed", 1),), (0,))
            self.assertEqual(store.load()["tracks"][0].name, "Renamed")

    def test_credentials_write_only_on_changes_and_retry_failure(self):
        session = NS(token_type="Bearer", access_token="synthetic-a", refresh_token="synthetic-r",
                     is_pkce=True, expiry_time=None)
        persist = SessionPersistence(session)
        with patch("noneto.auth.storage.save_session") as save:
            persist()
            persist()
            save.assert_not_called()
            for name, value in (("access_token", "synthetic-b"), ("refresh_token", "synthetic-s"),
                                ("token_type", "Other"), ("is_pkce", False),
                                ("expiry_time", datetime(2030, 1, 1, tzinfo=timezone.utc))):
                setattr(session, name, value)
                persist()
                persist()
            self.assertEqual(save.call_count, 5)
            session.access_token = "synthetic-c"
            save.side_effect = OSError("synthetic disk failure")
            with self.assertRaises(OSError):
                persist()
            save.side_effect = None
            persist()
            persist()
            self.assertEqual(save.call_count, 7)


class CoverTests(unittest.TestCase):
    @staticmethod
    def jpeg():
        data = io.BytesIO()
        Image.new("RGB", (80, 80), "navy").save(data, "JPEG")
        return data.getvalue()

    def test_validated_album_cache_reuses_connection_and_evicts(self):
        jpeg = self.jpeg()
        response = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.side_effect = lambda _: iter([jpeg])
        with patch("noneto.services.covers.requests.Session") as session:
            connection = session.return_value
            connection.get.return_value = response
            covers = CoverDownloader(max_entries=1)
            try:
                first = ("https://resources.tidal.com/album-a.jpg", 320)
                second = ("https://resources.tidal.com/album-b.jpg", 320)
                self.assertEqual(covers.fetch(first), jpeg)
                self.assertEqual(covers.fetch(first), jpeg)
                self.assertEqual(connection.get.call_count, 1)
                covers.fetch(second)
                covers.fetch(first)
                self.assertEqual(connection.get.call_count, 3)
                session.assert_called_once()
                self.assertFalse(connection.trust_env)
                for call in connection.get.call_args_list:
                    self.assertTrue(call.kwargs["verify"])
                    self.assertFalse(call.kwargs["allow_redirects"])
                self.assertLessEqual(covers._bytes, len(jpeg))
            finally:
                covers.close()
            connection.close.assert_called_once()

    def test_idle_cover_connection_is_replaced_before_tidal_drops_it(self):
        jpeg = self.jpeg()
        response = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.side_effect = lambda _: iter([jpeg])
        with patch("noneto.services.covers.requests.Session") as session,                 patch("noneto.services.covers.time.monotonic", side_effect=[100.0, 129.0, 130.0, 200.0, 201.0]):
            session.return_value.get.return_value = response
            covers = CoverDownloader()
            try:
                covers.fetch(("https://resources.tidal.com/a.jpg", 320))
                covers.fetch(("https://resources.tidal.com/b.jpg", 320))  # 29 s idle: reused
                self.assertEqual(session.call_count, 1)
                covers.fetch(("https://resources.tidal.com/c.jpg", 320))  # 70 s idle: replaced
                self.assertEqual(session.call_count, 2)
                session.return_value.close.assert_called_once()
            finally:
                covers.close()

    def test_untrusted_cover_rejected_before_connection_and_redirect_not_cached(self):
        covers = CoverDownloader()
        with patch("noneto.services.covers.requests.Session") as session:
            with self.assertRaises(AppError):
                covers.fetch(("https://evil.test/image.jpg", 320))
            session.assert_not_called()
            response = Mock(status_code=302, headers={"Content-Type": "image/jpeg"})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            session.return_value.get.return_value = response
            with self.assertRaises(AppError):
                covers.fetch(("https://resources.tidal.com/image.jpg", 320))
            self.assertEqual(len(covers._cache), 0)
        covers.close()

    def test_invalid_format_dimensions_and_oversize_never_enter_cache(self):
        png, large = io.BytesIO(), io.BytesIO()
        Image.new("RGB", (80, 80)).save(png, "PNG")
        Image.new("RGB", (641, 320)).save(large, "JPEG")
        for payload in (png.getvalue(), large.getvalue(), b"x" * (1024 * 1024 + 1)):
            with self.subTest(size=len(payload)), patch("noneto.services.covers.requests.Session") as session:
                response = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.iter_content.return_value = iter([payload])
                session.return_value.get.return_value = response
                covers = CoverDownloader()
                try:
                    with self.assertRaises(AppError):
                        covers.fetch(("https://resources.tidal.com/image.jpg", 320))
                    self.assertEqual(covers._bytes, 0)
                    self.assertEqual(len(covers._cache), 0)
                finally:
                    covers.close()

    def test_cache_byte_budget_is_enforced_without_unbounded_album_entries(self):
        jpeg = self.jpeg()
        with patch("noneto.services.covers.requests.Session") as session:
            response = Mock(status_code=200, headers={"Content-Type": "image/jpeg"})
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.iter_content.side_effect = lambda _: iter([jpeg])
            session.return_value.get.return_value = response
            covers = CoverDownloader(max_bytes=len(jpeg) * 2 - 1)
            try:
                for index in range(20):
                    covers.fetch((f"https://resources.tidal.com/{index}.jpg", 320))
                self.assertEqual(len(covers._cache), 1)
                self.assertEqual(covers._bytes, len(jpeg))
            finally:
                covers.close()


class UiPerformanceTests(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        return NonetoApp(FakeLibrary(), Mock(snapshot=Mock(return_value=PlaybackState()),
                                            bands=Mock(return_value=None)))

    async def test_large_table_yields_preserves_full_playback_queue_and_discards_old_batches(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            songs = [track(f"Song {i}", i + 1) for i in range(1000)]
            app.show_tracks(songs)
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 100)
            self.assertEqual(app.tracks, songs)
            app.play_selected()
            self.assertEqual(len(app.playback.send.call_args.args[1][0]), 1000)
            app.begin_view("Replacement")
            app.show_tracks([track("Replacement", 2000)])
            await asyncio.sleep(.1)
            self.assertEqual(table.row_count, 1)
            self.assertIn("Replacement", table.get_row_at(0)[1].plain)

    async def test_table_finishes_in_order_including_appended_duplicates(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            songs = [track(f"Song {i}", i + 1) for i in range(350)]
            app.show_tracks(songs)
            app.show_tracks([songs[0]], append=True)
            for _ in range(100):
                if app.query_one(DataTable).row_count == 351:
                    break
                await asyncio.sleep(.01)
            table = app.query_one(DataTable)
            self.assertEqual(table.row_count, 351)
            self.assertEqual(table.get_row_at(350)[1].plain, table.get_row_at(0)[1].plain)

    async def test_view_change_cancels_queued_reads_but_not_writes(self):
        app = self.make_app()
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            read, write = Future(), Future()
            with patch.object(app.library, "submit", side_effect=[read, write]):
                read_task = asyncio.create_task(app.api("search", "old"))
                write_task = asyncio.create_task(app.api("create", "Title"))
                await asyncio.sleep(0)
                app.begin_view("New")
                self.assertTrue(read.cancelled())
                self.assertFalse(write.cancelled())
                write.set_result("created")
                self.assertEqual(await write_task, "created")
                with self.assertRaises(asyncio.CancelledError):
                    await read_task

    async def test_cover_is_available_while_lyrics_are_still_pending(self):
        library = FakeLibrary()
        library.media_pending = {1: None}
        player = Mock(snapshot=Mock(return_value=PlaybackState(tracks=(track(),), index=0, status="Playing")),
                      bands=Mock(return_value=None))
        app = NonetoApp(library, player)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            self.assertFalse(library.media_pending[1].done())
            self.assertTrue(app.query_one("#cover", Static).content.spans)

    async def test_visualizer_skips_identical_frames_and_repaints_theme_mode_resize(self):
        app = self.make_app()
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            visualizer = app.query_one(Visualizer)
            visualizer.stop()
            visualizer.tick()
            with patch.object(visualizer, "refresh", wraps=visualizer.refresh) as refresh:
                visualizer.tick()
                visualizer.tick()
                refresh.assert_not_called()
                visualizer.set_mode("mirror")
                self.assertTrue(refresh.called)
            app.theme = "nord"
            await pilot.pause()
            self.assertEqual(visualizer.bar_styles(6)[0].color.triplet.hex, "#5e81ac")
            await pilot.resize_terminal(100, 36)
            await pilot.pause()
            visualizer.tick()
            self.assertTrue(all(len(line) == visualizer.content_size.width for line in visualizer._lines))
