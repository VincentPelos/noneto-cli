"""Download the pinned mpv build for Windows, verify its SHA-256 and extract it for Noneto CLI only."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import requests

from ..errors import AppError
from ..i18n import _

# shinchiro/mpv-winbuild-cmake release 20260927 (mpv a1bf4b6559). Change both together.
MPV_URL = ("https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20260927/"
           "mpv-x86_64-20260927-git-a1bf4b6559.7z")
MPV_SHA256 = "04852d5e37730dfa74dec8f88d7cc80b8f756beb52c354c0a3753d3eb2c030e4"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024


def mpv_dir():
    return Path(os.environ["LOCALAPPDATA"]) / "Noneto" / "mpv"


def install_mpv():
    """Return the installed mpv.exe, downloading it first when missing. Never uses mpv from PATH."""
    executable = mpv_dir() / "mpv.exe"
    if executable.is_file():
        return executable
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if not tar.is_file():
        raise AppError(_("Falta tar.exe de Windows para extraer mpv."))
    with tempfile.TemporaryDirectory(prefix="noneto-mpv-") as work:
        archive = Path(work) / "mpv.7z"
        digest, size = hashlib.sha256(), 0
        with requests.Session() as http:
            http.trust_env = False
            # GitHub redirects release assets to its CDN; the hash below is what authenticates the file.
            with http.get(MPV_URL, stream=True, timeout=(10, 60), verify=True) as response:
                if response.status_code != 200:
                    raise AppError(_("No se pudo descargar mpv (HTTP {status}).", status=response.status_code))
                with archive.open("wb") as output:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            raise AppError(_("La descarga de mpv es demasiado grande."))
                        digest.update(chunk)
                        output.write(chunk)
        if digest.hexdigest() != MPV_SHA256:
            raise AppError(_("El hash SHA-256 de mpv no coincide; no se instalo."))
        staging = Path(work) / "mpv"
        staging.mkdir()
        # bsdtar refuses absolute paths and ".." entries by default.
        result = subprocess.run([str(tar), "-xf", str(archive), "-C", str(staging)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode != 0 or not (staging / "mpv.exe").is_file():
            raise AppError(_("El archivo descargado no contiene mpv.exe."))
        target = mpv_dir()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)  # leftover of an interrupted install without mpv.exe
        shutil.move(str(staging), str(target))
    return executable
