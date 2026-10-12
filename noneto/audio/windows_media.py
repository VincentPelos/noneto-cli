"""Windows SMTC publisher. Owns a hidden desktop window, never an audio device."""
import ctypes
from ctypes import wintypes
from datetime import timedelta

from ..diagnostics import failure
from .windows_identity import APP_ID, register_identity


class WindowsMedia:
    def __init__(self, on_button, on_seek):
        from winrt.runtime import init_apartment, ApartmentType
        from winrt.windows.media.interop import get_for_window
        from winrt.windows.media import MediaPlaybackType

        init_apartment(ApartmentType.MULTI_THREADED)
        self._apartment = True
        self.hwnd = None
        self.controls = None
        self.tokens = []
        self._stream = None
        try:
            try:
                register_identity()
            except Exception as error:
                failure("media.identity_unavailable", error)
            shell = ctypes.WinDLL("shell32")
            identify = shell.SetCurrentProcessExplicitAppUserModelID
            identify.argtypes = [wintypes.LPCWSTR]
            identify.restype = ctypes.c_long
            if identify(APP_ID) < 0:
                raise OSError("Cannot identify media session")
            self.user = ctypes.WinDLL("user32", use_last_error=True)
            create = self.user.CreateWindowExW
            create.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                               wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                               wintypes.HINSTANCE, ctypes.c_void_p]
            create.restype = wintypes.HWND
            self.user.DestroyWindow.argtypes = [wintypes.HWND]
            self.user.DestroyWindow.restype = wintypes.BOOL
            self.user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                              wintypes.UINT, wintypes.UINT, wintypes.UINT]
            self.user.PeekMessageW.restype = wintypes.BOOL
            self.user.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            self.user.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            self.user.DispatchMessageW.restype = ctypes.c_ssize_t
            self.hwnd = create(0, "STATIC", "Noneto CLI", 0x80000000, 0, 0, 0, 0,
                               None, None, None, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.controls = get_for_window(self.hwnd)
            self.controls.is_enabled = False
            self.controls.display_updater.type = MediaPlaybackType.MUSIC
            self.tokens = [
                (self.controls.remove_button_pressed,
                 self.controls.add_button_pressed(lambda sender, args: on_button(args.button.name))),
                (self.controls.remove_playback_position_change_requested,
                 self.controls.add_playback_position_change_requested(
                     lambda sender, args: on_seek(args.requested_playback_position.total_seconds()))),
            ]
        except BaseException:
            self.close()
            raise

    def pump(self):
        message = wintypes.MSG()
        while self.user.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            self.user.TranslateMessage(ctypes.byref(message))
            self.user.DispatchMessageW(ctypes.byref(message))

    def metadata(self, title, artist, album):
        from winrt.windows.media import MediaPlaybackType
        updater = self.controls.display_updater
        updater.clear_all()
        updater.type = MediaPlaybackType.MUSIC
        updater.music_properties.title = title
        updater.music_properties.artist = artist
        updater.music_properties.album_title = album
        updater.update()
        self._close_stream()

    def thumbnail(self, data):
        from winrt.windows.storage.streams import (DataWriter, InMemoryRandomAccessStream,
                                                   RandomAccessStreamReference)
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        try:
            writer.write_bytes(data)
            # store_async is completed by Windows, not by the Textual event loop.
            operation = writer.store_async()
            from winrt.windows.foundation import AsyncStatus
            import time
            deadline = time.monotonic() + 2
            while operation.status == AsyncStatus.STARTED:
                if time.monotonic() >= deadline:
                    operation.cancel()
                    raise TimeoutError("Media artwork write timed out")
                time.sleep(0.01)
            operation.get_results()
            writer.detach_stream()
            stream.seek(0)
            self.controls.display_updater.thumbnail = RandomAccessStreamReference.create_from_stream(stream)
            self.controls.display_updater.update()
        except BaseException:
            stream.close()
            raise
        finally:
            writer.close()
        self._close_stream()
        self._stream = stream

    def update(self, status, position, duration, previous, next_track):
        from winrt.windows.media import MediaPlaybackStatus, SystemMediaTransportControlsTimelineProperties
        active = status in ("playing", "paused", "loading")
        controls = self.controls
        controls.playback_status = {"playing": MediaPlaybackStatus.PLAYING,
                                    "paused": MediaPlaybackStatus.PAUSED,
                                    "loading": MediaPlaybackStatus.CHANGING}.get(status, MediaPlaybackStatus.STOPPED)
        controls.is_play_enabled = active
        controls.is_pause_enabled = active
        controls.is_stop_enabled = active
        controls.is_previous_enabled = active and previous
        controls.is_next_enabled = active and next_track
        timeline = SystemMediaTransportControlsTimelineProperties()
        timeline.start_time = timeline.min_seek_time = timedelta(0)
        timeline.end_time = timeline.max_seek_time = timedelta(seconds=duration)
        timeline.position = timedelta(seconds=position)
        controls.update_timeline_properties(timeline)
        controls.is_enabled = active

    def _close_stream(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def close(self):
        from winrt.runtime import uninit_apartment
        try:
            if self.controls is not None:
                for remove, token in self.tokens:
                    remove(token)
                self.tokens = []
                self.controls.is_enabled = False
                self.controls.display_updater.clear_all()
                self.controls.display_updater.update()
        finally:
            self.controls = None
            try:
                self._close_stream()
            finally:
                if self.hwnd:
                    self.user.DestroyWindow(self.hwnd)
                    self.hwnd = None
                if self._apartment:
                    uninit_apartment()
                    self._apartment = False
