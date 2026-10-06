from concurrent.futures import Future
import queue
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import wave
from pathlib import Path

from noneto.audio.controller import PlaybackController
from noneto.audio.player import Player
from noneto.errors import AppError
from noneto.services.library import Library
from noneto.services.tidal import Catalog


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError("Timed out waiting for playback state")


class StubPlayer:
    def __init__(self):
        self.events = queue.Queue()
        self.failed = threading.Event()
        self.process = NS(poll=lambda: None)
        self.targets = []
        self.commands = []
        self.closed = False

    def command(self, *args, **kwargs):
        self.commands.append(args)
        return 1

    def play(self, target):
        self.targets.append(target)
        self.events.put({"event": "start-file"})
        self.events.put({"event": "file-loaded"})

    def close(self):
        self.closed = True


class ControllerTests(unittest.TestCase):
    def test_stop_discards_late_stream_and_next_can_play(self):
        pending = Future()
        pending.set_running_or_notify_cancel()
        ready = Future()
        ready.set_result("new-target")
        resolver = Mock(side_effect=[pending, ready])
        player = StubPlayer()
        controller = PlaybackController(resolver, lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1), NS(id=2)], 0))
        wait_for(lambda: resolver.call_count == 1)
        controller.send("stop")
        wait_for(lambda: controller.snapshot().status == "Detenido")
        pending.set_result("old-target")
        controller.send("play", ([NS(id=2)], 0))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        self.assertEqual(player.targets, ["new-target"])
        controller.send("pause")
        wait_for(lambda: controller.snapshot().paused)
        controller.send("volume", 100)
        wait_for(lambda: controller.snapshot().volume == 100)

    def test_stale_eof_before_start_does_not_skip_the_new_track(self):
        player = StubPlayer()
        player.play = lambda target: (player.targets.append(target),
                                      player.events.put({"event": "end-file", "reason": "eof"}))
        ready = Future()
        ready.set_result("target")
        controller = PlaybackController(Mock(return_value=ready), lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1), NS(id=2)], 0))
        wait_for(lambda: player.targets)
        time.sleep(0.2)
        self.assertEqual(controller.snapshot().index, 0)
        self.assertEqual(player.targets, ["target"])

    def test_lost_ipc_reports_error_and_closes_player(self):
        player = StubPlayer()
        ready = Future()
        ready.set_result("target")
        controller = PlaybackController(Mock(return_value=ready), lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1)], 0))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        player.failed.set()
        wait_for(lambda: controller.snapshot().status == "Error")
        self.assertIn("mpv", controller.snapshot().error)
        self.assertTrue(player.closed)

    def test_resolution_error_is_safe_and_does_not_skip_entire_queue(self):
        pending = Future()
        pending.set_exception(RuntimeError("SIGNED_URL_SECRET"))
        resolver = Mock(return_value=pending)
        controller = PlaybackController(resolver, StubPlayer)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1), NS(id=2)], 0))
        wait_for(lambda: controller.snapshot().status == "Error")
        self.assertEqual(controller.snapshot().error, "RuntimeError")
        self.assertEqual(resolver.call_count, 1)

    def test_absolute_seek_uses_mpv_position_and_clamps_to_duration(self):
        ready = Future()
        ready.set_result("local-target")
        player = StubPlayer()
        controller = PlaybackController(lambda _: ready, lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1)], 0))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        controller._set(duration=20)
        controller.send("seek_absolute", 7.5)
        wait_for(lambda: ("seek", 7.5, "absolute") in player.commands)
        controller.send("seek_absolute", 50)
        wait_for(lambda: ("seek", 20, "absolute") in player.commands)

    def test_shuffle_preserves_current_track_and_restores_remaining_order(self):
        tracks = tuple(NS(id=identifier) for identifier in range(6))
        def resolve(_):
            future = Future()
            future.set_result("local-target")
            return future
        controller = PlaybackController(resolve, StubPlayer)
        self.addCleanup(controller.close)
        controller._random.seed(0)
        controller.send("play", (tracks, 1))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        controller.send("shuffle")
        wait_for(lambda: controller.snapshot().shuffle)
        shuffled = controller.snapshot()
        self.assertEqual(shuffled.index, 1)
        self.assertIs(shuffled.tracks[1], tracks[1])
        self.assertCountEqual(shuffled.tracks[2:], tracks[2:])
        self.assertNotEqual(shuffled.tracks[2:], tracks[2:])
        controller.send("shuffle")
        wait_for(lambda: not controller.snapshot().shuffle)
        self.assertEqual(controller.snapshot().tracks, tracks)

    def test_repeat_one_replays_on_eof_and_repeat_all_wraps_queue(self):
        tracks = (NS(id=1), NS(id=2))
        def resolve(_):
            future = Future()
            future.set_result("local-target")
            return future
        player = StubPlayer()
        controller = PlaybackController(resolve, lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", (tracks, 1))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        controller.send("repeat")
        wait_for(lambda: controller.snapshot().repeat == "all")
        player.events.put({"event": "end-file", "reason": "eof"})
        wait_for(lambda: controller.snapshot().index == 0)
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        controller.send("repeat")
        wait_for(lambda: controller.snapshot().repeat == "one")
        played = len(player.targets)
        player.events.put({"event": "end-file", "reason": "eof"})
        wait_for(lambda: len(player.targets) > played)
        self.assertEqual(controller.snapshot().index, 0)
        controller.send("repeat")
        wait_for(lambda: controller.snapshot().repeat == "off")

    def test_shuffle_repeat_all_avoids_same_track_at_cycle_boundary(self):
        tracks = (NS(id=1), NS(id=2), NS(id=3))
        def resolve(_):
            future = Future()
            future.set_result("local-target")
            return future
        player = StubPlayer()
        controller = PlaybackController(resolve, lambda: player)
        self.addCleanup(controller.close)
        controller._random.seed(1)
        controller.send("shuffle")
        controller.send("repeat")
        wait_for(lambda: controller.snapshot().repeat == "all")
        controller.send("play", (tracks, 2))
        wait_for(lambda: controller.snapshot().status == "Reproduciendo")
        player.events.put({"event": "end-file", "reason": "eof"})
        wait_for(lambda: len(player.targets) == 2)
        self.assertIsNot(controller.snapshot().tracks[0], tracks[2])

    def test_real_mpv_plays_two_local_tracks_and_finishes_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silence.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 6400)
            def resolve(track):
                future = Future()
                future.set_result(str(path))
                return future
            controller = PlaybackController(resolve, lambda: Player(extra_args=["--ao=null"]))
            try:
                controller.send("play", ([NS(id=1), NS(id=2)], 0))
                wait_for(lambda: controller.snapshot().index == 1)
                wait_for(lambda: controller.snapshot().status == "Detenido")
                self.assertFalse(controller.snapshot().error)
            finally:
                controller.close()
            self.assertFalse(controller._thread.is_alive())


class LibraryTests(unittest.TestCase):
    def test_read_and_stream_requests_share_one_serial_executor(self):
        release = threading.Event()
        called = []
        def read():
            called.append("read")
            release.wait(2)
            return []
        persist = Mock()
        library = Library(NS(playlists=read), persist)
        try:
            first = library.submit("playlists")
            second = library.resolve(lambda *_: called.append("stream"), None, None)
            wait_for(lambda: bool(called))
            self.assertEqual(called, ["read"])
            release.set()
            first.result(2)
            second.result(2)
            self.assertEqual(called, ["read", "stream"])
            self.assertEqual(persist.call_count, 2)
        finally:
            release.set()
            library.close()

    def test_add_refetches_ownership_and_uses_duplicate_protection(self):
        session = Mock()
        session.user.id = 7
        catalog = Catalog(session)
        playlist = NS(id="7cfb726d-7e82-4f7a-b6e4-0b0cf73a76dd")
        current = session.playlist.return_value
        current.creator.id = 8
        with self.assertRaises(AppError):
            catalog.add(playlist, NS(id=100))
        current.add.assert_not_called()
        current.creator.id = 7
        catalog.add(playlist, NS(id=100))
        current.add.assert_called_once_with(["100"], allow_duplicates=False)

    def test_search_and_create_validate_input_before_network(self):
        session = Mock()
        catalog = Catalog(session)
        for method in (catalog.create, catalog.search):
            with self.assertRaises(AppError):
                method("   ")
            with self.assertRaises(AppError):
                method("x" * 201)
        session.search.assert_not_called()
        session.user.create_playlist.assert_not_called()

    def test_cover_rejects_non_tidal_image_host_before_request(self):
        session = Mock()
        catalog = Catalog(session)
        album = NS(cover="present", image=lambda size: "https://example.org/image.jpg")
        with self.assertRaises(AppError), __import__("unittest.mock").mock.patch(
                "noneto.services.tidal.requests.Session") as connection:
            catalog.cover(NS(album=album))
        connection.assert_not_called()
