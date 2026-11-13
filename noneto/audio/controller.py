"""Nonblocking playback queue. Receives a future-returning stream resolver."""
from dataclasses import dataclass, replace
import queue
import random
import threading
import time

from ..diagnostics import emit, failure, track_id
from ..errors import AppError, error_label
from ..i18n import _
from ..state.writer import QueueWriter
from .player import Player
from .inspector import describe_audio
import os
import sys


def process_tap(pid):
    if os.name == "nt":
        from .tap import ProcessTap as WindowsTap
        return WindowsTap(pid)
    if sys.platform.startswith("linux"):
        from .pulse_tap import PulseTap
        return PulseTap(pid)
    return None


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


class PlaybackController:
    def __init__(self, resolve, player_factory=Player, tap_factory=process_tap, queue_store=None):
        self.resolve = resolve
        self.player_factory = player_factory
        self.tap_factory = tap_factory
        self._tap = None
        self._spectrum = None
        self.queue_store = queue_store
        self._restored = queue_store.load() if queue_store is not None else None
        self._state = PlaybackState()
        if self._restored:
            saved = self._restored
            self._state = PlaybackState(tracks=tuple(saved["tracks"][i] for i in saved["order"]),
                                        index=saved["index"], position=saved["position"],
                                        volume=saved["volume"], shuffle=saved["shuffle"],
                                        repeat=saved["repeat"], paused=saved["index"] >= 0,
                                        status="Paused" if saved["index"] >= 0 else "Stopped")
        self._lock = threading.Lock()
        self._commands = queue.Queue(maxsize=128)
        self._wake = threading.Event()
        self._writer = (QueueWriter(queue_store, lambda error: failure("queue.save_failed", error))
                        if queue_store is not None else None)
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
            self._wake.set()
        except queue.Full:
            raise AppError(_("Too many pending actions. Wait a moment.")) from None

    def close(self):
        self._closing.set()
        self._wake.set()
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
            capture = self.tap_factory(pid)
            if capture is not None:
                self._tap = capture.start()

    def _run(self):
        player = None
        future = None
        loading = None
        started = False
        last_poll = 0
        source_tracks = self._restored["tracks"] if self._restored else ()
        queue_order = tuple(self._restored["order"]) if self._restored else ()
        resume_position = self._state.position if self._restored else 0
        seek_on_load = 0
        prepared = None  # (index, track object, future, monotonic time, staged in mpv)
        last_save = time.monotonic()
        prefetch_blocked = None

        def persist():
            nonlocal last_save
            if self._writer is not None:
                self._writer.submit(self.snapshot(), source_tracks, queue_order)
            last_save = time.monotonic()

        def discard_prepared(clear=True):
            nonlocal prepared, prefetch_blocked
            if prepared is not None:
                prepared[2].cancel()
                if clear and prepared[4] and player is not None and hasattr(player, "clear_next"):
                    player.clear_next()
            prepared = None
            prefetch_blocked = None

        def predicted_next():
            state = self.snapshot()
            if state.index < 0:
                return -1
            if state.repeat == "one":
                return state.index
            if state.index + 1 < len(state.tracks):
                return state.index + 1
            if state.repeat == "all" and not state.shuffle and state.tracks:
                return 0
            return -1  # A shuffled cycle is selected on EOF, preserving duplicate rules.

        def prefetch():
            nonlocal prepared, prefetch_blocked
            state = self.snapshot()
            index = predicted_next()
            if index < 0 or state.status != "Playing" or state.paused:
                return
            # Resolve close to EOF so signed targets are not held through an entire long song.
            if state.duration <= 0 or state.duration - state.position > 30:
                return
            track = state.tracks[index]
            if prepared is not None and time.monotonic() - prepared[3] > 90:
                discard_prepared()
            if prepared is None and prefetch_blocked is not track:
                try:
                    prepared = (index, track, self.resolve(track), time.monotonic(), False)
                    prepared[2].add_done_callback(lambda _future: self._wake.set())
                except Exception as error:
                    prefetch_blocked = track
                    failure("playback.prefetch_failed", error)
                    return
            if prepared is not None and prepared[2].done() and not prepared[4]:
                try:
                    target = prepared[2].result()
                    if hasattr(player, "stage"):
                        player.stage(target)
                    prepared = (*prepared[:4], True)
                except Exception as error:
                    discard_prepared()
                    prefetch_blocked = track
                    failure("playback.prefetch_failed", error)

        visualizer = False

        def set_order(order):
            nonlocal queue_order
            queue_order = tuple(order)
            self._set(tracks=tuple(source_tracks[index] for index in order))

        def select(index):
            nonlocal future, loading, started, prepared, resume_position, seek_on_load
            if future is not None:
                future.cancel()
            future = None
            cached = prepared
            usable = (cached is not None and cached[0] == index and 0 <= index < len(self.snapshot().tracks)
                      and cached[1] is self.snapshot().tracks[index]
                      and time.monotonic() - cached[3] <= 90
                      and not cached[2].cancelled()
                      and (not cached[2].done() or cached[2].exception() is None))
            if usable:
                prepared = None
                if cached[4] and hasattr(player, "clear_next"):
                    player.clear_next()
            else:
                discard_prepared()
            resume_position, seek_on_load = 0, 0
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
                future = cached[2] if usable else self.resolve(state.tracks[index])
                future.add_done_callback(lambda _future: self._wake.set())
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
            nonlocal prepared, loading, started
            state = self.snapshot()
            next_index = predicted_next()
            if (ended and prepared is not None and prepared[4] and hasattr(player, "stage")
                    and prepared[0] == next_index and prepared[1] is state.tracks[next_index]):
                prepared = None
                self._set(index=next_index, status="Loading", position=0, duration=0,
                          paused=False, error="", audio=())
                loading, started = time.monotonic(), False
                return  # mpv continues into the prefetched playlist entry without stop/loadfile.

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
                pending_event = player is not None and not player.events.empty()
                if self._commands.empty() and not pending_event:
                    # Real players notify IPC events; legacy/test players retain bounded polling.
                    interval = .5 if player is not None and callable(getattr(player, "set_wakeup", None)) else .05
                    self._wake.wait(interval if player is not None or future is not None else 5)
                self._wake.clear()
                if self._closing.is_set():
                    break
                try:
                    action, value = self._commands.get_nowait()
                except queue.Empty:
                    action, value = None, None
                try:
                    state = self.snapshot()
                    if action == "play":
                        discard_prepared()
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
                        discard_prepared()
                        source_tracks += (value,)
                        set_order((*queue_order, len(source_tracks) - 1))
                    elif action == "next":
                        advance()
                    elif action == "previous":
                        select(max(0, state.index - 1))
                    elif action == "stop":
                        select(-1)
                    elif action == "shuffle":
                        discard_prepared()
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
                        discard_prepared()
                        self._set(repeat={"off": "all", "all": "one", "one": "off"}[state.repeat])
                    elif action in ("pause", "set_pause") and player is None and state.index >= 0:
                        if action == "pause" or not value:
                            position = resume_position
                            select(state.index)
                            seek_on_load = position
                    elif action in ("pause", "set_pause") and player and state.status in ("Playing", "Paused"):
                        paused = not state.paused if action == "pause" else bool(value)
                        if paused != state.paused:
                            player.command("set_property", "pause", paused)
                            if paused:
                                discard_prepared()
                            self._set(paused=paused, status="Paused" if paused else "Playing")
                    elif action == "volume":
                        volume = max(0, min(100, state.volume + value))
                        if player:
                            player.command("set_property", "volume", volume)
                        self._set(volume=volume)
                    elif action == "seek" and player and state.status in ("Playing", "Paused"):
                        discard_prepared()
                        player.command("seek", value, "relative")
                    elif (action == "seek_absolute" and player
                          and state.status in ("Playing", "Paused") and state.duration > 0):
                        discard_prepared()
                        target = max(0.0, min(state.duration, float(value)))
                        player.command("seek", target, "absolute")
                        self._set(position=target)

                    if future is not None and future.done():
                        pending, future = future, None
                        target = pending.result()
                        if player is None:
                            player = self.player_factory()
                            if callable(getattr(player, "set_wakeup", None)):
                                player.set_wakeup(self._wake.set)
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
                                if seek_on_load:
                                    player.command("seek", seek_on_load, "absolute")
                                    self._set(position=seek_on_load)
                                    seek_on_load = 0
                                emit("playback.loaded", track_id=track_id(self.snapshot().tracks[self.snapshot().index]))
                                read_audio()
                            elif kind == "end-file" and started and event.get("reason") == "eof":
                                advance(ended=True)
                                persist()
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
                    if player:
                        prefetch()
                    if action in {"play", "enqueue", "next", "previous", "stop", "shuffle", "repeat", "volume"} or time.monotonic() - last_save >= 5:
                        persist()
                except Exception as error:
                    failure("playback.failed", error)
                    discard_prepared(clear=False)
                    if future is not None:
                        future.cancel()
                    future, loading, started = None, None, False
                    self._set(status="Error", error=str(error) if isinstance(error, AppError) else error_label(error))
                    if player:
                        player.close()
                        player = None
                    self._tap_player(None, False)
        finally:
            persist()
            discard_prepared(clear=False)
            self._tap_player(None, False)
            if future is not None:
                future.cancel()
            try:
                if player:
                    player.close()
            finally:
                if self._writer is not None:
                    self._writer.close()
