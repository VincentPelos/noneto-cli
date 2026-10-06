"""Session storage encrypted with the current Windows user's DPAPI key."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile

MAX_SESSION_BYTES = 64 * 1024


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, decrypt: bool = False) -> bytes:
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def session_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Noneto" / "session.bin"


def save_session(session, path=None):
    path = Path(path) if path is not None else session_path()
    data = dict(token_type=session.token_type, access_token=session.access_token,
                refresh_token=session.refresh_token, is_pkce=session.is_pkce,
                expiry_time=session.expiry_time.isoformat() if session.expiry_time else None)
    encrypted = protect(json.dumps(data).encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".session-", suffix=".tmp",
                                         delete=False) as output:
            temp = Path(output.name)
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def read_session(path=None):
    from datetime import datetime
    path = Path(path) if path is not None else session_path()
    with path.open("rb") as source:
        encrypted = source.read(MAX_SESSION_BYTES + 1)
    if len(encrypted) > MAX_SESSION_BYTES:
        raise ValueError("Invalid stored session size")
    data = json.loads(protect(encrypted, decrypt=True))
    expected = {"token_type", "access_token", "refresh_token", "is_pkce", "expiry_time"}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError("Invalid stored session fields")
    for key in ("token_type", "access_token", "refresh_token"):
        if not isinstance(data[key], str) or not data[key] or len(data[key]) > 16384:
            raise ValueError("Invalid stored token")
    if type(data["is_pkce"]) is not bool:
        raise ValueError("Invalid stored session type")
    if data.get("expiry_time"):
        data["expiry_time"] = datetime.fromisoformat(data["expiry_time"])
    return data
