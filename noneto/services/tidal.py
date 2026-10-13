"""Tidal HTTP and catalog boundary. No terminal or player imports."""
import logging
import re
import uuid
import time
import io
import warnings

import requests
import tidalapi
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..errors import AppError
from ..i18n import _
from ..security import https_url
from ..diagnostics import emit, failure, request_target

API_HOSTS = frozenset({"auth.tidal.com", "api.tidal.com", "openapi.tidal.com"})
# Tidal closed idle connections after 180-300 s (measured 2026-10-06). A request sent on a
# socket closing at that moment fails with ConnectionError and read=0 never retries it, so
# after a song-length pause start from fresh connections instead of reusing pooled ones.
IDLE_RESET_SECONDS = 60


class TimedSession(requests.Session):
    def __init__(self):
        super().__init__()
        self._last_used = None
        # Do not implicitly load .netrc credentials, environment proxies or CA overrides.
        self.trust_env = False
        retry = Retry(total=2, connect=2, read=0, status=2, redirect=0,
                      allowed_methods=frozenset({"GET", "HEAD"}),
                      status_forcelist=(502, 503, 504), backoff_factor=0.3,
                      respect_retry_after_header=False)
        self.mount("https://", HTTPAdapter(max_retries=retry))

    def request(self, method, url, **kwargs):
        https_url(url, hosts=API_HOSTS)
        kwargs.setdefault("timeout", (10, 30))
        kwargs["allow_redirects"] = False  # OAuth POST bodies must never follow 307/308.
        kwargs["verify"] = True
        target = request_target(url)
        started = time.monotonic()
        if self._last_used is not None and started - self._last_used > IDLE_RESET_SECONDS:
            for adapter in self.adapters.values():
                adapter.poolmanager.clear()
            emit("http.pool_reset", elapsed_ms=round((started - self._last_used) * 1000))
        emit("http.started", method=method, **target)
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException as error:
            failure("http.failed", error, method=method, **target,
                    elapsed_ms=round((time.monotonic() - started) * 1000))
            raise
        finally:
            self._last_used = time.monotonic()
        emit("http.completed", method=method, **target, status=response.status_code,
             elapsed_ms=round((time.monotonic() - started) * 1000))
        if 300 <= response.status_code < 400:
            response.close()
            raise AppError(_("TIDAL returned a disallowed redirect."))
        return response


def create_session(quality):
    for name in ("tidalapi", "urllib3", "requests"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)
    session = tidalapi.Session(tidalapi.Config(quality=tidalapi.Quality(quality)))
    session.request_session.close()
    session.request_session = TimedSession()
    return session


def all_pages(fetch, limit=50, max_pages=1000):
    if limit < 1 or max_pages < 1:
        raise ValueError("Pagination limits must be positive")
    result = []
    for _page in range(max_pages):
        page = fetch(limit=limit, offset=len(result))
        if len(page) > limit:
            raise AppError(_("TIDAL returned a larger page than requested."))
        result.extend(page)
        if len(page) < limit:
            return result
    raise AppError(_("Pagination exceeded the safety limit."))


def playlist_id(value):
    match = re.fullmatch(
        r"(?:https://(?:www\.|listen\.)?tidal\.com/(?:browse/)?playlist/)?"
        r"([0-9a-fA-F-]{36})/?(?:\?[^\s]*)?", value.strip())
    if not match:
        raise ValueError("Use a TIDAL playlist link or its UUID.")
    return str(uuid.UUID(match.group(1)))


class Catalog:
    def __init__(self, session):
        self.session = session

    def playlists(self):
        return all_pages(self.session.user.playlist_and_favorite_playlists)

    def playlist(self, value):
        return self.session.playlist(playlist_id(value))

    def tracks(self, playlist):
        return all_pages(playlist.tracks, limit=100)

    def search(self, query, offset=0):
        return self._search(query, offset, tidalapi.Track, "tracks")

    def search_playlists(self, query, offset=0):
        return self._search(query, offset, tidalapi.Playlist, "playlists")

    def _search(self, query, offset, model, key):
        query = query.strip()
        if not query or len(query) > 200:
            raise AppError(_("Type a search between 1 and 200 characters."))
        return self.session.search(query, models=[model], limit=50, offset=offset)[key]

    def lyrics(self, track):
        data = track.lyrics()
        return (data.text or "", data.subtitles or "", bool(data.right_to_left))

    def cover(self, track):
        data = self.media_cover(track, 80)
        if data is None:
            return None
        with Image.open(io.BytesIO(data)) as picture:
            pixels = picture.convert("RGB").resize((12, 12), Image.Resampling.BILINEAR)
            return tuple(tuple(pixels.getpixel((x, y)) for x in range(12)) for y in range(12))

    def media_cover(self, track, resolution=320):
        album = getattr(track, "album", None)
        if album is None or not getattr(album, "cover", None):
            return None
        url = https_url(album.image(resolution), hosts={"resources.tidal.com"})
        chunks, size = [], 0
        with requests.Session() as connection:
            connection.trust_env = False
            with connection.get(url, stream=True, timeout=(5, 15), allow_redirects=False,
                                verify=True) as response:
                if response.status_code != 200 or not response.headers.get(
                        "Content-Type", "").lower().startswith("image/jpeg"):
                    raise AppError(_("Cover art not available."))
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise AppError(_("Cover art too large."))
                    chunks.append(chunk)
        data = b"".join(chunks)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as picture:
                if picture.format != "JPEG" or max(picture.size) > resolution * 2:
                    raise AppError(_("Invalid cover art."))
                picture.load()
        return data

    def editable(self, playlist):
        return (str(getattr(getattr(playlist, "creator", None), "id", ""))
                == str(self.session.user.id) and callable(getattr(playlist, "add", None)))

    def create(self, title):
        title = title.strip()
        if not title or len(title) > 100:
            raise AppError(_("The name must be between 1 and 100 characters."))
        return self.session.user.create_playlist(title, "")

    def add(self, playlist, track):
        # Refresh ownership and ETag immediately before writing.
        current = self.playlist(str(playlist.id))
        if not self.editable(current):
            raise AppError(_("You can only add songs to your own playlists."))
        return current.add([str(track.id)], allow_duplicates=False)
