"""Validated JPEG transfers, reusable connection and bounded in-memory LRU."""
from collections import OrderedDict
import io
import time
import warnings

from PIL import Image
import requests

from ..errors import AppError
from ..i18n import _
from ..security import https_url

# Same rule as the API session (services/tidal.py): TIDAL drops idle connections after a few
# minutes, and a request on one that is closing fails, so start fresh after a pause.
IDLE_RESET_SECONDS = 60
# 640 px keeps the cover sharp when the Lyrics tab shows it large; 320 is TIDAL's next size down.
COVER_SIZES = (320, 640)
COVER_SIZE = 640


def cover_image(data):
    """Decoded off the UI thread; the TUI scales it to each cover's size."""
    if data is None:
        return None
    with Image.open(io.BytesIO(data)) as picture:
        return picture.convert("RGB")


class CoverDownloader:
    """Confined to one media worker. Cache holds only validated JPEG bytes."""
    def __init__(self, max_bytes=8 * 1024 * 1024, max_entries=64):
        self._connection = None
        self._last_used = 0.0
        self._cache = OrderedDict()
        self._bytes = 0
        self._max_bytes, self._max_entries = max_bytes, max_entries

    def fetch(self, request):
        if request is None:
            return None
        url, resolution = request
        https_url(url, hosts={"resources.tidal.com"})
        if type(resolution) is not int or resolution not in COVER_SIZES:
            raise ValueError("Invalid cover resolution")
        key = (url, resolution)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if self._connection is not None and time.monotonic() - self._last_used > IDLE_RESET_SECONDS:
            self._connection.close()
            self._connection = None
        if self._connection is None:
            self._connection = requests.Session()
            self._connection.trust_env = False
        chunks, size = [], 0
        try:
            with self._connection.get(url, stream=True, timeout=(5, 15), allow_redirects=False,
                                      verify=True) as response:
                if response.status_code != 200 or not response.headers.get("Content-Type", "").lower().startswith("image/jpeg"):
                    raise AppError(_("Cover art not available."))
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise AppError(_("Cover art too large."))
                    chunks.append(chunk)
        finally:
            self._last_used = time.monotonic()
        data = b"".join(chunks)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != "JPEG" or max(picture.size) > resolution * 2:
                    raise AppError(_("Invalid cover art."))
                picture.load()
        if len(data) <= self._max_bytes and self._max_entries > 0:
            self._cache[key] = data
            self._bytes += len(data)
            while self._bytes > self._max_bytes or len(self._cache) > self._max_entries:
                _key, evicted = self._cache.popitem(last=False)
                self._bytes -= len(evicted)
        return data

    def close(self):
        if self._connection is not None:
            self._connection.close()
        self._cache.clear()
        self._bytes = 0
