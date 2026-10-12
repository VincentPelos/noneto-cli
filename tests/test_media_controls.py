import asyncio
from concurrent.futures import Future
from dataclasses import replace
from datetime import timedelta
import io
import os
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from PIL import Image

from noneto.audio.controller import PlaybackState
from noneto.audio.media_controls import MediaControls


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Media update timed out")


def track(identifier, title):
    return NS(id=identifier, name=title, artist=NS(name="Artist"),
              album=NS(name="Album"), duration=180)


class MediaControlsTests(unittest.TestCase):
    def make_bridge(self, tracks=None):
        self.player = Mock()
        self.player.snapshot.return_value = PlaybackState(tracks=tracks or (track(1, "First"),),
                                                          index=0, status="Reproduciendo", position=12, duration=180)
        self.backend = Mock()
        self.art = Future()
        bridge = MediaControls(self.player, Mock(return_value=self.art),
                               backend_factory=Mock(return_value=self.backend))
        self.addCleanup(bridge.close)
        wait_for(lambda: self.backend.update.called)
        return bridge

    def test_metadata_progress_and_buttons_use_the_noneto_queue(self):
        bridge = self.make_bridge((track(1, "First"), track(2, "Second")))
        self.backend.metadata.assert_called_with("First", "Artist", "Album")
        self.backend.update.assert_called_with("playing", 12, 180, True, True)
        for button, action, value in (("PAUSE", "set_pause", True), ("PLAY", "set_pause", False),
                                      ("NEXT", "next", None), ("PREVIOUS", "previous", None),
                                      ("STOP", "stop", None)):
            bridge._button(button)
            if value is None:
                self.player.send.assert_called_with(action)
            else:
                self.player.send.assert_called_with(action, value)
        bridge._seek(40)
        self.player.send.assert_called_with("seek_absolute", 40)
        count = self.player.send.call_count
        bridge._seek(float("nan"))
        self.assertEqual(self.player.send.call_count, count)
        bridge.close()
        self.backend.close.assert_called_once()
        bridge._button("PLAY")
        self.assertEqual(self.player.send.call_count, count)

    def test_old_artwork_does_not_replace_new_song(self):
        old, new = Future(), Future()
        old.set_running_or_notify_cancel()
        player = Mock(snapshot=Mock(return_value=PlaybackState(tracks=(track(1, "First"), track(2, "Second")),
                                                              index=0, status="Reproduciendo")))
        backend = Mock()
        art = Mock(side_effect=[old, new])
        bridge = MediaControls(player, art, backend_factory=Mock(return_value=backend))
        self.addCleanup(bridge.close)
        wait_for(lambda: art.call_count == 1)
        player.snapshot.return_value = replace(player.snapshot.return_value, index=1)
        wait_for(lambda: art.call_count == 2)
        old.set_result(b"old")
        new.set_result(b"new")
        wait_for(lambda: backend.thumbnail.called)
        backend.thumbnail.assert_called_once_with(b"new")
        backend.metadata.assert_called_with("Second", "Artist", "Album")

    def test_unavailable_windows_integration_keeps_audio_alive(self):
        player = Mock(snapshot=Mock(return_value=PlaybackState()))
        bridge = MediaControls(player, Mock(), backend_factory=Mock(side_effect=OSError("Not available")))
        bridge.close()
        self.assertFalse(bridge.available)
        player.close.assert_not_called()

    def test_artwork_failure_does_not_disable_metadata_or_controls(self):
        bridge = self.make_bridge()
        self.art.set_exception(OSError("Unavailable"))
        time.sleep(0.15)
        self.assertTrue(bridge.available)
        self.backend.thumbnail.assert_not_called()
        self.player.snapshot.return_value = replace(self.player.snapshot.return_value, paused=True, status="Pausado")
        wait_for(lambda: self.backend.update.call_args.args[0] == "paused")


@unittest.skipUnless(os.name == "nt", "Windows SMTC")
class NativeMediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_receives_artwork_timeline_buttons_and_removes_session_on_close(self):
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
            import winrt.windows.foundation.collections
        except ImportError:
            self.skipTest("Install the windows-test extra for native session inspection")
        player = Mock(snapshot=Mock(return_value=PlaybackState(tracks=(track(9876, "Noneto SMTC test"),),
                                                              index=0, status="Reproduciendo", position=12, duration=180)))
        image = Image.new("RGB", (80, 80), "navy")
        stream = io.BytesIO()
        image.save(stream, format="JPEG")
        artwork = Future()
        artwork.set_result(stream.getvalue())
        bridge = MediaControls(player, Mock(return_value=artwork))
        try:
            self.assertTrue(bridge.available)
            manager = await Manager.request_async()
            session = None
            for _ in range(40):
                sessions = [s for s in manager.get_sessions() if s.source_app_user_model_id == "Noneto.CLI"]
                for candidate in sessions:
                    media = await candidate.try_get_media_properties_async()
                    if media.title == "Noneto SMTC test" and media.thumbnail is not None:
                        session = candidate
                        break
                if session is not None:
                    break
                await asyncio.sleep(0.05)
            self.assertIsNotNone(session)
            self.assertEqual(media.artist, "Artist")
            self.assertEqual(media.album_title, "Album")
            self.assertEqual(session.get_timeline_properties().position, timedelta(seconds=12))
            self.assertEqual(session.get_timeline_properties().end_time, timedelta(seconds=180))
            self.assertTrue(await session.try_pause_async())
            for _ in range(30):
                if player.send.called:
                    break
                await asyncio.sleep(0.05)
            player.send.assert_called_with("set_pause", True)
            self.assertTrue(await session.try_change_playback_position_async(40 * 10_000_000))
            for _ in range(30):
                if player.send.call_args.args == ("seek_absolute", 40.0):
                    break
                await asyncio.sleep(0.05)
            player.send.assert_called_with("seek_absolute", 40.0)
        finally:
            bridge.close()
        for _ in range(40):
            remaining = []
            for candidate in manager.get_sessions():
                if candidate.source_app_user_model_id == "Noneto.CLI":
                    data = await candidate.try_get_media_properties_async()
                    if data.title == "Noneto SMTC test":
                        remaining.append(candidate)
            if not remaining:
                break
            await asyncio.sleep(0.05)
        self.assertFalse(remaining)
