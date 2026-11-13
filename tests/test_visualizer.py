import json
import os
import shutil
import sys
from array import array
from concurrent.futures import Future
import math
from pathlib import Path
import queue
import struct
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
import wave

from noneto.audio.controller import PlaybackController, PlaybackState
from noneto.audio.player import Player
from noneto.audio.pulse_tap import PulseTap, sink_input_for
from noneto.audio.spectrum import Spectrum
if os.name == "nt":
    from noneto.audio.tap import ProcessTap
from noneto.ui.themes import (CUSTOM_THEMES, ThemeSpec, VISUALIZER_STOPS, available_themes,
                              load_user_themes, register_theme)
from noneto.ui.tui import NonetoApp
from noneto.ui.visualizer import (VISUALIZER_MODES_REGISTRY, CavaBars, Visualizer,
                                  available_visualizer_modes, bar_count, register_visualizer_mode,
                                  render_bars, render_mode)

from tests.test_tui import FakeLibrary

RATE = 48000


def sine(frequency, frames=4096, amplitude=0.5):
    """Interleaved float32 stereo bytes, as the tap stores them."""
    values = array("f")
    for n in range(frames):
        value = amplitude * math.sin(math.tau * frequency * n / RATE)
        values.extend((value, value))
    return values.tobytes()


def write_sine_wav(path, frequency=1000, seconds=3):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        frames = bytearray()
        for n in range(RATE * seconds):
            sample = struct.pack("<h", int(16000 * math.sin(math.tau * frequency * n / RATE)))
            frames += sample + sample
        wav.writeframes(bytes(frames))


def band_of(frequency, count):
    """Index of the log-spaced band (50 Hz to 12 kHz) holding `frequency`."""
    return int(math.log(frequency / 50) / math.log(12000 / 50) * count)


class SpectrumTests(unittest.TestCase):
    def test_tone_lands_in_its_band_and_silence_is_flat(self):
        spectrum = Spectrum(RATE)
        for frequency in (100, 1000, 6000):
            bands = spectrum.bands(sine(frequency), 40)
            self.assertAlmostEqual(bands.index(max(bands)), band_of(frequency, 40), delta=1)
        self.assertEqual(max(spectrum.bands(bytes(4096 * 8), 40)), 0)
        short = spectrum.bands(sine(1000, frames=500), 40)
        self.assertEqual(len(short), 40)
        self.assertGreater(max(short), 0)
        self.assertEqual(len(spectrum.bands(b"", 12)), 12)


class CavaBarsTests(unittest.TestCase):
    def test_autosens_fills_bars_without_clipping_and_gravity_drops_them(self):
        bars = CavaBars(8)
        quiet = [1e-7, 4e-7, 2e-7, 1e-7, 1e-8, 1e-8, 1e-8, 1e-8]
        for _ in range(240):
            bars.step(quiet, 1 / 30)
        self.assertGreater(max(bars.heights), 0.6)
        self.assertLessEqual(max(bars.heights), 1.0)
        self.assertGreater(bars.heights[1], bars.heights[6])
        sensitivity = bars.sensitivity
        for _ in range(30):
            bars.step([0.0] * 8, 1 / 30)
        self.assertEqual(bars.sensitivity, sensitivity)
        self.assertLess(max(bars.heights), 0.01)
        bars.step(Mock(), 1 / 30)  # anything but a list of the right size counts as silence
        bars.step([1.0], 1 / 30)

    def test_thin_bars_fill_from_the_bottom(self):
        self.assertEqual(render_bars([1.0, 0.5, 0.0], width=6, rows=2), ["█     ", "█ █   "])
        self.assertEqual(bar_count(80), 40)

    def test_all_builtin_modes_render_exact_dimensions(self):
        heights = [0.9, 0.5, 0.2, 0.0]
        peaks = [1.0, 0.75, 0.4, 0.0]
        self.assertEqual(available_visualizer_modes(), ("classic", "braille", "mirror", "flame"))
        for mode in available_visualizer_modes():
            lines = render_mode(mode, heights, peaks, width=8, rows=4)
            self.assertEqual(len(lines), 4, mode)
            self.assertTrue(all(len(line) == 8 for line in lines), mode)
            self.assertTrue(any(line.strip(" ⠀") for line in lines), mode)

    def test_mirror_grows_up_and_down_from_the_middle_with_braille_dots(self):
        full = render_mode("mirror", [1.0, 1.0, 0.0], [], width=6, rows=4)
        self.assertEqual([line[0] for line in full], ["⣿"] * 4)
        quiet = [line[2] for line in render_mode("mirror", [0.3, 0.3, 0.0], [], width=6, rows=4)]
        # Symmetric: the rows above the middle mirror the rows below it, and the edges stay empty.
        self.assertEqual(quiet[0], "⠀")
        self.assertEqual(quiet[-1], "⠀")
        self.assertNotEqual(quiet[1], "⠀")
        silent = render_mode("mirror", [0.0] * 3, [], width=6, rows=4)
        self.assertTrue(all(set(line) <= {" ", "⠀"} for line in silent))

    def test_flame_is_solid_below_and_shaded_at_the_tip(self):
        lines = render_mode("flame", [1.0, 0.5, 0.0], [], width=6, rows=4)
        self.assertEqual([line[0] for line in lines], ["▒", "▓", "█", "█"])
        self.assertEqual([line[2] for line in lines], [" ", " ", "▒", "▓"])
        self.assertTrue(all(line[4] == " " for line in lines))


class ExtensionApiTests(unittest.TestCase):
    def test_register_custom_visualizer_mode_and_theme_spec(self):
        import json as _json
        import noneto.ui.themes as themes_mod
        import noneto.ui.visualizer as vis_mod

        orig_themes = themes_mod.THEMES
        orig_custom = dict(CUSTOM_THEMES)
        orig_stops = dict(VISUALIZER_STOPS)
        orig_modes = vis_mod.VISUALIZER_MODES
        orig_reg = dict(VISUALIZER_MODES_REGISTRY)
        try:
            @register_visualizer_mode("stars")
            def _stars(heights, width, rows):
                return [("*" * len(heights))[:width].ljust(width) for _ in range(rows)]

            self.assertIn("stars", available_visualizer_modes())
            self.assertEqual(render_mode("stars", [0.5, 0.8], [0.6, 0.9], 4, 2), ["**  ", "**  "])

            spec = ThemeSpec(
                name="cyber-amber",
                primary="#ffb000",
                background="#0d0a00",
                foreground="#ffe082",
                stops=("#ff6f00", "#ffb000", "#ffe082"),
            )
            register_theme(spec)
            self.assertIn("cyber-amber", available_themes())
            self.assertEqual(VISUALIZER_STOPS["cyber-amber"][0], "#ff6f00")

            with tempfile.TemporaryDirectory() as directory:
                theme_file = Path(directory) / "oceanic.json"
                theme_file.write_text(_json.dumps({
                    "name": "oceanic-deep",
                    "primary": "#00bcd4",
                    "background": "#051622",
                    "foreground": "#e0f7fa",
                    "stops": ["#006064", "#00bcd4", "#80deea"],
                }), encoding="utf-8")
                (Path(directory) / "invalid.json").write_text("{bad", encoding="utf-8")
                loaded = load_user_themes(directory)
                self.assertEqual(loaded, ("oceanic-deep",))
                self.assertIn("oceanic-deep", available_themes())

            with self.assertRaises(ValueError):
                ThemeSpec(name="Bad Name!", primary="#ffffff", background="#000000")
            with self.assertRaises(ValueError):
                ThemeSpec(name="bad-color", primary="red", background="#000000")
            with self.assertRaises(ValueError):
                register_visualizer_mode("Bad Mode!", lambda h, w, r: [])
        finally:
            themes_mod.THEMES = orig_themes
            CUSTOM_THEMES.clear()
            CUSTOM_THEMES.update(orig_custom)
            VISUALIZER_STOPS.clear()
            VISUALIZER_STOPS.update(orig_stops)
            vis_mod.VISUALIZER_MODES = orig_modes
            VISUALIZER_MODES_REGISTRY.clear()
            VISUALIZER_MODES_REGISTRY.update(orig_reg)


class FakeTap:
    def __init__(self, pid):
        self.pid, self.rate = pid, RATE
        self.failed = threading.Event()
        self.stopped = False

    def start(self):
        return self

    def stop(self):
        self.stopped = True

    def samples(self):
        return sine(1000)


class FakePlayer:
    def __init__(self):
        self.process = NS(pid=4242, poll=lambda: None)
        self.events = queue.Queue()
        self.failed = threading.Event()

    def command(self, *args, **kwargs):
        return None

    def play(self, target):
        self.events.put({"event": "start-file"})
        self.events.put({"event": "file-loaded"})

    def close(self):
        pass


class ControllerTapTests(unittest.TestCase):
    def wait(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(condition())

    def test_tap_follows_visualizer_and_player(self):
        taps = []

        def tap_factory(pid):
            taps.append(FakeTap(pid))
            return taps[-1]

        def resolve(_track):
            future = Future()
            future.set_result("song")
            return future

        controller = PlaybackController(resolve, FakePlayer, tap_factory)
        try:
            self.assertIsNone(controller.bands(10))
            controller.send("visualizer", True)
            time.sleep(0.15)
            self.assertEqual(taps, [])  # no mpv yet: nothing to capture
            controller.send("play", (("song",), 0))
            self.wait(lambda: taps)
            self.assertEqual(taps[0].pid, 4242)
            bands = controller.bands(40)
            self.assertAlmostEqual(bands.index(max(bands)), band_of(1000, 40), delta=1)
            controller.send("visualizer", False)
            self.wait(lambda: taps[0].stopped)
            self.assertIsNone(controller.bands(10))
            controller.send("visualizer", True)
            self.wait(lambda: len(taps) == 2)
        finally:
            controller.close()
        self.assertTrue(taps[1].stopped)


@unittest.skipUnless(os.name == "nt", "Native Windows integration")
class ProcessTapTests(unittest.TestCase):
    def test_captures_only_what_mpv_plays(self):
        """Real device, faint: volume 10 is about -60 dB, above endpoint noise gates.

        At volume 1 (~-120 dB), some Windows audio paths return digital silence even
        though process loopback works. Keep the signal quiet but measurable.
        """
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tone.wav"
            write_sine_wav(source)
            with Player() as player:
                player.command("set_property", "volume", 10)
                tap = ProcessTap(player.process.pid).start()
                try:
                    player.play(source)
                    deadline = time.monotonic() + 5
                    peak = 0.0
                    while time.monotonic() < deadline and not tap.failed.is_set():
                        time.sleep(0.1)
                        data = array("f", tap.samples())
                        peak = max((abs(value) for value in data), default=0.0)
                        if peak and len(data) == 4096 * 2:
                            break
                    if tap.failed.is_set():
                        self.skipTest("Process loopback unavailable on this machine")
                    self.assertGreater(peak, 0)
                    bands = Spectrum(RATE).bands(tap.samples(), 40)
                    self.assertAlmostEqual(bands.index(max(bands)), band_of(1000, 40), delta=1)
                finally:
                    tap.stop()


class PulseSinkInputTests(unittest.TestCase):
    def test_matches_only_the_mpv_pid(self):
        listing = json.dumps([
            {"index": 3, "properties": {"application.process.id": "100", "application.name": "other"}},
            {"index": 7, "properties": {"application.process.id": "4242", "application.name": "mpv"}},
        ])
        self.assertEqual(sink_input_for(listing, 4242), 7)
        self.assertIsNone(sink_input_for(listing, 999))
        self.assertIsNone(sink_input_for("[]", 4242))


@unittest.skipUnless(sys.platform.startswith("linux") and shutil.which("parec") and shutil.which("pactl"),
                     "Linux with a PulseAudio-compatible server")
class PulseTapTests(unittest.TestCase):
    def test_captures_only_what_mpv_plays(self):
        """Real server, faint (~-48 dBFS): monitors read after mpv's volume and the WSLg server
        returned zeros at volume 1 and 5, so this tone stays briefly audible."""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tone.wav"
            write_sine_wav(source)
            with Player(extra_args=["--volume=20", "--loop-file=inf"]) as player:
                player.command("set_property", "volume", 20)
                tap = PulseTap(player.process.pid).start()
                try:
                    player.play(source)
                    deadline = time.monotonic() + 8
                    peak = 0.0
                    while time.monotonic() < deadline and not tap.failed.is_set():
                        time.sleep(0.1)
                        data = array("f", tap.samples())
                        peak = max((abs(value) for value in data), default=0.0)
                        if peak and len(data) == 4096 * 2:
                            break
                    if tap.failed.is_set() or not peak:
                        self.skipTest("PulseAudio monitor unavailable on this machine")
                    bands = Spectrum(RATE).bands(tap.samples(), 40)
                    self.assertAlmostEqual(bands.index(max(bands)), band_of(1000, 40), delta=1)
                finally:
                    tap.stop()
                self.assertFalse(tap._thread.is_alive())


class VisualizerTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_visualizer_runs_only_on_lyrics_tab_with_room(self):
        player = Mock(snapshot=Mock(return_value=PlaybackState()), bands=Mock(return_value=None))
        app = NonetoApp(FakeLibrary(), player)
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            visualizer = app.query_one(Visualizer)
            self.assertFalse(visualizer.running)
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(visualizer.display)
            self.assertTrue(visualizer.running)
            self.assertGreaterEqual(visualizer.size.height, 4)
            self.assertGreaterEqual(app.query_one("#lyrics-pane").size.height, 10)
            player.send.assert_called_with("visualizer", True)
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            await pilot.pause()
            self.assertFalse(visualizer.display)
            self.assertFalse(visualizer.running)
            player.send.assert_called_with("visualizer", False)
            await pilot.resize_terminal(120, 38)
            await pilot.pause()
            await pilot.pause()
            self.assertTrue(visualizer.running)
            await pilot.click("#--content-tab-songs-view")
            await pilot.pause()
            await pilot.pause()
            self.assertFalse(visualizer.running)
            player.send.assert_called_with("visualizer", False)

    async def test_bars_follow_the_theme_gradient_and_ctrl_v_or_click_cycles_modes(self):
        chosen_modes = []
        player = Mock(snapshot=Mock(return_value=PlaybackState()), bands=Mock(return_value=None))
        app = NonetoApp(FakeLibrary(), player, theme="dracula", visualizer_mode="classic",
                        on_visualizer_mode=chosen_modes.append)
        async with app.run_test(size=(120, 38)) as pilot:
            await pilot.pause()
            visualizer = app.query_one(Visualizer)
            styles = visualizer.bar_styles(6)
            # Left to right through Dracula's own colors: cyan first, purple last.
            self.assertEqual(styles[0].color.triplet.hex, "#8be9fd")
            self.assertEqual(styles[-1].color.triplet.hex, "#bd93f9")
            self.assertEqual(len({style.color.triplet.hex for style in styles}), 6)
            app.theme = "kanagawa"
            await pilot.pause()
            self.assertEqual(visualizer.bar_styles(6)[0].color.triplet.hex, "#7e9cd8")
            visualizer._lines = render_bars([1.0] * 6, 11, 1)
            strip = visualizer.render_line(0)
            self.assertEqual(strip.cell_length, visualizer.content_size.width)

            await pilot.press("ctrl+g")
            await pilot.pause()
            self.assertEqual(app.visualizer_mode, "braille")
            self.assertEqual(visualizer.mode, "braille")
            await pilot.click("#--content-tab-lyrics-view")
            await pilot.pause()
            await pilot.click("#visualizer")
            await pilot.pause()
            self.assertEqual(app.visualizer_mode, "mirror")
            self.assertEqual(chosen_modes, ["braille", "mirror"])
