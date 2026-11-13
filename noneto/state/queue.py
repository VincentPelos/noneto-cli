"""Bounded, atomic queue storage. No SDK objects, credentials or stream URLs."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

from ..platform.paths import state_dir
from ..security import terminal_text

MAX_BYTES = 8 * 1024 * 1024
MAX_TRACKS = 10000


@dataclass(frozen=True)
class SavedTrack:
    id: int
    name: str
    artist: object
    album: object
    duration: float


def _number(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid queue number")
    return value


def _text(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("Invalid queue metadata")
    return terminal_text(value)


class QueueStore:
    def __init__(self, account, path=None):
        self.account = str(account)
        self.path = Path(path) if path is not None else state_dir() / "queue.json"
        self._cached_tracks = None
        self._cached_order = None
        self._encoded_tracks = b"[]"
        self._encoded_order = b"[]"

    def load(self):
        try:
            with self.path.open("rb") as source:
                raw = source.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Queue too large")
            data = json.loads(raw)
            if (not isinstance(data, dict) or type(data.get("version")) is not int
                    or data.get("version") != 1 or data.get("account") != self.account):
                return None
            entries = data["tracks"]
            if not isinstance(entries, list) or len(entries) > MAX_TRACKS:
                raise ValueError("Invalid queue size")
            tracks = []
            for entry in entries:
                identifier = entry["id"]
                if type(identifier) is not int or not 0 < identifier < 2**63:
                    raise ValueError("Invalid track ID")
                tracks.append(SavedTrack(identifier, _text(entry["name"]),
                                         SimpleNamespace(name=_text(entry["artist"])),
                                         SimpleNamespace(name=_text(entry["album"])),
                                         _number(entry["duration"], 86400)))
            order = data["order"]
            if (not isinstance(order, list) or any(type(i) is not int for i in order)
                    or sorted(order) != list(range(len(tracks)))):
                raise ValueError("Invalid queue order")
            index = data["index"]
            if type(index) is not int or not -1 <= index < len(tracks):
                raise ValueError("Invalid queue index")
            if type(data["shuffle"]) is not bool or data["repeat"] not in ("off", "all", "one"):
                raise ValueError("Invalid queue mode")
            _number(data["position"], 86400)
            if type(data["volume"]) is not int:
                raise ValueError("Invalid queue volume")
            _number(data["volume"], 100)
            return {**data, "tracks": tuple(tracks)}
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return None

    def save(self, state, source_tracks, order):
        # Only immutable tuple snapshots use identity caches. Lists are always encoded.
        if not isinstance(source_tracks, tuple) or source_tracks is not self._cached_tracks:
            self._encoded_tracks = self._encode_tracks(source_tracks)
            self._cached_tracks = source_tracks if isinstance(source_tracks, tuple) else None
        if not isinstance(order, tuple) or order is not self._cached_order:
            self._encoded_order = json.dumps(list(order)).encode("utf-8")
            self._cached_order = order if isinstance(order, tuple) else None
        data = {"version": 1, "account": self.account,
                "index": state.index, "position": _number(state.position, 86400),
                "volume": state.volume, "shuffle": state.shuffle, "repeat": state.repeat}
        header = json.dumps(data, ensure_ascii=False).encode("utf-8")
        encoded = header[:-1] + b', "tracks": ' + self._encoded_tracks + b', "order": ' + self._encoded_order + b"}"
        if len(encoded) > MAX_BYTES:
            raise ValueError("Queue too large")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".queue-", suffix=".tmp",
                                             delete=False) as output:
                temp = Path(output.name)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(self.path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    @staticmethod
    def _encode_tracks(source_tracks):
        def text(value):
            return terminal_text(str(value or ""))[:300]
        if len(source_tracks) > MAX_TRACKS:
            raise ValueError("Queue too large")
        entries = []
        for track in source_tracks:
            identifier = getattr(track, "id", None)
            if type(identifier) is not int or not 0 < identifier < 2**63:
                raise ValueError("Invalid track ID")
            entries.append({"id": identifier, "name": text(getattr(track, "name", "")),
                            "artist": text(getattr(getattr(track, "artist", None), "name", "")),
                            "album": text(getattr(getattr(track, "album", None), "name", "")),
                            "duration": _number(getattr(track, "duration", 0) or 0, 86400)})
        return json.dumps(entries, ensure_ascii=False).encode("utf-8")
