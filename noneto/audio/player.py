"""mpv JSON IPC transport: bounded messages, acknowledgements and explicit failure."""
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import os
import shutil

from ..errors import AppError
from ..i18n import _
from ..diagnostics import emit, failure, mpv_message
if os.name == "nt":
    from .lifetime import PlayerJob
from .mpv_install import mpv_dir

MAX_FRAME = 1024 * 1024
COMMAND_TIMEOUT = 5


def find_mpv():
    if os.name == "nt":
        local = mpv_dir() / "mpv.exe"
        if not local.is_file():
            raise AppError(_("mpv is missing. Install it with: noneto --install-mpv"))
        return str(local)
    executable = shutil.which("mpv")
    if executable is None:
        raise AppError(_("mpv is missing. Install it with your system package manager."))
    return executable


class Player:
    def __init__(self, executable=None, extra_args=(), media_controls=True):
        self.events = queue.Queue(maxsize=128)
        self.failed = threading.Event()
        self._closing = threading.Event()
        self._lock = threading.Lock()
        self._command_lock = threading.Lock()
        self._pending = {}
        self._sequence = 0
        self.pipe = None
        self.thread = None
        self._job = None
        self._wakeup = None
        if os.name == "nt":
            from .ipc_windows import WindowsIPC
            self._transport = WindowsIPC()
        else:
            from .ipc_unix import UnixIPC
            self._transport = UnixIPC()
        self.process = None
        try:
            self.process = subprocess.Popen([
                str(Path(executable or find_mpv()).resolve()), *extra_args,
                "--no-config", "--idle=yes", "--no-video", "--audio-display=no",
                "--terminal=no", "--volume=60", "--load-scripts=no", "--ytdl=no",
                "--load-unsafe-playlists=no", "--access-references=no", "--tls-verify=yes",
                "--network-timeout=30",
                *([f"--media-controls={'yes' if media_controls else 'no'}"] if os.name == "nt" else []),
                "--prefetch-playlist=yes",
                "--demuxer-lavf-o=protocol_whitelist=[file,https,tls,tcp]",
                self._transport.argument,
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
               stderr=subprocess.DEVNULL, **self._transport.spawn_options)
            # mpv is idle: bind its lifetime before IPC can load any audio.
            # Abrupt terminal closure may bypass close()/finally entirely.
            if os.name == "nt":
                self._job = PlayerJob()
                self._job.attach(self.process)
            self._transport.connect(self.process)
            self.pipe = self._transport.pipe
            self.thread = threading.Thread(target=self._listen, daemon=True, name="mpv-ipc")
            self.thread.start()
            self.command("get_property", "mpv-version")
            self.command("request_log_messages", "warn")
        except BaseException:
            self.close()
            raise

    def _receive(self, message):
        if not isinstance(message, dict):
            raise ValueError("Invalid IPC object")
        if message.get("event") == "log-message":
            mpv_message(message)
            return
        request_id = message.get("request_id")
        if request_id is not None:
            with self._lock:
                pending = self._pending.get(request_id)
                if pending:
                    pending[1].update(message)
                    pending[0].set()
        if message.get("event") in {"start-file", "file-loaded", "end-file", "shutdown"}:
            self.events.put_nowait(message)
            if self._wakeup is not None:
                self._wakeup()

    def set_wakeup(self, wakeup):
        self._wakeup = wakeup
        wakeup()  # Includes events already delivered before attachment.

    def _listen(self):
        buffer = b""
        try:
            while not self._closing.is_set() and self.process.poll() is None:
                chunk = self._transport.read(65536)
                if chunk is None:
                    continue
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_FRAME:
                    raise ValueError("IPC frame too large")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self._receive(json.loads(line))
        except (OSError, ValueError, TypeError, queue.Full) as error:
            if not self._closing.is_set():
                failure("ipc.failed", error, stage="read")
        finally:
            if not self._closing.is_set():
                self.failed.set()
                if self._wakeup is not None:
                    self._wakeup()
            with self._lock:
                for ready, _response in self._pending.values():
                    ready.set()

    def command(self, *args, timeout=COMMAND_TIMEOUT, allow_unavailable=False):
        with self._command_lock:
            return self._command(*args, timeout=timeout, allow_unavailable=allow_unavailable)

    def _command(self, *args, timeout, allow_unavailable=False):
        if self.failed.is_set() or self._closing.is_set():
            raise AppError(_("Lost communication with the player."))
        ready, response = threading.Event(), {}
        with self._lock:
            self._sequence += 1
            request_id = self._sequence
            self._pending[request_id] = (ready, response)
        deadline = time.monotonic() + timeout
        try:
            message = (json.dumps({"command": list(args), "request_id": request_id}) + "\n").encode()
            if len(message) > 65536:
                raise AppError(_("Playback command too large."))
            offset = 0
            while offset < len(message):
                if time.monotonic() >= deadline:
                    raise AppError(_("mpv did not answer the command in time."))
                count = self._transport.write(message[offset:], timeout=max(0, deadline - time.monotonic()))
                if count:
                    offset += count
                else:
                    time.sleep(0.01)
            if not ready.wait(max(0, deadline - time.monotonic())):
                emit("ipc.timeout", request_id=request_id)
                raise AppError(_("mpv did not answer the command in time."))
            if (allow_unavailable and args and args[0] == "get_property"
                    and response.get("error") == "property unavailable"):
                return None
            if response.get("error") != "success":
                emit("ipc.rejected", request_id=request_id)
                raise AppError(_("mpv rejected a command or disconnected."))
            return response.get("data")
        except OSError:
            self.failed.set()
            raise AppError(_("Lost communication with the player.")) from None
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def play(self, target):
        self.command("stop")
        # Barrier: all messages already read before the synchronous command response.
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self.command("set_property", "pause", False)
        if str(target).endswith(".mpd"):
            # A DASH manifest is made of segment URLs. Only manifests written by stream_target reach here,
            # after validate_dash checked every reference; the per-file option reverts when the file ends.
            self.command("loadfile", str(target), "replace", -1, "access-references=yes")
        else:
            self.command("loadfile", str(target), "replace")

    def stage(self, target):
        """Append one validated target so mpv can open it before the current EOF."""
        self.clear_next()
        if str(target).endswith(".mpd"):
            self.command("loadfile", str(target), "append", -1, "access-references=yes")
        else:
            self.command("loadfile", str(target), "append")

    def clear_next(self):
        self.command("playlist-clear")

    def close(self):
        self._closing.set()
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            if self._job:
                self._job.close()
            if self.thread:
                self.thread.join(timeout=2)
            self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
