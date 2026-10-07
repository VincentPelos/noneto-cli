"""cava-style bars for the lyrics tab, fed by the FFT of mpv's own output.

The playback side captures only mpv (process loopback) and returns raw band
amplitudes. Here they get cava's treatment: automatic sensitivity, a little
smoothing and gravity on the way down.
"""
import time

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip
from textual.widget import Widget

from .themes import VISUALIZER_STOPS, gradient

BLOCKS = " ▁▂▃▄▅▆▇█"
FPS = 30
SMOOTHING = 0.45   # share of the new frame; the rest is the previous one (cava's noise reduction)
GRAVITY = 4.0      # height units per second squared on the way down
SILENCE = 1e-12


class CavaBars:
    """Bar heights in [0, 1]."""

    def __init__(self, count):
        self.heights = [0.0] * count
        self._memory = [0.0] * count
        self._peaks = [0.0] * count
        self._falling = [0.0] * count
        self.sensitivity = 1.0
        self._settled = False

    def resize(self, count):
        if count != len(self.heights):
            sensitivity, settled = self.sensitivity, self._settled
            self.__init__(count)
            self.sensitivity, self._settled = sensitivity, settled

    def step(self, raw, dt):
        count = len(self.heights)
        if not isinstance(raw, (list, tuple)) or len(raw) != count:
            raw = [0.0] * count
        loud = max(raw, default=0.0) > SILENCE
        values = []
        for index, amplitude in enumerate(raw):
            value = self._memory[index] * (1 - SMOOTHING) + amplitude * self.sensitivity * SMOOTHING
            self._memory[index] = value
            values.append(value)
        # Automatic sensitivity: back off fast when bars clip, creep up while they don't.
        if max(values, default=0.0) > 1:
            self.sensitivity *= 0.97
            self._settled = True
        elif loud:
            self.sensitivity *= 1.003 if self._settled else 1.1
        self.sensitivity = max(1e-3, min(1e9, self.sensitivity))
        for index, value in enumerate(values):
            value = min(1.0, value)
            if value >= self.heights[index]:
                self.heights[index] = self._peaks[index] = value
                self._falling[index] = 0.0
            else:
                self._falling[index] += dt
                drop = GRAVITY * self._falling[index] ** 2
                self.heights[index] = max(value, self._peaks[index] - drop)
        return self.heights


def render_bars(heights, width, rows, bar=1, gap=1):
    """Draw bars bottom-up with eighth blocks; returns `rows` lines of `width` cells."""
    levels = [round(height * rows * 8) for height in heights]
    spacer = " " * gap
    lines = []
    for row in range(rows):
        floor = (rows - 1 - row) * 8
        line = "".join(BLOCKS[min(8, max(0, level - floor))] * bar + spacer for level in levels)
        lines.append(line[:width].ljust(width))
    return lines


def bar_count(width):
    return max(1, (width + 1) // 2)


class Visualizer(Widget):
    """Animates only while running; `source(count)` returns band amplitudes or None.

    Uses the line API: each frame is plain strings, so Textual neither wraps text
    nor re-lays out the screen. Measured headless at 120x38 with 10 rows: 31.7% of a
    core with Static.update, 17.8% without layout, 15.1% with the line API.
    """

    def __init__(self, source, **kwargs):
        super().__init__(**kwargs)
        self._source = source
        self._bars = CavaBars(0)
        self._lines = []
        self._timer = None
        self._last = time.monotonic()
        self._palette_key = None
        self._styles = []

    @property
    def running(self):
        return self._timer is not None

    def start(self):
        if self._timer is None:
            self._last = time.monotonic()
            self._timer = self.set_interval(1 / FPS, self.tick)

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._lines = []
        self.refresh()

    def bar_styles(self, count):
        """One style per bar: the theme's gradient from left to right, recomputed on theme change."""
        theme = self.app.theme
        if self._palette_key != (theme, count):
            stops = VISUALIZER_STOPS.get(theme) or (self.app.current_theme.primary,)
            base = self.rich_style
            self._styles = [base + Style(color=color) for color in gradient(stops, count)]
            self._palette_key = (theme, count)
        return self._styles

    def render_line(self, y):
        width = self.content_size.width
        line = (self._lines[y] if y < len(self._lines) else "")[:width].ljust(width)
        styles = self.bar_styles(bar_count(width))
        # Each bar is one cell plus its gap, as drawn by render_bars.
        segments = [Segment(line[index * 2:index * 2 + 2], style) for index, style in enumerate(styles)]
        return Strip([segment for segment in segments if segment.text], width)

    def tick(self):
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        width, rows = self.content_size.width, self.content_size.height
        if width <= 0 or rows <= 0:
            return
        count = bar_count(width)
        self._bars.resize(count)
        heights = self._bars.step(self._source(count), dt)
        self._lines = render_bars(heights, width, rows)
        self.refresh()
