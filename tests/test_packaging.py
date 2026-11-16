"""Frozen-runtime boundaries must not depend on the user's previous uv installation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from noneto.audio.mpv_install import install_mpv, mpv_dir
from noneto.audio.player import find_mpv
from noneto.errors import AppError


@unittest.skipUnless(os.name == "nt", "Windows distribution")
class FrozenRuntimeTests(unittest.TestCase):
    def test_relocated_bundle_uses_its_own_mpv_without_network(self):
        with tempfile.TemporaryDirectory(prefix="noneto package ") as directory:
            executable = Path(directory) / "Música" / "noneto.exe"
            player = executable.parent / "mpv/mpv.exe"
            player.parent.mkdir(parents=True)
            player.touch()
            with patch("sys.frozen", True, create=True), patch("sys.executable", str(executable)), \
                    patch("requests.Session.get") as network:
                self.assertEqual(mpv_dir(), player.parent)
                self.assertEqual(find_mpv(), str(player))
                self.assertEqual(install_mpv(), player)
            network.assert_not_called()

    def test_missing_bundle_mpv_does_not_use_an_old_user_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "profile/Noneto/mpv/mpv.exe"
            old.parent.mkdir(parents=True)
            old.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root / "profile")}), \
                    patch("sys.frozen", True, create=True), \
                    patch("sys.executable", str(root / "new/noneto.exe")):
                with self.assertRaises(AppError):
                    find_mpv()

    def test_python_installation_keeps_the_existing_user_data_location(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"LOCALAPPDATA": directory}), \
                patch("sys.frozen", False, create=True):
            self.assertEqual(mpv_dir(), Path(directory) / "Noneto/mpv")
