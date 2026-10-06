import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import wave

from noneto.audio.player import Player
from noneto.errors import AppError


CHILD = """
import os, sys
from pathlib import Path
from noneto.audio.player import Player
player = Player(extra_args=['--ao=null'])
player.play(sys.argv[1])
while player.events.get(timeout=5).get('event') != 'file-loaded':
    pass
Path(sys.argv[2]).write_text(str(player.process.pid))
mode = sys.stdin.readline().strip()
if mode == 'normal':
    player.close()
else:
    os._exit(0)
"""


class PlayerLifetimeTests(unittest.TestCase):
    def check_owner_exit(self, mode):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'silence.wav'
            ready = Path(directory) / 'ready.txt'
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * 8000 * 60)
            owner = subprocess.Popen([sys.executable, '-c', CHILD, str(path), str(ready)],
                                     cwd=Path(__file__).resolve().parents[1],
                                     stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    self.assertIsNone(owner.poll(), 'Player owner exited before loading audio')
                    time.sleep(0.025)
                self.assertTrue(ready.exists(), 'Audio did not load')
                handle = kernel.OpenProcess(0x100001, False, int(ready.read_text()))  # SYNCHRONIZE | TERMINATE
                self.assertTrue(handle)
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258)  # still playing
                if mode == 'kill':
                    owner.terminate()  # bypass Python handlers/finally, as a forced window closure can
                else:
                    owner.stdin.write((mode + '\n').encode())
                    owner.stdin.flush()
                owner.wait(timeout=10)
                self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0,
                                 'mpv survived its owning application')
            finally:
                if owner.poll() is None:
                    owner.kill()
                owner.wait(timeout=5)
                owner.stdin.close()
                if handle:
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        kernel.TerminateProcess(handle, 1)
                        kernel.WaitForSingleObject(handle, 5000)
                    kernel.CloseHandle(handle)

    def test_forced_parent_termination_stops_playing_mpv(self):
        self.check_owner_exit('kill')

    def test_exit_without_finally_stops_playing_mpv(self):
        self.check_owner_exit('abrupt')

    def test_normal_close_stops_playing_mpv(self):
        self.check_owner_exit('normal')

    def test_attachment_failure_closes_player_instead_of_playing_unprotected(self):
        processes = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            processes.append(process)
            return process
        with patch('noneto.audio.player.subprocess.Popen', side_effect=spawn), \
                patch('noneto.audio.player.PlayerJob.attach', side_effect=AppError('attachment failed')):
            with self.assertRaises(AppError):
                Player(extra_args=['--ao=null'])
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
