"""TIDAL login without copying anything: a WebView2 window, or the clipboard as fallback.

TIDAL redirects to a fixed page (no localhost callback is possible), so the redirect URL that
carries the code is captured here and handed to auth/service.py, which validates it.
"""
import ctypes
from ctypes import wintypes
import logging
import threading
import time

from ..errors import AppError
from ..i18n import _

CLIPBOARD_TIMEOUT = 600
CF_UNICODETEXT = 13


class WindowUnavailable(Exception):
    """WebView2 (or pywebview) is missing; the caller falls back to the system browser."""


def window_login(url, redirect):
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
    window = webview.create_window(_("Iniciar sesion en TIDAL - Noneto CLI"), url, width=500, height=780)

    def on_request(request):
        target = str(getattr(request, "url", ""))
        if not target.startswith(redirect + "?"):
            return
        with lock:
            if captured:
                return
            captured.append(target)
        window.destroy()

    window.events.request_sent += on_request
    try:
        # Only Edge WebView2: never fall back to the legacy MSHTML engine. Private mode keeps no cookies.
        webview.start(gui="edgechromium", private_mode=True)
    except Exception:
        if not captured:
            raise WindowUnavailable from None
    return captured[0] if captured else ""


def read_clipboard():
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
    raise AppError(_("No se recibio la direccion de inicio de sesion a tiempo. Ejecuta noneto otra vez."))
