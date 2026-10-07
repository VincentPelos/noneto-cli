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
from noneto.security import https_url, pkce_code, terminal_text
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

    @staticmethod
    def pkce_session(login_url="https://login.tidal.com/authorize?client_id=x"):
        session = Mock(is_pkce=False)
        session.config.pkce_uri_redirect = "https://tidal.com/android/login/auth"
        session.pkce_login_url.return_value = login_url
        session.pkce_get_auth_token.return_value = {"access_token": "a"}
        return session

    def test_untrusted_authorization_link_is_never_shown(self):
        session = self.pkce_session("https://login.tidal.com.evil.test/authorize")
        notify, ask = Mock(), Mock()
        with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: False)):
            with self.assertRaises(AppError):
                login(session, notify, ask)
        notify.assert_not_called()
        ask.assert_not_called()

    def test_pkce_login_sends_only_the_validated_code(self):
        session = self.pkce_session()
        ask = Mock(return_value="  https://tidal.com/android/login/auth?code=abc-123_X&lang=es  ")
        notify = Mock()
        with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: False)), \
             patch("noneto.auth.service.save_session") as save:
            login(session, notify, ask)
        ask.assert_called_once_with("redirect", url="https://login.tidal.com/authorize?client_id=x",
                                    redirect="https://tidal.com/android/login/auth")
        session.pkce_get_auth_token.assert_called_once_with("https://tidal.com/android/login/auth?code=abc-123_X")
        session.process_auth_token.assert_called_once_with({"access_token": "a"}, is_pkce_token=True)
        save.assert_called_once_with(session)
        self.assertEqual([c.args[0] for c in notify.call_args_list], ["session-started"])

    def test_cancelled_or_invalid_redirect_never_contacts_tidal(self):
        for answer in ("", None, "https://evil.test/android/login/auth?code=a",
                       "https://tidal.com/android/login/auth?error=access_denied"):
            session = self.pkce_session()
            with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: False)):
                with self.assertRaises(AppError):
                    login(session, Mock(), Mock(return_value=answer))
            session.pkce_get_auth_token.assert_not_called()

    def test_rejected_code_is_reported_without_its_value(self):
        session = self.pkce_session()
        session.pkce_get_auth_token.side_effect = requests.HTTPError("code=secret")
        with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: False)):
            with self.assertRaises(AppError) as raised:
                login(session, Mock(), Mock(return_value="https://tidal.com/android/login/auth?code=secret"))
        self.assertNotIn("secret", str(raised.exception))

    def test_pkce_code_validation(self):
        redirect = "https://tidal.com/android/login/auth"
        self.assertEqual(pkce_code(redirect + "/?code=Ab9", redirect), "Ab9")
        for value in (None, "", "code=Ab9", "http://tidal.com/android/login/auth?code=a",
                      "https://tidal.com:8443/android/login/auth?code=a", "https://user@tidal.com/android/login/auth?code=a",
                      redirect, redirect + "?code=", redirect + "?code=a&code=b", redirect + "?code=a%20b",
                      redirect + "?code=a#frag", redirect + "?error=x&code=a", "https://tidal.com/login?code=a"):
            with self.assertRaises(AppError, msg=value):
                pkce_code(value, redirect)

    def test_stored_sessions_restore_only_when_pkce(self):
        # Device-code sessions from earlier versions cannot reach Hi-Res: log in again with PKCE.
        for is_pkce, error, expected in [(True, None, "session-restored"), (False, None, "session-upgrade"),
                                         (True, AuthenticationError("secret"), "session-invalid")]:
            session = self.pkce_session()
            session.is_pkce = is_pkce
            session.load_oauth_session.side_effect = error
            session.load_oauth_session.return_value = True
            notify = Mock()
            with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: True)), \
                 patch("noneto.auth.service.read_session", return_value={}), \
                 patch("noneto.auth.service.save_session"):
                login(session, notify, Mock(return_value="https://tidal.com/android/login/auth?code=a"))
            self.assertEqual(notify.call_args_list[0].args[0], expected)
            self.assertEqual(session.pkce_get_auth_token.called, expected != "session-restored")

    def test_network_error_while_restoring_does_not_start_a_login(self):
        session = self.pkce_session()
        session.load_oauth_session.side_effect = requests.Timeout("secret")
        with patch("noneto.auth.service.session_path", return_value=Mock(exists=lambda: True)), \
             patch("noneto.auth.service.read_session", return_value={}):
            with self.assertRaises(requests.Timeout):
                login(session, Mock(), Mock())
        session.pkce_login_url.assert_not_called()

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
