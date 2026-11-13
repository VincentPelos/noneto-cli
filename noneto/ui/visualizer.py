"""Track aura: the cava-style spectrum under the lyrics, fed by the FFT of mpv's own output.

The playback side captures only mpv (process loopback) and returns raw band
amplitudes. Here they get cava's treatment: automatic sensitivity, a little
smoothing and gravity on the way down, plus a pluggable mode registry so people
and agents can register custom visualizer effects in one function.
"""
import inspect
import time

from rich.segment import Segment
from rich.style import Style
from textual.message import Message
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from .themes import NAME_PATTERN, VISUALIZER_STOPS, gradient

BLOCKS = " ▁▂▃▄▅▆▇█"
FLAME = "░▒▓█"  # tip to body
L_BITS = (0x01, 0x02, 0x04, 0x40)
R_BITS = (0x08, 0x10, 0x20, 0x80)
FPS = 30
SMOOTHING = 0.45    # share of the new frame; the rest is the previous one (cava's noise reduction)
GRAVITY = 4.0       # height units per second squared on the way down
PEAK_GRAVITY = 1.15 # slower gravity for floating peak-hold caps
SILENCE = 1e-12
MIRROR_CURVE = 0.7  # below 1 lifts quiet bands so the mirror spreads further
LYRICS_MIN_ROWS = 10  # the visualizer only takes rows the lyrics can spare
VISUALIZER_ROWS = (4, 10)
CSS = """
#visualizer { height: 6; padding: 0 1; color: $foreground; }
"""


class CavaBars:
    """Bar heights and floating peak-hold caps in [0, 1]."""

    def __init__(self, count):
        self.heights = [0.0] * count
        self.peaks = [0.0] * count
        self._memory = [0.0] * count
        self._crests = [0.0] * count
        self._falling = [0.0] * count
        self._peak_crests = [0.0] * count
        self._peak_falling = [0.0] * count
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
                self.heights[index] = self._crests[index] = value
                self._falling[index] = 0.0
            else:
                self._falling[index] += dt
                drop = GRAVITY * self._falling[index] ** 2
                self.heights[index] = max(value, self._crests[index] - drop)

            height = self.heights[index]
            if height >= self.peaks[index]:
                self.peaks[index] = self._peak_crests[index] = height
                self._peak_falling[index] = 0.0
            else:
                self._peak_falling[index] += dt
                peak_drop = PEAK_GRAVITY * self._peak_falling[index] ** 2
                self.peaks[index] = max(height, self._peak_crests[index] - peak_drop)
        return self.heights


def render_bars(heights, width, rows, bar=1, gap=1):
    """Draw classic bars bottom-up with eighth blocks; returns `rows` lines of `width` cells."""
    levels = [round(height * rows * 8) for height in heights]
    spacer = " " * gap
    lines = []
    for row in range(rows):
        floor = (rows - 1 - row) * 8
        line = "".join(BLOCKS[min(8, max(0, level - floor))] * bar + spacer for level in levels)
        lines.append(line[:width].ljust(width))
    return lines


def _braille(rows, row, left, right):
    """One Braille cell of `row`: dots lit where `left`/`right` say so, counted from the bottom."""
    mask = 0
    for dot in range(4):
        dot_from_bottom = (rows - 1 - row) * 4 + (3 - dot)
        if left(dot_from_bottom):
            mask |= L_BITS[dot]
        if right(dot_from_bottom):
            mask |= R_BITS[dot]
    return chr(0x2800 + mask)


def _halves(heights):
    """Each bar and the midpoint to its right neighbor: the two dot columns of a Braille cell."""
    last = len(heights) - 1
    return [(height, (height + heights[min(last, index + 1)]) * 0.5) for index, height in enumerate(heights)]


def render_braille(heights, peaks, width, rows):
    """Draw a high-resolution 2×4 Braille dot spectrum interpolated across neighboring bands."""
    total_dots = rows * 4
    levels = [(round(left * total_dots), round(right * total_dots)) for left, right in _halves(heights)]
    lines = []
    for row in range(rows):
        cells = [_braille(rows, row, lambda dot, top=left: dot < top, lambda dot, top=right: dot < top) + " "
                 for left, right in levels]
        lines.append("".join(cells)[:width].ljust(width))
    return lines


def render_mirror(heights, peaks, width, rows):
    """Draw a symmetric Braille wave that grows up and down from the middle row."""
    total_dots = rows * 4
    middle = total_dots / 2
    # A softer curve than the bars so quiet passages still reach well above and below the middle.
    spans = [(round(left ** MIRROR_CURVE * middle), round(right ** MIRROR_CURVE * middle))
             for left, right in _halves(heights)]
    lines = []
    for row in range(rows):
        cells = [_braille(rows, row, lambda dot, span=left: abs(dot + 0.5 - middle) < span,
                          lambda dot, span=right: abs(dot + 0.5 - middle) < span) + " "
                 for left, right in spans]
        lines.append("".join(cells)[:width].ljust(width))
    return lines


def render_flame(heights, peaks, width, rows):
    """Draw solid bars that thin into shade toward the top (`█▓▒░`), like a flame."""
    lines = []
    for row in range(rows):
        base = rows - 1 - row
        cells = []
        for height in heights:
            depth = height * rows - base  # how far the bar's tip is above this cell's bottom
            if height * rows < 0.15 or depth <= 0:
                cells.append("  ")
            else:
                cells.append(FLAME[min(3, int(depth))] + " ")
        lines.append("".join(cells)[:width].ljust(width))
    return lines


def _adapt_renderer(fn):
    """Allow custom mode functions with `(heights, width, rows)` or `(heights, peaks, width, rows)`."""
    try:
        sig = inspect.signature(fn)
        required = sum(
            1 for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
    except (TypeError, ValueError):
        required = 4
    if required == 3:
        return lambda heights, _peaks, width, rows: fn(heights, width, rows)
    return fn


VISUALIZER_MODES_REGISTRY = {
    "classic": lambda heights, _peaks, width, rows: render_bars(heights, width, rows),
    "braille": render_braille,
    "mirror": render_mirror,
    "flame": render_flame,
}

VISUALIZER_MODES = tuple(VISUALIZER_MODES_REGISTRY)
# Names shown to people (translated by the caller); custom modes show their own id.
MODE_LABELS = {"classic": "Classic", "braille": "Braille", "mirror": "Mirror", "flame": "Flame"}


def register_visualizer_mode(name, fn=None):
    """Register a custom visualizer effect in Noneto CLI (usable as a function or `@decorator`).

    The renderer receives `(heights, peaks, width, rows)` (or `(heights, width, rows)`) where
    `heights` and `peaks` are lists of floats in `[0.0, 1.0]` of length `bar_count(width)`,
    and must return a list of `rows` strings (each padded/clipped to `width` cells).
    """
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        raise ValueError(f"Invalid visualizer mode name: {name!r}")

    def _register(renderer):
        global VISUALIZER_MODES
        if not callable(renderer):
            raise TypeError("Visualizer mode renderer must be callable")
        VISUALIZER_MODES_REGISTRY[name] = _adapt_renderer(renderer)
        if name not in VISUALIZER_MODES:
            VISUALIZER_MODES = (*VISUALIZER_MODES, name)
        return renderer

    return _register(fn) if fn is not None else _register


def available_visualizer_modes() -> tuple[str, ...]:
    """Return the registered visualizer mode names in cycle order."""
    return VISUALIZER_MODES


def render_mode(mode, heights, peaks, width, rows):
    """Render `mode` into `rows` lines of exact `width` cells."""
    renderer = VISUALIZER_MODES_REGISTRY.get(mode) or VISUALIZER_MODES_REGISTRY["classic"]
    raw_lines = renderer(heights, peaks, width, rows)
    return [str(line)[:width].ljust(width) for line in list(raw_lines)[:rows]] + [" " * width] * max(0, rows - len(raw_lines))


def bar_count(width):
    return max(1, (width + 1) // 2)


class Visualizer(Widget):
    """Animates only while running; `source(count)` returns band amplitudes or None.

    Uses the line API: each frame is plain strings, so Textual neither wraps text
    nor re-lays out the screen. Measured headless at 120x38 with 10 rows: 31.7% of a
    core with Static.update, 17.8% without layout, 15.1% with the line API.
    """

    class Clicked(Message):
        """Posted when the user clicks the visualizer to cycle the effect mode."""

    def __init__(self, source, mode="classic", **kwargs):
        super().__init__(**kwargs)
        self._source = source
        self.mode = mode if mode in VISUALIZER_MODES_REGISTRY else "classic"
        self._bars = CavaBars(0)
        self._lines = []
        self._timer = None
        self._last = time.monotonic()
        self._palette_key = None
        self._styles = []
        self._frame_key = None

    def notify_style_update(self):
        super().notify_style_update()
        self._palette_key = None
        self._frame_key = None

    def _present(self, lines):
        width, rows = self.content_size.width, self.content_size.height
        key = (width, rows, self.mode, self.app.theme)
        previous, self._lines = self._lines, lines
        if key != self._frame_key:
            self._frame_key = key
            self.refresh()
        else:
            dirty = [Region(0, y, width, 1) for y, line in enumerate(lines)
                     if y >= len(previous) or line != previous[y]]
            if dirty:
                self.refresh(*dirty)

    def set_mode(self, mode):
        self.mode = mode if mode in VISUALIZER_MODES_REGISTRY else "classic"
        width, rows = self.content_size.width, self.content_size.height
        if width > 0 and rows > 0 and self._bars.heights:
            self._present(render_mode(self.mode, self._bars.heights, self._bars.peaks, width, rows))

    def on_click(self, event):
        if event.button == 1:
            self.post_message(self.Clicked())
            event.stop()

    @property
    def running(self):
        return self._timer is not None

    def start(self):
        if self._timer is None:
            self._last = time.monotonic()
            self._timer = self.set_interval(1 / FPS, self.tick)

    def fit(self, area, wanted):
        """Show only when wanted and the lyrics in `area` can spare the rows; returns whether it shows."""
        rows = min(VISUALIZER_ROWS[1], area.height - LYRICS_MIN_ROWS)
        show = wanted and rows >= VISUALIZER_ROWS[0] and area.width >= 40
        self.display = show
        if show:
            self.styles.height = rows
            self.start()
        else:
            self.stop()
        return show

    def stop(self):
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._lines = []
        self._frame_key = None
        self.refresh()

    def bar_styles(self, count):
        """One style per bar: the theme's gradient from left to right, recomputed on theme change."""
        theme = self.app.theme
        base = self.rich_style
        if self._palette_key != (theme, count, base):
            stops = VISUALIZER_STOPS.get(theme) or (self.app.current_theme.primary,)
            self._styles = [base + Style(color=color) for color in gradient(stops, count)]
            self._palette_key = (theme, count, base)
        return self._styles

    def render_line(self, y):
        width = self.content_size.width
        line = (self._lines[y] if y < len(self._lines) else "")[:width].ljust(width)
        styles = self.bar_styles(bar_count(width))
        # Each bar is one cell plus its gap, as drawn by render_mode.
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
        self._present(render_mode(self.mode, heights, self._bars.peaks, width, rows))
