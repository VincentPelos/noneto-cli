"""The public Windows release must not start experimental platform integrations."""
import contextlib
import io
import unittest
from unittest.mock import patch

from noneto import app
from noneto.i18n import set_language


class ReleasePlatformTests(unittest.TestCase):
    def tearDown(self):
        set_language("en")

    def test_unsupported_platform_cannot_start_or_modify_user_data(self):
        for platform in ("linux", "darwin"):
            for args in ([], ["--logout"], ["--install-mpv"]):
                with self.subTest(platform=platform, args=args), \
                        patch.object(app.sys, "platform", platform), \
                        patch.object(app, "logout") as logout, \
                        patch.object(app, "install_mpv") as install, \
                        patch.object(app, "configure") as configure, \
                        patch.object(app, "create_session") as session, \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(app.main(args), 1)
                    self.assertIn("Windows only", errors.getvalue())
                    for action in (logout, install, configure, session):
                        action.assert_not_called()

    def test_platform_message_respects_explicit_spanish(self):
        with patch.object(app.sys, "platform", "linux"), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(app.main(["--lang", "es"]), 1)
        self.assertIn("solo admite Windows", errors.getvalue())
