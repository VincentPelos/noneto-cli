"""A package-manager installation needs no separate audio setup command."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from noneto import app
from noneto.errors import AppError


@unittest.skipUnless(os.name == "nt", "Windows automatic mpv setup")
class FirstRunTests(unittest.TestCase):
    def launch(self, directory, installer):
        # Stop before authentication. These tests never read the user's session or use the network.
        with patch("noneto.app.mpv_dir", return_value=Path(directory)), \
                patch("noneto.app.install_mpv", installer), \
                patch("noneto.app.find_mpv"), \
                patch("noneto.app.create_session", side_effect=RuntimeError("stop before login")) as session, \
                patch("noneto.app.configure", return_value=Path(directory) / "logs"), \
                patch("noneto.app.emit"), patch("noneto.app.failure"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main([]), 1)
        return session

    def test_fresh_launch_prepares_audio_before_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            install = Mock(return_value=Path(directory) / "mpv.exe")
            session = self.launch(directory, install)
            install.assert_called_once_with()
            session.assert_called_once()

    def test_existing_audio_does_not_download_again(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "mpv.exe").touch()
            from unittest.mock import Mock
            install = Mock(side_effect=AssertionError("Unexpected network setup"))
            session = self.launch(directory, install)
            install.assert_not_called()
            session.assert_called_once()

    def test_setup_failure_stops_before_authentication_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            from unittest.mock import Mock
            install = Mock(side_effect=AppError("Could not prepare mpv"))
            session = self.launch(directory, install)
            session.assert_not_called()
            self.assertFalse((Path(directory) / "mpv.exe").exists())
            install.side_effect = None
            install.return_value = Path(directory) / "mpv.exe"
            session = self.launch(directory, install)
            self.assertEqual(install.call_count, 2)
            session.assert_called_once()

    def test_help_and_logout_never_prepare_audio(self):
        with patch("noneto.app.install_mpv") as install, patch("noneto.app.logout") as logout, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                app.main(["--help"])
            self.assertEqual(result.exception.code, 0)
            self.assertEqual(app.main(["--logout"]), 0)
            logout.assert_called_once()
            install.assert_not_called()
