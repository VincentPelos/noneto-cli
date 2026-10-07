from pathlib import Path
import ast
import queue
import unittest
from unittest.mock import Mock, patch

from noneto.audio.player import Player
from noneto.errors import AppError
from noneto.services.tidal import all_pages, IDLE_RESET_SECONDS, TimedSession


class StabilityTests(unittest.TestCase):
    def test_pagination_cannot_loop_forever(self):
        fetch = Mock(return_value=["same", "same"])
        with self.assertRaises(AppError):
            all_pages(fetch, limit=2, max_pages=3)
        self.assertEqual(fetch.call_count, 3)

    def test_retries_bounded_and_oauth_post_not_retried_on_status(self):
        with TimedSession() as session:
            retries = session.get_adapter("https://").max_retries
            self.assertFalse(retries.is_retry("POST", 503))
            self.assertTrue(retries.is_retry("GET", 503))
            self.assertEqual(retries.total, 2)
            self.assertEqual(retries.read, 0)

    def test_idle_pooled_connections_are_dropped_before_next_request(self):
        # After a whole song without API calls Tidal may be closing the kept-alive socket.
        response = Mock(status_code=200)
        with TimedSession() as session, \
                patch("requests.Session.request", return_value=response), \
                patch.object(session.get_adapter("https://").poolmanager, "clear") as clear:
            session.get("https://api.tidal.com/v1/tracks/1")
            clear.assert_not_called()
            session.get("https://api.tidal.com/v1/tracks/1")
            clear.assert_not_called()
            session._last_used -= IDLE_RESET_SECONDS + 1
            session.get("https://api.tidal.com/v1/tracks/1")
            clear.assert_called_once()

    def test_real_mpv_rejects_bad_command_without_leaking_arguments(self):
        with Player(extra_args=["--ao=null"]) as player:
            with self.assertRaises(AppError) as error:
                player.command("not-a-command", "SECRET")
            self.assertNotIn("SECRET", str(error.exception))
            self.assertEqual(player._pending, {})
            self.assertIsInstance(player.command("get_property", "mpv-version"), str)

    def test_real_mpv_security_options(self):
        with Player(extra_args=["--ao=null"]) as player:
            for name in ("load-scripts", "ytdl", "access-references", "terminal"):
                self.assertFalse(player.command("get_property", "options/" + name))
            self.assertTrue(player.command("get_property", "options/tls-verify"))
            self.assertEqual(player.command("get_property", "options/network-timeout"), 30)

    def test_only_validated_dash_manifests_may_follow_references(self):
        # TIDAL PKCE streams are DASH: segment URLs are references, checked by validate_dash beforehand.
        player = Player.__new__(Player)
        player.events = Mock(get_nowait=Mock(side_effect=queue.Empty))
        player.command = Mock()
        player.play(r"C:\Temp\noneto-x\tmp1.mpd")
        player.command.assert_called_with("loadfile", r"C:\Temp\noneto-x\tmp1.mpd", "replace", -1,
                                          "access-references=yes")
        player.play("https://example.com/audio.flac")
        player.command.assert_called_with("loadfile", "https://example.com/audio.flac", "replace")

    def test_unanswered_command_times_out_and_removes_pending_request(self):
        with Player(extra_args=["--ao=null"]) as player:
            with patch.object(player, "_receive"), self.assertRaises(AppError):
                player.command("get_property", "mpv-version", timeout=0.1)
            self.assertEqual(player._pending, {})

    def test_ipc_disconnect_wakes_pending_command(self):
        with Player(extra_args=["--ao=null"]) as player:
            player.pipe.close()
            self.assertTrue(player.failed.wait(2))
            with self.assertRaises(AppError):
                player.command("get_property", "mpv-version")

    def test_modules_keep_auth_audio_and_ui_separate(self):
        root = Path(__file__).resolve().parents[1] / "noneto"
        for group, forbidden in {"auth": {"audio", "ui"}, "audio": {"auth", "ui", "tidalapi"},
                                 "ui": {"auth", "audio"}}.items():
            for path in (root / group).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                for node in ast.walk(tree):
                    modules = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                               [n.name for n in node.names] if isinstance(node, ast.Import) else [])
                    for module in modules:
                        self.assertFalse(set(module.split(".")) & forbidden, f"{path}: {module}")
