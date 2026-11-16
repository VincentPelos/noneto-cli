"""Offline checks of the actual frozen application, using only disposable local data."""
import asyncio
import json
from pathlib import Path
import tempfile


def check(login=False):
    import certifi
    import numpy
    import requests
    import tidalapi
    import textual_image.widget
    import winrt.windows.foundation
    import winrt.windows.media
    import winrt.windows.media.interop
    import winrt.windows.storage.streams
    from noneto.ui.tui import NonetoApp
    from noneto.auth.storage import protect
    from textual.app import App
    from textual.widgets import Label
    from PIL import Image
    import io
    # Exercise native DLL loading, certificates, image decoders, and Textual resources.
    assert Path(certifi.where()).is_file()
    assert numpy.fft.rfft(numpy.zeros(64)).shape == (33,)
    assert protect(protect(b"noneto-packaging-check"), decrypt=True) == b"noneto-packaging-check"
    image = io.BytesIO()
    Image.new("RGB", (2, 2)).save(image, format="JPEG")
    Image.open(io.BytesIO(image.getvalue())).load()

    class Probe(App):
        def compose(self):
            yield Label("Noneto packaging check")

    async def tui():
        async with Probe().run_test() as pilot:
            await pilot.pause()

    asyncio.run(tui())
    # Importing these loads Python.NET, the native CLR loader, and the WebView2 bindings.
    import webview.platforms.winforms
    import webview.platforms.edgechromium
    if login:
        _login_check()
    print(json.dumps({"bundle": "ok", "login": "ok" if login else "not-run"}))
    return 0


def _login_check():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import webview
    from noneto.ui.login_window import _render_login

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302 if self.path == "/" else 200)
            if self.path == "/":
                self.send_header("Location", "/callback?code=local-packaging-check")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{server.server_port}"

    def close():
        for window in webview.windows:
            window.destroy()

    timer = threading.Timer(20, close)
    timer.daemon = True
    timer.start()
    try:
        assert _render_login(root + "/", root + "/callback", "edgechromium") == (
            root + "/callback?code=local-packaging-check")
    finally:
        timer.cancel()
        server.shutdown()
        server.server_close()


def check_player():
    import queue
    import time
    import wave
    from noneto.audio.player import Player
    with tempfile.TemporaryDirectory(prefix="noneto-local-audio-") as directory:
        audio = Path(directory) / "silence.wav"
        with wave.open(str(audio), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\0\0" * 8000)
        with Player(extra_args=["--ao=null"], media_controls=False) as player:
            player.play(audio)
            deadline = time.monotonic() + 10
            loaded = False
            while time.monotonic() < deadline:
                try:
                    event = player.events.get(timeout=0.2)
                except queue.Empty:
                    continue
                assert event["event"] != "command-error"
                if event["event"] == "file-loaded":
                    loaded = True
                    assert player.command("get_property", "tls-verify") is True
                if event["event"] == "end-file" and event.get("reason") == "eof":
                    assert loaded
                    break
            else:
                raise RuntimeError("Bundled mpv did not finish local audio playback")
        assert player.process.poll() is not None
    print(json.dumps({"mpv": "ok", "audio": "local-null-output", "tls": True}))
    return 0
