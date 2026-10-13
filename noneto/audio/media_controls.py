"""Mirror playback into the system media session without blocking UI or audio."""
import math
import threading
import time

from ..diagnostics import emit, failure
from ..security import terminal_text
from .windows_media import WindowsMedia


class MediaControls:
    def __init__(self, playback, artwork, backend_factory=WindowsMedia):
        self.playback = playback
        self.artwork = artwork  # returns a future; SDK access stays serialized by Library
        self.backend_factory = backend_factory
        self.available = False
        self._ready = threading.Event()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="noneto-media", daemon=True)
        self._thread.start()
        if not self._ready.wait(3):
            self._closing.set()

    def _button(self, name):
        if self._closing.is_set():
            return
        try:
            if name in ("PLAY", "PAUSE"):
                self.playback.send("set_pause", name == "PAUSE")
            elif name in ("NEXT", "PREVIOUS", "STOP"):
                self.playback.send(name.lower())
        except Exception as error:
            failure("media.command_failed", error)

    def _seek(self, seconds):
        if not self._closing.is_set() and math.isfinite(seconds):
            try:
                self.playback.send("seek_absolute", seconds)
            except Exception as error:
                failure("media.command_failed", error)

    def _run(self):
        backend = None
        track_key = object()
        cover = None
        last_update = 0
        try:
            backend = self.backend_factory(self._button, self._seek)
            self.available = True
            self._ready.set()
            emit("media.started")
            while not self._closing.is_set():
                backend.pump()
                state = self.playback.snapshot()
                track = state.tracks[state.index] if 0 <= state.index < len(state.tracks) else None
                key = getattr(track, "id", None)
                if key != track_key:
                    track_key = key
                    if cover is not None:
                        cover.cancel()
                    cover = None
                    text = lambda value: terminal_text(str(value or ""))[:300]
                    backend.metadata(text(getattr(track, "name", "")),
                                     text(getattr(getattr(track, "artist", None), "name", "")),
                                     text(getattr(getattr(track, "album", None), "name", "")))
                    if track is not None:
                        cover = self.artwork(track)
                    last_update = 0
                if cover is not None and cover.done():
                    pending, cover = cover, None
                    try:
                        data = pending.result()
                        if data:
                            backend.thumbnail(data)
                    except Exception as error:
                        failure("media.artwork_unavailable", error)
                now = time.monotonic()
                if now - last_update >= 0.25:
                    duration = max(0, state.duration or getattr(track, "duration", 0) or 0)
                    position = min(duration, max(0, state.position))
                    status = {"Playing": "playing", "Paused": "paused",
                              "Loading": "loading"}.get(state.status, "stopped")
                    backend.update(status, position, duration, state.index > 0 or position > 3,
                                   state.index + 1 < len(state.tracks) or state.repeat == "all")
                    last_update = now
                self._closing.wait(0.05)
        except Exception as error:
            failure("media.unavailable", error)
        finally:
            self.available = False
            self._ready.set()
            if cover is not None:
                cover.cancel()
            if backend is not None:
                try:
                    backend.close()
                except Exception as error:
                    failure("media.close_failed", error)
            if not self._closing.is_set():
                try:
                    self.playback.send("media_controls", True)
                except Exception as error:
                    failure("media.fallback_failed", error)

    def close(self):
        self._closing.set()
        self._thread.join(timeout=5)
