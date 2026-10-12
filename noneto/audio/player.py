"""mpv JSON IPC transport: bounded messages, acknowledgements and explicit failure."""
import ctypes
from ctypes import wintypes
import json
import msvcrt
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

from ..errors import AppError
from ..i18n import _
from ..diagnostics import emit, failure, mpv_message
from .lifetime import PlayerJob
from .mpv_install import mpv_dir

MAX_FRAME = 1024 * 1024
COMMAND_TIMEOUT = 5


def find_mpv():
    local = mpv_dir() / "mpv.exe"
    if not local.is_file():
        raise AppError(_("Falta mpv. Instalalo con: noneto --install-mpv"))
    return str(local)


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
        pipe_name = rf"\\.\pipe\noneto-{uuid.uuid4().hex}"
        self.process = subprocess.Popen([
            str(Path(executable or find_mpv()).resolve()), *extra_args,
            "--no-config", "--idle=yes", "--no-video", "--audio-display=no",
            "--terminal=no", "--volume=60", "--load-scripts=no", "--ytdl=no",
            "--load-unsafe-playlists=no", "--access-references=no", "--tls-verify=yes",
            "--network-timeout=30",
            f"--media-controls={'yes' if media_controls else 'no'}",
            "--demuxer-lavf-o=protocol_whitelist=[file,https,tls,tcp]",
            f"--input-ipc-server={pipe_name}",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            # mpv is idle: bind its lifetime before IPC can load any audio.
            # Abrupt terminal closure may bypass close()/finally entirely.
            self._job = PlayerJob()
            self._job.attach(self.process)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise AppError(_("mpv no pudo iniciarse."))
                try:
                    self.pipe = open(pipe_name, "r+b", buffering=0)
                    break
                except OSError:
                    time.sleep(0.05)
            if self.pipe is None:
                raise AppError(_("mpv no respondio en 10 segundos."))
            self._configure_pipe()
            self.thread = threading.Thread(target=self._listen, daemon=True, name="mpv-ipc")
            self.thread.start()
            self.command("get_property", "mpv-version")
            self.command("request_log_messages", "warn")
        except BaseException:
            self.close()
            raise

    def _configure_pipe(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = msvcrt.get_osfhandle(self.pipe.fileno())
        get_pid = kernel.GetNamedPipeServerProcessId
        get_pid.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        get_pid.restype = wintypes.BOOL
        pid = wintypes.ULONG()
        if not get_pid(self._handle, ctypes.byref(pid)) or pid.value != self.process.pid:
            raise AppError(_("No se pudo verificar el canal del reproductor."))
        set_state = kernel.SetNamedPipeHandleState
        set_state.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                              ctypes.c_void_p, ctypes.c_void_p]
        set_state.restype = wintypes.BOOL
        mode = wintypes.DWORD(1)  # PIPE_NOWAIT: a hung peer must not block writes forever.
        if not set_state(self._handle, ctypes.byref(mode), None, None):
            raise AppError(_("No se pudo configurar el canal del reproductor."))

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

    def _listen(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        peek.restype = wintypes.BOOL
        buffer = b""
        try:
            while not self._closing.is_set() and self.process.poll() is None:
                available = wintypes.DWORD()
                if not peek(self._handle, None, 0, None, ctypes.byref(available), None):
                    break
                if not available.value:
                    self._closing.wait(0.02)
                    continue
                chunk = self.pipe.read(min(available.value, 65536))
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
            with self._lock:
                for ready, _response in self._pending.values():
                    ready.set()

    def command(self, *args, timeout=COMMAND_TIMEOUT, allow_unavailable=False):
        with self._command_lock:
            return self._command(*args, timeout=timeout, allow_unavailable=allow_unavailable)

    def _command(self, *args, timeout, allow_unavailable=False):
        if self.failed.is_set() or self._closing.is_set():
            raise AppError(_("Se perdio la comunicacion con el reproductor."))
        ready, response = threading.Event(), {}
        with self._lock:
            self._sequence += 1
            request_id = self._sequence
            self._pending[request_id] = (ready, response)
        deadline = time.monotonic() + timeout
        try:
            message = (json.dumps({"command": list(args), "request_id": request_id}) + "\n").encode()
            if len(message) > 65536:
                raise AppError(_("Comando de reproduccion demasiado grande."))
            offset = 0
            while offset < len(message):
                if time.monotonic() >= deadline:
                    raise AppError(_("mpv no respondio al comando a tiempo."))
                try:
                    count = self.pipe.write(message[offset:])
                except OSError as error:
                    if getattr(error, "winerror", None) != 232:  # ERROR_NO_DATA: pipe busy
                        raise
                    count = 0
                if count:
                    offset += count
                else:
                    time.sleep(0.01)
            if not ready.wait(max(0, deadline - time.monotonic())):
                emit("ipc.timeout", request_id=request_id)
                raise AppError(_("mpv no respondio al comando a tiempo."))
            if (allow_unavailable and args and args[0] == "get_property"
                    and response.get("error") == "property unavailable"):
                return None
            if response.get("error") != "success":
                emit("ipc.rejected", request_id=request_id)
                raise AppError(_("mpv rechazo un comando o se desconecto."))
            return response.get("data")
        except OSError:
            self.failed.set()
            raise AppError(_("Se perdio la comunicacion con el reproductor.")) from None
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

    def close(self):
        self._closing.set()
        try:
            if self.process.poll() is None:
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
            if self.pipe:
                self.pipe.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
