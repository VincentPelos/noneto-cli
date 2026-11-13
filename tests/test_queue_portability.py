from concurrent.futures import Future
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from noneto.audio.controller import PlaybackController, PlaybackState
from noneto.audio.player import Player
from noneto.auth import storage
from noneto.errors import AppError
from noneto.platform import paths
from noneto.services.library import Library
from noneto.services.tidal import Catalog
from noneto.state.queue import QueueStore, SavedTrack
from noneto.ui.terminal import Terminal
from tests.test_library_controller import StubPlayer, wait_for


def ready(target):
    future = Future()
    future.set_result(target)
    return future


class QueueTests(unittest.TestCase):
    def test_restores_duplicates_order_modes_position_without_autoplay(self):
        with tempfile.TemporaryDirectory() as directory:
            store = QueueStore("account", Path(directory) / "queue.json")
            tracks = (NS(id=1, name="One"), NS(id=2, name="Two"), NS(id=1, name="One"))
            store.save(PlaybackState(index=1, position=7.5, volume=42, shuffle=True, repeat="all"),
                       tracks, [2, 0, 1])
            resolver = Mock(side_effect=lambda track: ready(track.id))
            player = StubPlayer()
            controller = PlaybackController(resolver, lambda: player, queue_store=store)
            try:
                state = controller.snapshot()
                self.assertEqual([track.id for track in state.tracks], [1, 1, 2])
                self.assertEqual((state.index, state.position, state.volume, state.shuffle, state.repeat),
                                 (1, 7.5, 42, True, "all"))
                self.assertTrue(state.paused)
                time.sleep(0.1)
                resolver.assert_not_called()
                controller.send("pause")
                wait_for(lambda: ("seek", 7.5, "absolute") in player.commands)
                self.assertEqual(player.targets, [1])
                controller.send("shuffle")
                wait_for(lambda: not controller.snapshot().shuffle)
                self.assertEqual([t.id for t in controller.snapshot().tracks], [1, 1, 2])
            finally:
                controller.close()
            self.assertIsNotNone(store.load())

    def test_only_allowlisted_metadata_is_written_and_account_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            store = QueueStore("a", path)
            track = NS(id=3, name="Song\x1b[31m", access_token="TOKEN_SECRET",
                       stream_url="SIGNED_SECRET", duration=15)
            store.save(PlaybackState(index=0), (track, track), [0, 1])
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("TOKEN_SECRET", raw)
            self.assertNotIn("SIGNED_SECRET", raw)
            self.assertNotIn("\\u001b", raw)
            self.assertIsNone(QueueStore("other", path).load())
            self.assertEqual(len(store.load()["tracks"]), 2)

    def test_invalid_queue_is_discarded_and_failed_save_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            store = QueueStore("a", path)
            store.save(PlaybackState(index=0), (NS(id=1),), [0])
            original = path.read_bytes()
            with patch.object(Path, "replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
                store.save(PlaybackState(index=-1), (), [])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            data = json.loads(original)
            for field, value in (("order", [0, 0]), ("index", 1), ("position", float("nan")),
                                 ("volume", True), ("repeat", "bad"), ("tracks", [None])):
                path.write_text(json.dumps({**data, field: value}), encoding="utf-8")
                self.assertIsNone(store.load(), field)
            path.write_bytes(b"corrupt")
            self.assertIsNone(store.load())

    def test_save_error_does_not_interrupt_playback(self):
        store = Mock(load=Mock(return_value=None), save=Mock(side_effect=OSError("disk")))
        player = StubPlayer()
        controller = PlaybackController(lambda track: ready(track.id), lambda: player, queue_store=store)
        try:
            controller.send("play", ([NS(id=1)], 0))
            wait_for(lambda: controller.snapshot().status == "Playing")
            self.assertFalse(controller.snapshot().error)
        finally:
            controller.close()
        self.assertTrue(store.save.called)

    def test_restored_tracks_are_hydrated_in_serial_api_executor(self):
        session = Mock()
        session.track.return_value = NS(id=7, name="Fresh")
        track = SavedTrack(7, "Stored", NS(name="Artist"), NS(name="Album"), 10)
        library = Library(Catalog(session))
        try:
            resolver = Mock(return_value="target")
            self.assertEqual(library.resolve(resolver, track, "temp").result(2), "target")
            resolver.assert_called_once_with(session.track.return_value, "temp")
            library.resolve(resolver, track, "temp").result(2)
            session.track.assert_called_once_with(7)
        finally:
            library.close()


class StagingPlayer(StubPlayer):
    def __init__(self):
        super().__init__()
        self.staged = []
        self.cleared = 0

    def stage(self, target):
        self.staged.append(target)

    def clear_next(self):
        self.cleared += 1

    def eof(self):
        self.events.put({"event": "end-file", "reason": "eof"})
        self.events.put({"event": "start-file"})
        self.events.put({"event": "file-loaded"})


class PrefetchTests(unittest.TestCase):
    def test_real_mpv_continues_three_local_files_without_reloading_after_eof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silence.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 12000)
            player = Player(extra_args=["--ao=null"])
            player.play = Mock(wraps=player.play)
            player.stage = Mock(wraps=player.stage)
            controller = PlaybackController(lambda _: ready(str(path)), lambda: player)
            try:
                controller.send("play", ([NS(id=1), NS(id=2), NS(id=3)], 0))
                wait_for(lambda: controller.snapshot().index == 2, timeout=10)
                wait_for(lambda: controller.snapshot().status == "Stopped", timeout=10)
                self.assertEqual(player.play.call_count, 1)
                self.assertEqual(player.stage.call_count, 2)
                self.assertFalse(controller.snapshot().error)
            finally:
                controller.close()

    def controller(self, resolve):
        player = StagingPlayer()
        controller = PlaybackController(resolve, lambda: player)
        self.addCleanup(controller.close)
        controller.send("play", ([NS(id=1), NS(id=2), NS(id=3)], 0))
        wait_for(lambda: controller.snapshot().status == "Playing")
        return controller, player

    def test_eof_uses_staged_track_without_stop_or_foreground_reload(self):
        resolve = Mock(side_effect=lambda track: ready(track.id))
        controller, player = self.controller(resolve)
        wait_for(lambda: player.staged == [2])
        stops = player.commands.count(("stop",))
        player.eof()
        wait_for(lambda: controller.snapshot().index == 1 and controller.snapshot().status == "Playing")
        self.assertEqual(player.targets, [1])
        self.assertEqual(player.commands.count(("stop",)), stops)
        self.assertEqual([call.args[0].id for call in resolve.call_args_list].count(2), 1)

    def test_failed_prefetch_keeps_current_audio_and_retries_on_next(self):
        bad = Future()
        bad.set_exception(RuntimeError("SIGNED_SECRET"))
        resolve = Mock(side_effect=[ready(1), bad, ready(2), ready(3)])
        controller, player = self.controller(resolve)
        wait_for(lambda: resolve.call_count == 2)
        time.sleep(0.2)
        self.assertEqual(controller.snapshot().status, "Playing")
        self.assertFalse(controller.snapshot().error)
        controller.send("next")
        wait_for(lambda: player.targets == [1, 2])

    def test_stop_discards_running_prefetch_and_never_plays_late_result(self):
        pending = Future()
        pending.set_running_or_notify_cancel()
        resolve = Mock(side_effect=[ready(1), pending])
        controller, player = self.controller(resolve)
        wait_for(lambda: resolve.call_count == 2)
        controller.send("stop")
        wait_for(lambda: controller.snapshot().index == -1)
        pending.set_result("obsolete")
        time.sleep(0.15)
        self.assertEqual(player.targets, [1])
        self.assertEqual(player.staged, [])

    def test_pause_clears_staged_entry(self):
        controller, player = self.controller(lambda track: ready(track.id))
        wait_for(lambda: player.staged)
        controller.send("pause")
        wait_for(lambda: controller.snapshot().paused)
        self.assertEqual(player.cleared, 1)

    def test_manual_next_reuses_pending_prefetch(self):
        pending = Future()
        pending.set_running_or_notify_cancel()
        resolve = Mock(side_effect=[ready(1), pending, ready(3)])
        controller, player = self.controller(resolve)
        wait_for(lambda: resolve.call_count == 2)
        controller.send("next")
        wait_for(lambda: controller.snapshot().index == 1)
        pending.set_result(2)
        wait_for(lambda: player.targets == [1, 2])
        self.assertEqual([call.args[0].id for call in resolve.call_args_list].count(2), 1)


class PlatformTests(unittest.TestCase):
    def test_linux_xdg_paths_ignore_relative_xdg(self):
        with patch.object(sys, "platform", "linux"), patch.object(Path, "home", return_value=Path("/home/test")), \
                patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(tempfile.gettempdir()) / "config"), "XDG_STATE_HOME": "relative",
                                        "XDG_DATA_HOME": str(Path(tempfile.gettempdir()) / "data")}):
            self.assertEqual(paths.config_dir(), Path(tempfile.gettempdir()) / "config/noneto")
            self.assertEqual(paths.state_dir(), Path("/home/test/.local/state/noneto"))
            self.assertEqual(paths.data_dir(), Path(tempfile.gettempdir()) / "data/noneto")

    def test_unix_login_uses_browser_and_pasted_redirect(self):
        from noneto.ui.login_window import ClipboardUnavailable, WindowUnavailable
        with patch("noneto.ui.terminal.window_login", side_effect=WindowUnavailable), \
                patch("noneto.ui.terminal.clipboard_login", side_effect=ClipboardUnavailable), \
                patch("builtins.input", return_value=" https://redirect "), \
                patch("builtins.print"), patch("noneto.ui.terminal.webbrowser.open") as browser:
            self.assertEqual(Terminal().ask("redirect", url="https://login.tidal.com", redirect="https://redirect"),
                             "https://redirect")
            browser.assert_called_once_with("https://login.tidal.com")

    def test_native_keyring_roundtrip_and_delete_never_create_token_file(self):
        backend = Mock()
        payload = {}
        backend.set_password.side_effect = lambda service, user, value: payload.update(value=value)
        backend.get_password.side_effect = lambda *_: payload.get("value")
        backend.delete_password.side_effect = lambda *_: payload.clear()
        with tempfile.TemporaryDirectory() as directory, patch("noneto.auth.storage.os", NS(name="posix")), \
                patch("noneto.auth.storage._keyring", return_value=backend):
            path = Path(directory) / "session.bin"
            session = NS(token_type="Bearer", access_token="test", refresh_token="refresh", is_pkce=True, expiry_time=None)
            storage.save_session(session, path)
            self.assertTrue(storage.has_session())
            self.assertEqual(storage.read_session(path)["refresh_token"], "refresh")
            self.assertFalse(path.exists())
            storage.delete_session()
            self.assertFalse(storage.has_session())

    def test_missing_secure_backend_fails_without_plaintext_fallback(self):
        with patch.dict(sys.modules, {"keyring.backends.SecretService": None}), patch.object(sys, "platform", "linux"):
            with self.assertRaises(AppError):
                storage._keyring()
