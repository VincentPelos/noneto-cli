import queue
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import wave

from noneto.auth.storage import save_session, read_session
from noneto.audio.player import Player
from noneto.audio.streams import stream_target
from noneto.services.tidal import all_pages, playlist_id


class CoreTests(unittest.TestCase):
    def test_windows_credentials_are_encrypted_and_recoverable(self):
        session = SimpleNamespace(token_type="Bearer", access_token="test-access-token",
                                  refresh_token="test-refresh-token", is_pkce=False, expiry_time=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.bin"
            save_session(session, path)
            self.assertNotIn(b"test-access-token", path.read_bytes())
            self.assertEqual(read_session(path)["refresh_token"], "test-refresh-token")
            session.access_token = "refreshed-access-token"
            save_session(session, path)
            self.assertEqual(read_session(path)["access_token"], "refreshed-access-token")

    def test_playlist_pagination_preserves_order_and_duplicates(self):
        entries = ["first", "repeat", "repeat", "last"]
        calls = []
        def fetch(limit, offset):
            calls.append(offset)
            return entries[offset:offset + limit]
        self.assertEqual(all_pages(fetch, limit=2), entries)
        self.assertEqual(calls, [0, 2, 4])

    def test_playlist_link_and_uuid(self):
        identifier = "01234567-89ab-cdef-0123-456789abcdef"
        for value in (identifier, f"https://tidal.com/browse/playlist/{identifier}?u=1",
                      f"https://listen.tidal.com/playlist/{identifier}"):
            self.assertEqual(playlist_id(value), identifier)
        with self.assertRaises(ValueError):
            playlist_id("https://example.com/playlist/" + identifier)

    def test_stream_url_and_dash_are_handled_separately(self):
        stream = SimpleNamespace(manifest_mime_type="application/vnd.tidal.bts",
                                 get_manifest_data=lambda: json.dumps({"encryptionType": "NONE", "urls": ["https://example.com/audio"]}))
        track = SimpleNamespace(get_stream=lambda: stream)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(stream_target(track, directory), "https://example.com/audio")
            stream.manifest_mime_type = "application/dash+xml"
            stream.get_manifest_data = lambda: '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><BaseURL>https://example.com/audio</BaseURL></MPD>'
            path = Path(stream_target(track, directory))
            self.assertIn("<MPD", path.read_text())

    def test_unsupported_encryption_is_reported(self):
        stream = Mock(manifest_mime_type="application/vnd.tidal.bts")
        stream.get_manifest_data.return_value = json.dumps({"encryptionType": "UNSUPPORTED", "urls": ["https://example.com/audio"]})
        track = Mock()
        track.get_stream.return_value = stream
        with self.assertRaises(RuntimeError):
            stream_target(track, ".")

    def test_real_mpv_named_pipe_load_pause_replace_and_eof(self):
        # Use a local silent WAV and the null audio output: no account or speakers.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.wav"
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(b"\0\0" * 8000)
            with Player(extra_args=["--ao=null"]) as player:
                def wait_for(name, reason=None):
                    deadline = time.monotonic() + 8
                    while time.monotonic() < deadline:
                        try:
                            event = player.events.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        self.assertNotEqual(event["event"], "command-error")
                        if event["event"] == name and (reason is None or event.get("reason") == reason):
                            return event
                    self.fail(f"mpv event missing: {name} {reason}")
                player.play(path)
                wait_for("file-loaded")
                player.command("set_property", "pause", True)
                player.command("add", "volume", -5)
                player.command("stop")
                wait_for("end-file", "stop")
                player.play(path)
                wait_for("file-loaded")
                wait_for("end-file", "eof")
            self.assertIsNotNone(player.process.poll())


if __name__ == "__main__":
    unittest.main()
