import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from noneto.errors import AppError
from noneto.ui.login_window import WindowUnavailable, clipboard_login, window_login
from noneto.ui.terminal import Terminal

REDIRECT = "https://tidal.com/android/login/auth"


class Handlers:
    def __init__(self):
        self.items = []

    def __iadd__(self, handler):
        self.items.append(handler)
        return self


def fake_webview(urls=(), start_error=None):
    """pywebview stand-in: start() 'navigates' through urls, firing request_sent for each."""
    window = NS(events=NS(request_sent=Handlers()), destroyed=False)
    window.destroy = lambda: setattr(window, "destroyed", True)
    module = NS(window=window, starts=[])

    def start(**options):
        module.starts.append(options)
        if start_error:
            raise start_error
        for url in urls:
            if window.destroyed:
                break
            for handler in window.events.request_sent.items:
                handler(NS(url=url))

    module.create_window = lambda title, url, **_: window
    module.start = start
    return module


class LoginWindowTests(unittest.TestCase):
    def test_captures_the_redirect_and_closes_the_window(self):
        webview = fake_webview(["https://login.tidal.com/authorize?x=1", "https://cdn.example/app.js",
                                REDIRECT + "?code=first", REDIRECT + "?code=second"])
        with patch.dict(sys.modules, {"webview": webview}):
            self.assertEqual(window_login("https://login.tidal.com/authorize", REDIRECT), REDIRECT + "?code=first")
        self.assertTrue(webview.window.destroyed)
        # Never the legacy MSHTML engine, and no cookies kept after the login.
        self.assertEqual(webview.starts, [{"gui": "edgechromium", "private_mode": True}])

    def test_lookalike_redirects_are_ignored(self):
        webview = fake_webview([REDIRECT + ".evil.test/?code=a", "https://evil.test/?u=" + REDIRECT + "?code=a",
                                REDIRECT + "x?code=a"])
        with patch.dict(sys.modules, {"webview": webview}):
            self.assertEqual(window_login("https://login.tidal.com/authorize", REDIRECT), "")

    def test_closing_the_window_cancels(self):
        with patch.dict(sys.modules, {"webview": fake_webview()}):
            self.assertEqual(window_login("https://login.tidal.com/authorize", REDIRECT), "")

    def test_missing_webview2_or_pywebview_is_reported_for_fallback(self):
        with patch.dict(sys.modules, {"webview": fake_webview(start_error=RuntimeError("no WebView2"))}):
            with self.assertRaises(WindowUnavailable):
                window_login("https://login.tidal.com/authorize", REDIRECT)
        with patch.dict(sys.modules, {"webview": None}):
            with self.assertRaises(WindowUnavailable):
                window_login("https://login.tidal.com/authorize", REDIRECT)

    def test_clipboard_fallback_ignores_old_and_unrelated_text(self):
        stale = REDIRECT + "?code=old"
        read = Mock(side_effect=[stale, stale, "hola", " " + REDIRECT + "?code=new "])
        self.assertEqual(clipboard_login(REDIRECT, read=read, interval=0), REDIRECT + "?code=new")
        with self.assertRaises(AppError):
            clipboard_login(REDIRECT, read=Mock(return_value=stale), interval=0, timeout=0.01)

    def test_terminal_uses_the_browser_only_when_the_window_is_unavailable(self):
        terminal = Terminal()
        with patch("noneto.ui.terminal.window_login", return_value=REDIRECT + "?code=a"), \
             patch("noneto.ui.terminal.webbrowser.open") as browser, patch("builtins.print"):
            self.assertEqual(terminal.ask("redirect", url="https://login.tidal.com/a", redirect=REDIRECT),
                             REDIRECT + "?code=a")
        browser.assert_not_called()
        with patch("noneto.ui.terminal.window_login", side_effect=WindowUnavailable), \
             patch("noneto.ui.terminal.clipboard_login", return_value=REDIRECT + "?code=b") as clipboard, \
             patch("noneto.ui.terminal.webbrowser.open") as browser, patch("builtins.print"):
            self.assertEqual(terminal.ask("redirect", url="https://login.tidal.com/a", redirect=REDIRECT),
                             REDIRECT + "?code=b")
        browser.assert_called_once_with("https://login.tidal.com/a")
        clipboard.assert_called_once_with(REDIRECT)


if __name__ == "__main__":
    unittest.main()
