# Third-party software in the Windows application

Noneto CLI is licensed under MIT. This does not replace the licenses of its dependencies.
The installed `_internal` directory includes dependency metadata and upstream license files.
`PYTHON-LICENSE.txt` covers the included Python runtime; `LICENSE` covers Noneto itself.

The unmodified LGPL-3.0-or-later packages `tidalapi` and `textual-image` are installed as
replaceable Python source files in `_internal/tidalapi` and `_internal/textual_image`.
Their license texts and notices are included in the corresponding `.dist-info` directories.
The source of Noneto is included in `_internal/source/noneto`, and the exact stable revision
and packaging adaptations are recorded in `BUILD-INFO.json`.

mpv is a separate process. Setup downloads the pinned, unmodified upstream distribution
directly from shinchiro's mpv-player-windows archive on SourceForge over HTTPS and verifies its SHA-256 before
installation. Its upstream license and documentation files remain alongside `mpv.exe`.
The installer does not contain or redistribute the mpv archive.

Sources and upstream notices:

- Noneto: https://github.com/VincentPelos/noneto-cli
- Python: https://www.python.org/downloads/source/
- tidalapi: https://github.com/EbbLabs/python-tidal/tree/v0.8.11
- textual-image: https://github.com/lnqs/textual-image
- mpv distribution and build recipes: https://github.com/shinchiro/mpv-winbuild-cmake
- PyInstaller and bootloader exception: https://pyinstaller.org/en/stable/license.html
