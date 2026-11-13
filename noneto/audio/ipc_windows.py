"""Overlapped Windows pipe: readiness events, bounded writes and verified PID."""
import _winapi
import ctypes
from ctypes import wintypes
import msvcrt
import os
import math
import subprocess
import time
import uuid

from ..errors import AppError
from ..i18n import _


class WindowsIPC:
    def __init__(self):
        self.name = rf"\\.\pipe\noneto-{uuid.uuid4().hex}"
        self.argument = f"--input-ipc-server={self.name}"
        self.spawn_options = {"creationflags": subprocess.CREATE_NO_WINDOW}
        self.pipe = None
        self._read = None

    def connect(self, process):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AppError(_("mpv could not start."))
            try:
                handle = _winapi.CreateFile(self.name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                                            0, 0, _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
                self.pipe = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY), "r+b", buffering=0)
                break
            except OSError:
                time.sleep(0.05)
        if self.pipe is None:
            raise AppError(_("mpv did not respond within 10 seconds."))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handle = msvcrt.get_osfhandle(self.pipe.fileno())
        get_pid = kernel.GetNamedPipeServerProcessId
        get_pid.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
        get_pid.restype = wintypes.BOOL
        pid = wintypes.ULONG()
        if not get_pid(self.handle, ctypes.byref(pid)) or pid.value != process.pid:
            raise AppError(_("Could not verify the player channel."))
        set_state = kernel.SetNamedPipeHandleState
        set_state.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
        set_state.restype = wintypes.BOOL
        mode = wintypes.DWORD(0)  # Byte mode; OVERLAPPED handles make waits interruptible.
        if not set_state(self.handle, ctypes.byref(mode), None, None):
            raise AppError(_("Could not configure the player channel."))

    def read(self, size):
        if self.pipe.closed:
            self._cancel_read()
            raise OSError("IPC disconnected")
        if self._read is None:
            self._read, _error = _winapi.ReadFile(self.handle, size, overlapped=True)
        # Data wakes immediately. The timeout only lets shutdown inspect its flag.
        if _winapi.WaitForSingleObject(self._read.event, 100) == _winapi.WAIT_TIMEOUT:
            return None
        pending, self._read = self._read, None
        count, error = pending.GetOverlappedResult(True)
        if error:
            raise OSError("IPC disconnected")
        return bytes(pending.getbuffer())[:count]

    def write(self, data, timeout=5):
        pending, _error = _winapi.WriteFile(self.handle, data, overlapped=True)
        try:
            if _winapi.WaitForSingleObject(pending.event, max(0, math.ceil(timeout * 1000))) != _winapi.WAIT_OBJECT_0:
                raise OSError("IPC write timed out")
        except BaseException:
            pending.cancel()
            pending.GetOverlappedResult(True)
            raise
        count, error = pending.GetOverlappedResult(True)
        if error:
            raise OSError("IPC disconnected")
        return count

    def _cancel_read(self):
        if self._read is not None:
            pending, self._read = self._read, None
            try:
                pending.cancel()
                pending.GetOverlappedResult(True)
            except OSError:
                pass

    def close(self):
        self._cancel_read()
        if self.pipe is not None:
            self.pipe.close()
