"""Native secure session storage: Windows DPAPI, Linux Secret Service."""
import json
import os
from pathlib import Path
import tempfile
from ..errors import AppError
from ..i18n import _
from ..platform.paths import data_dir

MAX_SESSION_BYTES = 64 * 1024


class SessionPersistence:
    """Called only by the SDK owner, after login has persisted the initial state."""
    def __init__(self, session):
        self.session = session
        self._saved = self._snapshot()

    def _snapshot(self):
        session = self.session
        return (session.token_type, session.access_token, session.refresh_token,
                session.is_pkce, session.expiry_time)

    def __call__(self):
        current = self._snapshot()
        if current != self._saved:
            save_session(self.session)
            self._saved = current  # Failed writes must be retried, never remembered.


def protect(data: bytes, decrypt: bool = False) -> bytes:
    from .dpapi import protect as windows_protect
    return windows_protect(data, decrypt)


def _keyring():
    # Select only the native secure backend: never honor plaintext third-party fallbacks.
    try:
        from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise RuntimeError("Unavailable keyring")
        return backend
    except Exception:
        raise AppError(_("No secure credential store is available. Unlock your system keyring.")) from None


def has_session():
    if os.name == "nt":
        return session_path().exists()
    return _keyring().get_password("Noneto CLI", "session") is not None


def delete_session():
    if os.name == "nt":
        session_path().unlink(missing_ok=True)
    else:
        backend = _keyring()
        if backend.get_password("Noneto CLI", "session") is not None:
            backend.delete_password("Noneto CLI", "session")


def session_path() -> Path:
    return data_dir() / "session.bin"


def save_session(session, path=None):
    path = Path(path) if path is not None else session_path()
    data = dict(token_type=session.token_type, access_token=session.access_token,
                refresh_token=session.refresh_token, is_pkce=session.is_pkce,
                expiry_time=session.expiry_time.isoformat() if session.expiry_time else None)
    serialized = json.dumps(data)
    if len(serialized.encode("utf-8")) > MAX_SESSION_BYTES:
        raise ValueError("Invalid stored session size")
    if os.name != "nt":
        _keyring().set_password("Noneto CLI", "session", serialized)
        return
    encrypted = protect(serialized.encode("utf-8"))
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
    if os.name == "nt":
        with path.open("rb") as source:
            encrypted = source.read(MAX_SESSION_BYTES + 1)
        if len(encrypted) > MAX_SESSION_BYTES:
            raise ValueError("Invalid stored session size")
        serialized = protect(encrypted, decrypt=True)
    else:
        serialized = _keyring().get_password("Noneto CLI", "session")
        if serialized is None:
            raise FileNotFoundError("No stored session")
        if len(serialized.encode("utf-8")) > MAX_SESSION_BYTES:
            raise ValueError("Invalid stored session size")
    data = json.loads(serialized)
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
