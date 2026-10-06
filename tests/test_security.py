import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from tidalapi.exceptions import AuthenticationError

from noneto import app
from noneto.auth.service import login
from noneto.auth.storage import protect, read_session, save_session
from noneto.audio.mpv_install import install_mpv
from noneto.audio.streams import stream_target, validate_dash, MAX_MANIFEST_BYTES
from noneto.errors import AppError
from noneto.security import https_url, terminal_text
from noneto.services.tidal import TimedSession

MPD = '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011">{}</MPD>'


class SecurityTests(unittest.TestCase):
    def test_rejects_unsafe_media_destinations(self):
        for url in ["http://example.com/audio", "file:///C:/secret", "data:text/plain,hello",
                    "https://localhost/audio", "https://127.0.0.1/a", "https://10.0.0.1/a",
                    "https://[::1]/a", "https://169.254.169.254/a", "https://2130706433/a",
                    "https://0177.0.0.1/a", "https://0x7f.0.0.1/a", "https://host.local/a",
                    "https://name:secret@example.com/a", "https://example.com:22/a",
                    "https://example.com\\@localhost/a", "https://example.com/\nsecret"]:
            with self.subTest(url=url), self.assertRaises(AppError):
                https_url(url)
        self.assertEqual(https_url("https://media.example.com/a?token=opaque"),
                         "https://media.example.com/a?token=opaque")

    def test_dash_external_entities_local_references_and_drm_rejected(self):
        payloads = [
            '<!DOCTYPE MPD [<!ENTITY x SYSTEM "file:///C:/secret">]>' + MPD.format('&x;'),
            MPD.format('<BaseURL>file:///C:/secret</BaseURL>'),
            MPD.format('<SegmentTemplate media="relative-$Number$.m4s"/>'),
            MPD.format('<BaseURL>https://example.com/</BaseURL><SegmentTemplate media="http://example.com/a"/>'),
            MPD.format('<BaseURL>https://example.com/</BaseURL><ContentProtection/>'),
            MPD.format('<BaseURL>https://example.com/</BaseURL><Location>file:///secret</Location>'),
            MPD.format('<Period xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://example.com/a"/>'),
            MPD.format('<BaseURL>https://example.com/</BaseURL><Period xml:base="file:///C:/"/>'),
            MPD.format('<BaseURL>https://example.com/</BaseURL><x:BaseURL xmlns:x="urn:other">file:///secret</x:BaseURL>'),
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(AppError):
                validate_dash(payload)

    def test_dash_relative_segments_use_validated_https_base(self):
        validate_dash(MPD.format('<BaseURL>https://example.com/audio/</BaseURL>'
                                '<Period><AdaptationSet><Representation>'
                                '<SegmentTemplate initialization="init.mp4" media="$Number$.m4s"/>'
                                '</Representation></AdaptationSet></Period>'))

    def test_size_limit_precedes_manifest_decode_and_no_temp_on_rejection(self):
        stream = Mock(manifest="x" * (MAX_MANIFEST_BYTES * 2))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(AppError):
                stream_target(Mock(get_stream=Mock(return_value=stream)), directory)
            stream.get_manifest_data.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_https_and_authorized_host_required_before_http(self):
        with TimedSession() as client, patch.object(requests.Session, "request") as request:
            for url in ("http://api.tidal.com/v1", "https://api.tidal.com.evil.test/v1"):
                with self.assertRaises(AppError):
                    client.get(url)
            request.assert_not_called()

    def test_oauth_redirect_is_not_followed_and_tls_cannot_be_disabled(self):
        response = Mock(status_code=307)
        with TimedSession() as client, patch.object(requests.Session, "request", return_value=response) as request:
            with self.assertRaises(AppError):
                client.post("https://auth.tidal.com/v1/oauth2/token", data={"refresh_token": "test"}, verify=False)
            self.assertFalse(request.call_args.kwargs["allow_redirects"])
            self.assertTrue(request.call_args.kwargs["verify"])
            self.assertEqual(request.call_args.kwargs["timeout"], (10, 30))
            self.assertFalse(client.trust_env)
            response.close.assert_called_once()

    def test_untrusted_authorization_link_is_neither_printed_nor_opened(self):
        session = Mock()
        session.get_link_login.return_value.verification_uri_complete = "https://tidal.com.evil.test/authorize"
        notify = Mock()
        with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: False)), \
             patch("noneto.auth.service.webbrowser.open") as browser:
            with self.assertRaises(AppError):
                login(session, notify)
            browser.assert_not_called()
            notify.assert_not_called()
            session.process_link_login.assert_not_called()

    def test_expired_refresh_token_restarts_login_but_network_error_does_not(self):
        for error, should_restart in [(AuthenticationError("secret"), True), (requests.Timeout("secret"), False)]:
            session = Mock()
            session.load_oauth_session.side_effect = error
            session.get_link_login.return_value.verification_uri_complete = "link.tidal.com/abc"
            with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: True)), \
                 patch("noneto.auth.service.read_session", return_value={}), \
                 patch("noneto.auth.service.save_session"), patch("noneto.auth.service.webbrowser.open"):
                if should_restart:
                    login(session, Mock())
                    session.process_link_login.assert_called_once()
                else:
                    with self.assertRaises(requests.Timeout):
                        login(session, Mock())
                    session.get_link_login.assert_not_called()

    def test_stored_session_schema_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.bin"
            for data in (b"corrupt", protect(json.dumps({"access_token": "x"}).encode())):
                path.write_bytes(data)
                with self.assertRaises((OSError, ValueError)):
                    read_session(path)

    def test_failed_atomic_save_preserves_previous_ciphertext_and_cleans_temp(self):
        session = SimpleNamespace(token_type="Bearer", access_token="first", refresh_token="test", is_pkce=False, expiry_time=None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.bin"
            save_session(session, path)
            original = path.read_bytes()
            session.access_token = "second"
            with patch.object(Path, "replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
                save_session(session, path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_remote_metadata_cannot_emit_terminal_controls(self):
        safe = terminal_text("Track\x1b]52;c;payload\x07\r\n\x9b31m\u202e")
        for char in ("\x1b", "\x07", "\r", "\n", "\x9b", "\u202e"):
            self.assertNotIn(char, safe)

    def test_dependency_runtime_error_does_not_leak_tokens(self):
        output = io.StringIO()
        with patch("noneto.app.find_mpv"), patch("noneto.app.create_session", side_effect=RuntimeError("access_token=TOP_SECRET")), \
             patch("noneto.app.configure", return_value=Path("test-diagnostics.log")), \
             patch("noneto.app.emit"), patch("noneto.app.failure"), \
             contextlib.redirect_stdout(output):
            self.assertEqual(app.main([]), 1)
        self.assertNotIn("TOP_SECRET", output.getvalue())
        self.assertIn("RuntimeError", output.getvalue())


class MpvInstallTests(unittest.TestCase):
    def test_download_with_wrong_hash_installs_nothing(self):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b"not the pinned mpv archive"]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with tempfile.TemporaryDirectory() as home, \
                patch.dict("os.environ", {"LOCALAPPDATA": home}), \
                patch("requests.Session.get", return_value=response) as get:
            with self.assertRaises(AppError):
                install_mpv()
            self.assertTrue(get.call_args.kwargs["verify"])
            self.assertFalse((Path(home) / "Noneto" / "mpv").exists())

    def test_existing_mpv_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as home, \
                patch.dict("os.environ", {"LOCALAPPDATA": home}), \
                patch("requests.Session.get") as get:
            target = Path(home) / "Noneto" / "mpv"
            target.mkdir(parents=True)
            (target / "mpv.exe").write_bytes(b"")
            self.assertEqual(install_mpv(), target / "mpv.exe")
            get.assert_not_called()
