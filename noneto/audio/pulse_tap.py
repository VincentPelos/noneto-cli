"""Capture only mpv's stream with a PulseAudio monitor (PulseAudio or PipeWire's pulse server).

Read-only tap for the visualizer: `parec --monitor-stream` reads a copy of mpv's sink input and
never touches its output path. Samples stay in a small in-memory ring and are never written anywhere.
"""
import json
import os
import shutil
import subprocess
import threading

from ..diagnostics import emit, failure

RATE = 48000
CHANNELS = 2
FRAME_BYTES = 4 * CHANNELS  # float32 stereo
RING_FRAMES = 4096
SEARCH_SECONDS = 0.5  # mpv opens its stream on the first file and may reopen it on format changes


def sink_input_for(listing, pid):
    """Index of the sink input owned by `pid` in `pactl -f json list sink-inputs` output, or None."""
    for entry in json.loads(listing or "[]"):
        if str(entry.get("properties", {}).get("application.process.id")) == str(pid):
            return entry.get("index")
    return None


class PulseTap:
    """Keeps the latest RING_FRAMES of mpv's output as interleaved float32 stereo."""

    def __init__(self, pid):
        self.pid = int(pid)
        self.rate = RATE
        self._ring = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._process = None
        self.failed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="noneto-tap", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def samples(self):
        with self._lock:
            return bytes(self._ring)

    def _push(self, chunk):
        with self._lock:
            self._ring += chunk
            excess = len(self._ring) - RING_FRAMES * FRAME_BYTES
            if excess > 0:
                del self._ring[:excess]

    def _run(self):
        try:
            pactl, parec = shutil.which("pactl"), shutil.which("parec")
            if not pactl or not parec:
                raise OSError("pactl/parec not found")
            started = False
            while not self._stop.is_set():
                index = self._find(pactl)
                if index is None:
                    self._stop.wait(SEARCH_SECONDS)
                    continue
                if not started:
                    emit("visualizer.tap_started")
                    started = True
                self._capture(parec, index)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.failed.set()
            failure("visualizer.tap_failed", error)

    def _find(self, pactl):
        result = subprocess.run([pactl, "-f", "json", "list", "sink-inputs"], capture_output=True,
                                text=True, timeout=5)
        if result.returncode != 0:
            raise OSError(f"pactl exited with {result.returncode}")
        return sink_input_for(result.stdout, self.pid)

    def _capture(self, parec, index):
        """Read until the stream ends (mpv closed or reopened it) or the tap stops."""
        process = subprocess.Popen(
            [parec, f"--monitor-stream={index}", "--raw", "--format=float32le",
             f"--rate={RATE}", f"--channels={CHANNELS}", "--latency-msec=20"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with self._lock:
            self._process = process
        carry = b""
        try:
            if self._stop.is_set():
                return
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                carry += chunk
                whole = len(carry) - len(carry) % FRAME_BYTES  # never split a frame across the ring
                self._push(carry[:whole])
                carry = carry[whole:]
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
            process.stdout.close()
            with self._lock:
                self._process = None
        if not self._stop.is_set():
            self._stop.wait(SEARCH_SECONDS)
