"""Player bar: mini cover, current song, transport controls, volume and clickable progress.

The bar handles its own buttons and clicks and sends the orders through the app's `control()`.
"""
from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, ProgressBar, Static

from ..i18n import _
from .common import Changes, album, artist, clean, duration

CSS = """
#player { height: 5; background: $panel; padding: 1 1; }
#player-body { width: 1fr; }
#now-row, #now-detail, #seek { height: 1; }
#now-detail { color: $text-muted; }
#now-playing { width: 1fr; height: 1; }
#controls { width: auto; height: 1; }
#controls Button { min-width: 4; margin: 0 1; background: $panel; }
#controls Button.-primary { min-width: 6; background: $primary; }
#shuffle, #repeat { color: $text-muted; }
#controls Button.active { color: $foreground; text-style: bold; }
#volume { width: 1fr; height: 1; align-horizontal: right; }
#volume Button { min-width: 3; margin: 0; background: $panel; color: $text-muted; }
#volume-bar { width: 10; }
#volume-label { width: 5; text-align: right; }
#elapsed, #total { width: 6; color: $text-muted; }
#total { text-align: right; }
#progress { width: 1fr; height: 1; }
#progress > #bar { width: 1fr; }
#progress:hover { background: $surface-lighten-1; }
#progress > .bar--bar, #progress > .bar--complete { color: $primary; }
"""

VOLUME_CELLS = 10
REPEAT_LABELS = {"off": _("off"), "all": _("queue"), "one": _("one")}
CONTROLS = (("shuffle", "⇄", _("Shuffle")), ("previous", "◁◁", _("Previous")),
            ("pause", "▷", _("Play or pause")), ("next", "▷▷", _("Next")),
            ("repeat", "↻", _("Repeat: off / queue / song")))


def level_bar(volume):
    filled = round(max(0, min(100, volume)) / 100 * VOLUME_CELLS)
    output = Text("━" * filled, style="bold")
    output.append("─" * (VOLUME_CELLS - filled), style="dim")
    return output


def playback_mode(state):
    """Status bar text: playback status, repeat mode and shuffle."""
    mode = f"{_(state.status)} · {_("Repeat: {mode}", mode=REPEAT_LABELS[state.repeat])}"
    return mode + (" · " + _("Shuffle") if state.shuffle else "")


class SeekProgress(ProgressBar):
    class SeekRequested(Message):
        def __init__(self, fraction):
            super().__init__()
            self.fraction = fraction

    def on_click(self, event):
        if event.button != 1:
            return
        bar = self.query_one("#bar")
        region = bar.content_region
        if region.width:
            fraction = (event.screen_x - region.x) / max(1, region.width - 1)
            self.post_message(self.SeekRequested(max(0.0, min(1.0, fraction))))
            event.stop()


class VolumeBar(Static):
    """Click a cell to jump to that volume, in steps of 10%."""

    class VolumeRequested(Message):
        def __init__(self, volume):
            super().__init__()
            self.volume = volume

    def on_click(self, event):
        if event.button == 1 and self.content_region.width:
            cell = event.screen_x - self.content_region.x
            self.post_message(self.VolumeRequested(max(0, min(VOLUME_CELLS, cell + 1)) * 100 // VOLUME_CELLS))
            event.stop()


class PlayerBar(Horizontal):
    def __init__(self, cover, **kwargs):
        super().__init__(**kwargs)
        self._cover = cover
        self._changes = Changes()

    def compose(self):
        yield self._cover
        with Vertical(id="player-body"):
            with Horizontal(id="now-row"):
                yield Static(_("No song"), id="now-playing", markup=False)
                with Horizontal(id="controls"):
                    for identifier, label, tooltip in CONTROLS:
                        button = Button(label, id=identifier,
                                        variant="primary" if identifier == "pause" else "default")
                        button.tooltip = tooltip
                        yield button
                with Horizontal(id="volume"):
                    yield Button("−", id="quieter")
                    yield VolumeBar(level_bar(60), id="volume-bar")
                    yield Button("+", id="louder")
                    yield Static("60%", id="volume-label", markup=False)
            yield Static("", id="now-detail", markup=False)
            with Horizontal(id="seek"):
                yield Static("0:00", id="elapsed", markup=False)
                yield SeekProgress(total=1, show_percentage=False, show_eta=False, id="progress")
                yield Static("0:00", id="total", markup=False)

    def on_mount(self):
        self._now_playing = self.query_one("#now-playing", Static)
        self._now_detail = self.query_one("#now-detail", Static)
        self._elapsed = self.query_one("#elapsed", Static)
        self._total = self.query_one("#total", Static)
        self._progress = self.query_one("#progress", ProgressBar)
        self._volume_bar = self.query_one("#volume-bar", Static)
        self._volume_label = self.query_one("#volume-label", Static)
        self._pause_button = self.query_one("#pause", Button)
        self._shuffle_button = self.query_one("#shuffle", Button)
        self._repeat_button = self.query_one("#repeat", Button)

    def show(self, state, track, source):
        changes = self._changes
        now = Text()
        detail = ""
        if track:
            now.append(f"{'❚❚' if state.paused else '▷'} {clean(track.name)}", style="bold")
            detail = " · ".join(part for part in (artist(track), album(track), source) if part)
        else:
            now.append(_("No song"), style="dim")
        changes.show(self._now_playing, now)
        changes.show(self._now_detail, detail)
        changes.show(self._elapsed, duration(state.position))
        changes.show(self._total, duration(state.duration or getattr(track, "duration", 0)))
        self._progress.update(total=max(1, state.duration or getattr(track, "duration", 0) or 1),
                              progress=max(0, state.position))
        changes.show(self._volume_bar, level_bar(state.volume))
        changes.show(self._volume_label, f"{state.volume}%")
        changes.label(self._pause_button, "▷" if state.paused or state.index < 0 else "❚❚")
        self._shuffle_button.set_class(state.shuffle, "active")
        self._repeat_button.set_class(state.repeat != "off", "active")
        changes.label(self._repeat_button, "↻1" if state.repeat == "one" else "↻")

    @on(Button.Pressed)
    def pressed(self, event):
        event.stop()
        identifier = event.button.id
        if identifier == "pause":
            self.app.action_pause()
        elif identifier in ("next", "previous", "shuffle", "repeat"):
            self.app.control(identifier)
        elif identifier in ("quieter", "louder"):
            self.app.control("volume", -5 if identifier == "quieter" else 5)

    @on(SeekProgress.SeekRequested)
    def seek_requested(self, event):
        state = self.app.playback.snapshot()
        if state.status in ("Playing", "Paused") and state.duration > 0:
            self.app.control("seek_absolute", round(state.duration * event.fraction, 2))

    @on(VolumeBar.VolumeRequested)
    def volume_requested(self, event):
        change = event.volume - self.app.playback.snapshot().volume
        if change:
            self.app.control("volume", change)
