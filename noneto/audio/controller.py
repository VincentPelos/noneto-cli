"""Nonblocking playback queue. Receives a future-returning stream resolver."""
from dataclasses import dataclass, replace
import queue
import random
import threading
import time

from ..diagnostics import emit, failure, track_id
from ..errors import AppError, error_label
from ..i18n import _
from .player import Player
from .tap import ProcessTap


@dataclass(frozen=True)
class PlaybackState:
    tracks: tuple = ()
    index: int = -1
    status: str = "Not playing"
    position: float = 0
    duration: float = 0
    volume: int = 60
    paused: bool = False
    shuffle: bool = False
    repeat: str = "off"
    error: str = ""
    audio: tuple = ()  # (short, detail, tier) of what mpv decodes, empty until loaded


LOSSY_CODECS = {"aac", "mp3", "opus", "vorbis", "ac3", "eac3"}


def describe_audio(codec, params):
    """Label the decoded stream and rank it as a TIDAL tier (None when it maps to none).

    TIDAL FLAC is 16-bit (decoded as s16) or 24-bit (decoded as s32).
    """
    rate = params.get("samplerate") if isinstance(params, dict) else None
    if not isinstance(codec, str) or not isinstance(rate, int) or rate <= 0:
        return ()
    khz = f"{rate / 1000:g}"
    if codec == "flac":
        bits = {"s16": 16, "s16p": 16, "s32": 24, "s32p": 24}.get(params.get("format"))
        if bits:
            tier = "HI_RES_LOSSLESS" if bits > 16 or rate > 48000 else "LOSSLESS"
            return (f"FLAC {bits}/{khz}", f"FLAC {bits}-bit / {khz} kHz", tier)
        return (f"FLAC {khz}", f"FLAC / {khz} kHz", "LOSSLESS")
    name = codec.upper()
    return (f"{name} {khz}", f"{name} / {khz} kHz", "HIGH" if codec in LOSSY_CODECS else None)


class PlaybackController:
    def __init__(self, resolve, player_factory=Player, tap_factory=ProcessTap):
        self.resolve = resolve
        self.player_factory = player_factory
        self.tap_factory = tap_factory
        self._tap = None
        self._spectrum = None
        self._state = PlaybackState()
        self._lock = threading.Lock()
        self._commands = queue.Queue(maxsize=128)
        self._random = random.Random()
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, name="noneto-audio", daemon=True)
        self._thread.start()

    def snapshot(self):
        with self._lock:
            return self._state

    def _set(self, **values):
        with self._lock:
            self._state = replace(self._state, **values)

    def send(self, action, value=None):
        try:
            self._commands.put_nowait((action, value))
        except queue.Full:
            raise AppError(_("Too many pending actions. Wait a moment.")) from None

    def close(self):
        self._closing.set()
        self._thread.join(timeout=20)

    def bands(self, count):
        """Spectrum of what mpv is playing right now, or None when there is no capture."""
        tap = self._tap
        if tap is None or tap.failed.is_set():
            return None
        if self._spectrum is None:
            from .spectrum import Spectrum
            self._spectrum = Spectrum(tap.rate)
        return self._spectrum.bands(tap.samples(), count)

    def _tap_player(self, player, wanted):
        """Capture only while the visualizer is visible and mpv exists; follow mpv restarts."""
        tap = self._tap
        pid = player.process.pid if wanted and player is not None else None
        if tap is not None and tap.pid != pid:
            self._tap = None
            tap.stop()
        if pid is not None and self._tap is None:
            self._tap = self.tap_factory(pid).start()

    def _run(self):
        player = None
        future = None
        loading = None
        started = False
        last_poll = 0
        source_tracks = ()
        queue_order = []
        visualizer = False

        def set_order(order):
            nonlocal queue_order
            queue_order = order
            self._set(tracks=tuple(source_tracks[index] for index in order))

        def select(index):
            nonlocal future, loading, started
            if future is not None:
                future.cancel()
            future = None
            loading, started = None, False
            if player is not None:
                player.command("stop")
                while not player.events.empty():
                    try:
                        player.events.get_nowait()
                    except queue.Empty:
                        break
            state = self.snapshot()
            if 0 <= index < len(state.tracks):
                self._set(index=index, status="Loading", position=0, duration=0,
                          paused=False, error="", audio=())
                emit("track.selected", track_id=track_id(state.tracks[index]), index=index,
                     count=len(state.tracks))
                future = self.resolve(state.tracks[index])
            else:
                self._set(index=-1, status="Stopped", position=0, duration=0, paused=False, audio=())

        def read_audio():
            # audio-params exists only once the decoder starts, usually after file-loaded: retried on each poll.
            audio = describe_audio(player.command("get_property", "audio-codec-name", allow_unavailable=True),
                                   player.command("get_property", "audio-params", allow_unavailable=True))
            if audio:
                state = self.snapshot()
                self._set(audio=audio)
                emit("playback.audio", track_id=track_id(state.tracks[state.index]), quality=audio[1])

        def advance(ended=False):
            state = self.snapshot()
            if ended and state.repeat == "one" and state.index >= 0:
                select(state.index)
            elif state.index + 1 < len(state.tracks):
                select(state.index + 1)
            elif state.repeat == "all" and state.tracks:
                if state.shuffle:
                    order = list(queue_order)
                    self._random.shuffle(order)
                    last = source_tracks[queue_order[state.index]]
                    last_id = getattr(last, "id", None)

                    def same_song(index):
                        track = source_tracks[index]
                        return track is last or (last_id is not None and getattr(track, "id", None) == last_id)

                    if same_song(order[0]):
                        alternative = next((i for i, index in enumerate(order) if not same_song(index)), None)
                        if alternative is not None:
                            order[0], order[alternative] = order[alternative], order[0]
                    set_order(order)
                select(0)
            else:
                select(-1)

        try:
            while not self._closing.is_set():
                try:
                    action, value = self._commands.get(timeout=0.05)
                except queue.Empty:
                    action, value = None, None
                try:
                    state = self.snapshot()
                    if action == "play":
                        tracks, index = value
                        source_tracks = tuple(tracks)
                        queue_order = list(range(len(source_tracks)))
                        if state.shuffle:
                            suffix = queue_order[index + 1:]
                            self._random.shuffle(suffix)
                            queue_order[index + 1:] = suffix
                        set_order(queue_order)
                        select(index)
                    elif action == "enqueue":
                        source_tracks += (value,)
                        set_order(queue_order + [len(source_tracks) - 1])
                    elif action == "next":
                        advance()
                    elif action == "previous":
                        select(max(0, state.index - 1))
                    elif action == "stop":
                        select(-1)
                    elif action == "shuffle":
                        order = list(queue_order)
                        start = max(0, state.index + 1)
                        suffix = order[start:]
                        if state.shuffle:
                            suffix.sort()
                        else:
                            self._random.shuffle(suffix)
                        set_order(order[:start] + suffix)
                        self._set(shuffle=not state.shuffle)
                    elif action == "visualizer":
                        visualizer = bool(value)
                    elif action == "media_controls" and player:
                        player.command("set_property", "media-controls", bool(value))
                    elif action == "repeat":
                        self._set(repeat={"off": "all", "all": "one", "one": "off"}[state.repeat])
                    elif action in ("pause", "set_pause") and player and state.status in ("Playing", "Paused"):
                        paused = not state.paused if action == "pause" else bool(value)
                        if paused != state.paused:
                            player.command("set_property", "pause", paused)
                            self._set(paused=paused, status="Paused" if paused else "Playing")
                    elif action == "volume":
                        volume = max(0, min(100, state.volume + value))
                        if player:
                            player.command("set_property", "volume", volume)
                        self._set(volume=volume)
                    elif action == "seek" and player and state.status in ("Playing", "Paused"):
                        player.command("seek", value, "relative")
                    elif (action == "seek_absolute" and player
                          and state.status in ("Playing", "Paused") and state.duration > 0):
                        target = max(0.0, min(state.duration, float(value)))
                        player.command("seek", target, "absolute")
                        self._set(position=target)

                    if future is not None and future.done():
                        pending, future = future, None
                        target = pending.result()
                        if player is None:
                            player = self.player_factory()
                            player.command("set_property", "volume", self.snapshot().volume)
                        player.play(target)
                        loading, started = time.monotonic(), False
                    self._tap_player(player, visualizer)
                    if player:
                        if player.failed.is_set() or player.process.poll() is not None:
                            raise AppError(_("Lost communication with mpv. Try playing again."))
                        while True:
                            try:
                                event = player.events.get_nowait()
                            except queue.Empty:
                                break
                            kind = event.get("event")
                            if kind == "start-file":
                                started = True
                            elif kind == "file-loaded" and started:
                                loading = None
                                self._set(status="Playing")
                                emit("playback.loaded", track_id=track_id(self.snapshot().tracks[self.snapshot().index]))
                                read_audio()
                            elif kind == "end-file" and started and event.get("reason") == "eof":
                                advance(ended=True)
                                break
                            elif kind == "end-file" and started and event.get("reason") == "error":
                                raise AppError(_("Could not load the audio. Try again or pick another song."))
                        if loading is not None and time.monotonic() - loading > 45:
                            raise AppError(_("The audio did not load within 45 seconds. Try again."))
                        if (self.snapshot().status in ("Playing", "Paused")
                                and time.monotonic() - last_poll > 0.5):
                            last_poll = time.monotonic()
                            self._set(position=player.command("get_property", "time-pos", allow_unavailable=True) or 0,
                                      duration=player.command("get_property", "duration", allow_unavailable=True) or 0)
                            if not self.snapshot().audio:
                                read_audio()
                except Exception as error:
                    failure("playback.failed", error)
                    if future is not None:
                        future.cancel()
                    future, loading, started = None, None, False
                    self._set(status="Error", error=str(error) if isinstance(error, AppError) else error_label(error))
                    if player:
                        player.close()
                        player = None
                    self._tap_player(None, False)
        finally:
            self._tap_player(None, False)
            if future is not None:
                future.cancel()
            if player:
                player.close()
