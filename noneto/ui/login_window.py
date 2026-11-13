"""Automatic TIDAL login: WebView2 on Windows, isolated Qt WebEngine on Linux.

TIDAL redirects to a fixed page (no localhost callback is possible), so the redirect URL that
carries the code is captured here and handed to auth/service.py, which validates it.
"""
import ctypes
from ctypes import wintypes
import logging
import json
import os
import signal
import subprocess
import sys
import threading
import time

from ..errors import AppError
from ..i18n import _, language, set_language

CLIPBOARD_TIMEOUT = 600
CF_UNICODETEXT = 13


class WindowUnavailable(Exception):
    """The web renderer is unavailable; use the system browser."""


class ClipboardUnavailable(Exception):
    """No supported clipboard command is available."""


def window_login(url, redirect):
    if sys.platform == "linux":
        return _linux_window_login(url, redirect)
    return _render_login(url, redirect, "edgechromium")


def _linux_window_login(url, redirect):
    # URLs/codes never enter the process command line. Qt diagnostics cannot reach the terminal.
    try:
        process = subprocess.Popen([sys.executable, "-m", "noneto.ui.login_window", "--worker"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
    except OSError:
        raise WindowUnavailable from None
    try:
        output, _stderr = process.communicate(json.dumps({"url": url, "redirect": redirect,
                                                        "language": language()}).encode(),
                                              timeout=CLIPBOARD_TIMEOUT)
        if process.returncode != 0 or len(output) > 32768:
            raise WindowUnavailable
        result = json.loads(output)
        if not isinstance(result, dict) or set(result) != {"redirect"} or not isinstance(result["redirect"], str):
            raise WindowUnavailable
        target = result["redirect"]
        if target and (len(target) > 16384 or not target.startswith(redirect + "?")):
            raise WindowUnavailable
        return target  # Empty means the user closed the window: cancel, without opening a browser.
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise WindowUnavailable from None
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
        for pipe in (process.stdin, process.stdout):
            if pipe is not None and not pipe.closed:
                try:
                    pipe.close()
                except OSError:
                    pass


def _render_login(url, redirect, renderer):
    """Show the login page; return the redirect URL it reaches, or "" if the user closes the window."""
    try:
        import webview
    except ImportError:
        raise WindowUnavailable from None
    log = logging.getLogger("pywebview")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    log.setLevel(logging.CRITICAL + 1)  # its logs would include URLs with the code
    captured = []
    lock = threading.Lock()
    try:
        window = webview.create_window(_("Sign in to TIDAL - Noneto CLI"), url, width=500, height=780)
    except Exception:
        raise WindowUnavailable from None

    def on_request(request):
        target = str(getattr(request, "url", ""))
        if len(target) > 16384 or not target.startswith(redirect + "?"):
            return
        with lock:
            if captured:
                return
            captured.append(target)
        window.destroy()

    window.events.request_sent += on_request
    try:
        # Private mode keeps no persistent login cookies. TLS validation stays enabled.
        webview.start(gui=renderer, private_mode=True)
    except Exception:
        if not captured:
            raise WindowUnavailable from None
    return captured[0] if captured else ""


def read_clipboard():
    if sys.platform == "linux":
        return _read_linux_clipboard()
    return _read_windows_clipboard()


def _read_linux_clipboard():
    import shutil
    choices = []
    if os.environ.get("WAYLAND_DISPLAY"):
        choices.append(["wl-paste", "--no-newline", "--type", "text/plain"])
    if os.environ.get("DISPLAY"):
        choices.extend((["xclip", "-selection", "clipboard", "-out"],
                        ["xsel", "--clipboard", "--output"]))
    for command in choices:
        executable = shutil.which(command[0])
        if executable is None:
            continue
        try:
            result = subprocess.run([executable, *command[1:]], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=1)
            if result.returncode == 0:
                return result.stdout[:16384].decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            continue
    raise ClipboardUnavailable


def _read_windows_clipboard():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)[:16384]
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_login(redirect, read=read_clipboard, interval=0.5, timeout=CLIPBOARD_TIMEOUT):
    """Wait until the user copies the redirect URL; text copied before the login started is ignored."""
    before = read()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(interval)
        text = read().strip()
        if text != before.strip() and text.startswith(redirect + "?"):
            return text
    raise AppError(_("The sign-in address did not arrive in time. Run noneto again."))


def _worker():
    try:
        payload = json.loads(sys.stdin.buffer.read(32769))
        if (not isinstance(payload, dict) or set(payload) != {"url", "redirect", "language"}
                or any(not isinstance(value, str) or len(value) > 16384 for value in payload.values())):
            return 2
        set_language(payload["language"])
        # The parent authenticated UI boundary supplies these values; auth validates the returned code.
        from .linux_login import render_login
        target = render_login(payload["url"], payload["redirect"])
        sys.stdout.write(json.dumps({"redirect": target}))
        return 0
    except Exception:
        return 2  # No exception messages: renderer failures can contain an authorization URL.


if __name__ == "__main__":
    raise SystemExit(_worker() if sys.argv[1:] == ["--worker"] else 2)
