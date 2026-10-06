"""Capture only mpv's rendered audio with Windows process loopback (Windows 10 2004+).

Read-only tap for the visualizer: it never touches mpv's output path. Samples
stay in a small in-memory ring and are never written anywhere.
"""
import ctypes
from ctypes import wintypes
import threading
import uuid

from ..diagnostics import emit, failure

RATE = 48000
CHANNELS = 2
FRAME_BYTES = 4 * CHANNELS  # float32 stereo
RING_FRAMES = 4096
POLL_SECONDS = 0.01

HRESULT = ctypes.c_long
S_OK, E_NOINTERFACE = 0, -2147467262
VT_BLOB = 65
CLSCTX_ALL = 23
LOOPBACK_TYPE_PROCESS = 1
INCLUDE_TARGET_PROCESS_TREE = 0
STREAMFLAGS = 0x00020000 | 0x80000000 | 0x08000000  # LOOPBACK | AUTOCONVERTPCM | SRC_DEFAULT_QUALITY
BUFFERFLAGS_SILENT = 0x2
WAVE_FORMAT_IEEE_FLOAT = 3


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        raw = uuid.UUID(text).bytes_le
        return cls.from_buffer_copy(raw)


IID_IUnknown = GUID.parse("00000000-0000-0000-C000-000000000046")
IID_IAgileObject = GUID.parse("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
IID_CompletionHandler = GUID.parse("41D949AB-9862-444A-80F6-C261334DA5EB")
IID_IAudioClient = GUID.parse("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAudioCaptureClient = GUID.parse("C8ADBD64-E71E-48a0-A4DE-185C395CD317")


class LoopbackParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("TargetProcessId", wintypes.DWORD),
                ("ProcessLoopbackMode", ctypes.c_int)]


class Blob(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", ctypes.c_void_p)]


class PropVariant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("reserved", ctypes.c_ushort * 3), ("blob", Blob)]


class WaveFormat(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


def _method(pointer, index, *argtypes):
    """Bind slot `index` of a COM vtable; failures raise OSError with the HRESULT."""
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    function = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)(table[index])

    def call(*args):
        result = function(pointer, *args)
        if result < 0:
            raise OSError(f"COM 0x{result & 0xFFFFFFFF:08X}")
        return result
    return call


def _release(pointer):
    if pointer:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])(pointer)


class _CompletionHandler:
    """Minimal agile IActivateAudioInterfaceCompletionHandler built from ctypes callbacks."""

    def __init__(self):
        self.done = threading.Event()
        self.operation = None
        query = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
        count = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)
        completed = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, ctypes.c_void_p)
        self._callbacks = (query(self._query), count(lambda _: 1), count(lambda _: 1), completed(self._completed))
        self._table = (ctypes.c_void_p * 4)(*(ctypes.cast(c, ctypes.c_void_p) for c in self._callbacks))
        self._object = ctypes.pointer(ctypes.c_void_p(ctypes.addressof(self._table)))

    @property
    def pointer(self):
        return ctypes.cast(self._object, ctypes.c_void_p)

    def _query(self, this, iid, out):
        if bytes(iid.contents) in (bytes(IID_IUnknown), bytes(IID_IAgileObject), bytes(IID_CompletionHandler)):
            out[0] = this
            return S_OK
        out[0] = None
        return E_NOINTERFACE

    def _completed(self, this, operation):
        self.operation = operation
        self.done.set()
        return S_OK


class ProcessTap:
    """Keeps the latest RING_FRAMES of mpv's output as interleaved float32 stereo."""

    def __init__(self, pid):
        self.pid = int(pid)
        self.rate = RATE
        self._ring = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.failed = threading.Event()
        self._thread = threading.Thread(target=self._run, name="noneto-tap", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
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
        ole = ctypes.WinDLL("ole32")
        ole.CoInitializeEx(None, 0)  # MTA: the completion arrives on a worker thread
        client = capture = None
        try:
            client = self._activate()
            fmt = WaveFormat(WAVE_FORMAT_IEEE_FLOAT, CHANNELS, RATE, RATE * FRAME_BYTES, FRAME_BYTES, 32, 0)
            _method(client, 3, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong,
                    ctypes.POINTER(WaveFormat), ctypes.c_void_p)(0, STREAMFLAGS, 2_000_000, 0, ctypes.byref(fmt), None)
            service = ctypes.c_void_p()
            _method(client, 14, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(IID_IAudioCaptureClient), ctypes.byref(service))
            capture = service.value
            _method(client, 10)()
            emit("visualizer.tap_started")
            get_buffer = _method(capture, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32),
                                 ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p)
            release = _method(capture, 4, ctypes.c_uint32)
            next_size = _method(capture, 5, ctypes.POINTER(ctypes.c_uint32))
            data, frames, flags, pending = ctypes.c_void_p(), ctypes.c_uint32(), wintypes.DWORD(), ctypes.c_uint32()
            while not self._stop.wait(POLL_SECONDS):
                next_size(ctypes.byref(pending))
                while pending.value:
                    get_buffer(ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags), None, None)
                    size = frames.value * FRAME_BYTES
                    silent = flags.value & BUFFERFLAGS_SILENT or not data.value
                    self._push(bytes(size) if silent else ctypes.string_at(data.value, size))
                    release(frames.value)
                    next_size(ctypes.byref(pending))
            _method(client, 11)()
        except OSError as error:
            self.failed.set()
            failure("visualizer.tap_failed", error)
        finally:
            _release(capture)
            _release(client)
            ole.CoUninitialize()

    def _activate(self):
        params = LoopbackParams(LOOPBACK_TYPE_PROCESS, self.pid, INCLUDE_TARGET_PROCESS_TREE)
        variant = PropVariant(VT_BLOB)
        variant.blob = Blob(ctypes.sizeof(params), ctypes.cast(ctypes.byref(params), ctypes.c_void_p))
        handler = _CompletionHandler()
        operation = ctypes.c_void_p()
        activate = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
        activate.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID), ctypes.POINTER(PropVariant),
                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        activate.restype = HRESULT
        result = activate("VAD\\Process_Loopback", ctypes.byref(IID_IAudioClient), ctypes.byref(variant),
                          handler.pointer, ctypes.byref(operation))
        if result < 0:
            raise OSError(f"COM 0x{result & 0xFFFFFFFF:08X}")
        try:
            if not handler.done.wait(5):
                raise OSError("activation timed out")
            status, client = HRESULT(), ctypes.c_void_p()
            _method(operation.value, 3, ctypes.POINTER(HRESULT), ctypes.POINTER(ctypes.c_void_p))(
                ctypes.byref(status), ctypes.byref(client))
            if status.value < 0:
                raise OSError(f"COM 0x{status.value & 0xFFFFFFFF:08X}")
            return client.value
        finally:
            _release(operation.value)
