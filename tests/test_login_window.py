import os
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from noneto.errors import AppError
from noneto.ui.login_window import (ClipboardUnavailable, WindowUnavailable, clipboard_login,
                                    window_login, _render_login, _linux_window_login, _read_linux_clipboard)
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
            self.assertEqual(_render_login("https://login.tidal.com/authorize", REDIRECT, "edgechromium"), REDIRECT + "?code=first")
        self.assertTrue(webview.window.destroyed)
        # Never the legacy MSHTML engine, and no cookies kept after the login.
        self.assertEqual(webview.starts, [{"gui": "edgechromium", "private_mode": True}])

    def test_lookalike_redirects_are_ignored(self):
        webview = fake_webview([REDIRECT + ".evil.test/?code=a", "https://evil.test/?u=" + REDIRECT + "?code=a",
                                REDIRECT + "x?code=a"])
        with patch.dict(sys.modules, {"webview": webview}):
            self.assertEqual(_render_login("https://login.tidal.com/authorize", REDIRECT, "edgechromium"), "")

    def test_closing_the_window_cancels(self):
        with patch.dict(sys.modules, {"webview": fake_webview()}):
            self.assertEqual(_render_login("https://login.tidal.com/authorize", REDIRECT, "edgechromium"), "")

    def test_missing_webview2_or_pywebview_is_reported_for_fallback(self):
        with patch.dict(sys.modules, {"webview": fake_webview(start_error=RuntimeError("no WebView2"))}):
            with self.assertRaises(WindowUnavailable):
                _render_login("https://login.tidal.com/authorize", REDIRECT, "edgechromium")
        with patch.dict(sys.modules, {"webview": None}):
            with self.assertRaises(WindowUnavailable):
                _render_login("https://login.tidal.com/authorize", REDIRECT, "edgechromium")

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

    def test_closing_login_cancels_without_opening_browser_or_reading_clipboard(self):
        with patch("noneto.ui.terminal.window_login", return_value=""), \
                patch("noneto.ui.terminal.webbrowser.open") as browser, \
                patch("noneto.ui.terminal.clipboard_login") as clipboard, patch("builtins.print"):
            self.assertEqual(Terminal().ask("redirect", url="https://login.tidal.com", redirect=REDIRECT), "")
        browser.assert_not_called()
        clipboard.assert_not_called()


class LinuxLoginTests(unittest.TestCase):
    def test_linux_uses_isolated_worker(self):
        with patch("noneto.ui.login_window.sys.platform", "linux"), \
                patch("noneto.ui.login_window._linux_window_login", return_value="captured") as worker:
            self.assertEqual(window_login("https://login.tidal.com", REDIRECT), "captured")
            worker.assert_called_once_with("https://login.tidal.com", REDIRECT)

    def test_worker_protocol_excludes_authorization_url_from_argv(self):
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = (json.dumps({"redirect": REDIRECT + "?code=test"}).encode(), None)
        with patch("noneto.ui.login_window.subprocess.Popen", return_value=process) as spawn:
            url = "https://login.tidal.com?challenge=TEST_ONLY"
            self.assertEqual(_linux_window_login(url, REDIRECT), REDIRECT + "?code=test")
        self.assertNotIn(url, spawn.call_args.args[0])
        self.assertEqual(json.loads(process.communicate.call_args.args[0])["url"], url)
        self.assertEqual(spawn.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_worker_cancellation_is_preserved_and_invalid_replies_fail_safely(self):
        for reply, accepted in (({"redirect": ""}, True), ({"redirect": "https://evil.test?code=x"}, False),
                                ({"redirect": 3}, False), ({"token": "fake"}, False)):
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (json.dumps(reply).encode(), None)
            with patch("noneto.ui.login_window.subprocess.Popen", return_value=process):
                if accepted:
                    self.assertEqual(_linux_window_login("https://login.tidal.com", REDIRECT), "")
                else:
                    with self.assertRaises(WindowUnavailable):
                        _linux_window_login("https://login.tidal.com", REDIRECT)

    def test_worker_startup_failure_allows_browser_fallback(self):
        with patch("noneto.ui.login_window.subprocess.Popen", side_effect=OSError("fake")):
            with self.assertRaises(WindowUnavailable):
                _linux_window_login("https://login.tidal.com", REDIRECT)

    def test_worker_timeout_stops_the_entire_gui_process_group(self):
        process = Mock(pid=123)
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired("worker", 600)
        with patch("noneto.ui.login_window.subprocess.Popen", return_value=process), \
                patch("noneto.ui.login_window.os.killpg", create=True) as kill:
            with self.assertRaises(WindowUnavailable):
                _linux_window_login("https://login.tidal.com", REDIRECT)
        kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=2)

    def test_wayland_clipboard_uses_wl_paste_with_timeout(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}), \
                patch("shutil.which", return_value="/usr/bin/wl-paste"), \
                patch("noneto.ui.login_window.subprocess.run", return_value=NS(returncode=0, stdout=b"test URL")) as run:
            self.assertEqual(_read_linux_clipboard(), "test URL")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/wl-paste", "--no-newline", "--type", "text/plain"])
            self.assertEqual(run.call_args.kwargs["timeout"], 1)

    def test_x11_clipboard_and_absent_clipboard_are_handled(self):
        with patch.dict(os.environ, {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}), \
                patch("shutil.which", side_effect=lambda name: "/usr/bin/xclip" if name == "xclip" else None), \
                patch("noneto.ui.login_window.subprocess.run", return_value=NS(returncode=0, stdout=b"test URL")) as run:
            self.assertEqual(_read_linux_clipboard(), "test URL")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/xclip", "-selection", "clipboard", "-out"])
        with patch("shutil.which", return_value=None):
            with self.assertRaises(ClipboardUnavailable):
                _read_linux_clipboard()


@unittest.skipUnless(sys.platform == "linux" and hasattr(os, "geteuid") and os.geteuid() != 0
                     and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                     "Linux Qt WebEngine integration requires an unprivileged desktop/Xvfb session")
class NativeLinuxLoginTests(unittest.TestCase):
    def test_real_window_captures_main_redirect_without_persistent_cookies(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls.append((self.path, self.headers.get("Cookie", "")))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if self.path.startswith("/callback"):
                    self.wfile.write(b"iframe callback; must not authorize the main window")
                    return
                self.wfile.write(b"""<html><body>Local login test
                    <script>document.cookie='noneto_login_test=1';</script>
                    <iframe src='/callback?code=iframe'></iframe>
                    <script>setTimeout(()=>location.href='/callback?code=main',500);</script>
                    </body></html>""")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            # Bound failure time; all URLs/codes in this test belong to the local fake server.
            with patch("noneto.ui.login_window.CLIPBOARD_TIMEOUT", 20):
                for _attempt in range(2):
                    self.assertEqual(window_login(url + "/login", url + "/callback"), url + "/callback?code=main")
            self.assertEqual([cookie for path, cookie in calls if path == "/login"], ["", ""])
            self.assertFalse(any(path == "/callback?code=main" for path, _cookie in calls))
            self.assertTrue(any(path == "/callback?code=iframe" for path, _cookie in calls))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
