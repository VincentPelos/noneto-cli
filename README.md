<h1 align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/noneto-logo-white.png">
    <img alt="Noneto CLI" src="docs/assets/noneto-logo-black.png" width="440">
  </picture>
</h1>

An unofficial TIDAL client for the Windows terminal. Search for songs, play your playlists,
create new ones, and follow lyrics with a cava-style visualizer. Supports mouse and keyboard.
Built with Python 3.12, Textual, [`tidalapi`](https://github.com/EbbLabs/python-tidal), and mpv.
You need a TIDAL account with playback access.

> [!IMPORTANT]
> Noneto CLI is a free, personal, **unofficial** project. It is not affiliated with, associated
> with, or endorsed by TIDAL. TIDAL is a trademark of its respective owners.
>
> Noneto provides a terminal interface and connects your account to TIDAL through the unofficial
> `tidalapi` library. It does not operate TIDAL's service or develop or control its APIs.
> External changes may make features unavailable; future compatibility is not guaranteed.
> Noneto CLI streams music only: it does not download or save music, and it rejects encrypted
> streams rather than bypassing DRM.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/). It creates the `noneto`
command and downloads Python 3.12 if needed.

```powershell
uv tool install git+https://github.com/VincentPelos/noneto-cli
noneto --install-mpv
noneto
```

`--install-mpv` downloads a pinned mpv build, verifies its SHA-256 hash, and installs it in
`%LOCALAPPDATA%\Noneto\mpv`. Noneto CLI uses that build rather than an mpv executable on your PATH.
Installation does not ask for your TIDAL account. If `noneto` is not found, run
`uv tool update-shell` and open a new terminal.

## Update and uninstall

```powershell
uv tool upgrade noneto-cli
uv tool uninstall noneto-cli
Remove-Item -Recurse "$env:LOCALAPPDATA\Noneto"   # optional: session, logs, and mpv
```

## Use

1. On your first launch, a TIDAL sign-in window opens. Sign in and the window closes automatically.
   Later launches restore your session.
2. If the window cannot open because WebView2 is unavailable, Noneto opens your browser.
   TIDAL may finish on an "Oops" page; copy its address with Ctrl+L, Ctrl+C. Noneto detects it.
3. Your first playlist opens.
4. Click a song, or select it and press Enter. Playback continues through the remaining songs.

**Top bar:** **Library ▾** opens your own and favorite playlists, lets you create a playlist,
and refreshes the list. The search field and quality badge sit beside it.
During playback, the badge shows the audio mpv actually decodes, such as `◆ FLAC 24/96`.
Before playback, it shows the requested quality (`Requested: Hi-Res`). If TIDAL delivers less
than the track offers, the badge changes to `◇ … ↓` in a warning color. Hover over it to see
the requested quality, received quality, and available track tiers.

**Tabs:** **Songs** lists titles with artist and album below each one; `▷` marks the current song.
**Queue · N** lists upcoming songs. **Lyrics** shows cover art and lyrics, synchronized when
TIDAL provides timestamps. **Playlists** lists playlists found through search; click one or
press Enter to open its songs. **Library ▾** lets you switch to a playlist from your library.

**Visualizer:** spare rows in the Lyrics tab show cava-style bars based on the actual audio
played by Noneto. Capture is limited to mpv's process and runs only while the visualizer is
visible. Samples are never saved. Requires Windows 10 version 2004 or later.

**Selection and player:** **+ Queue** and **+ Playlist** add the selected song. The player shows
cover art, title, artist, album, shuffle, previous/play/pause/next controls, repeat modes
(off, queue, or one song), clickable volume, and a progress bar you can click to seek.
This production version does not save the playback queue between sessions.

Shuffle keeps the current song and mixes only upcoming songs. Turning it off restores the
remaining songs to their original order. **+ Queue** always appends to the end. With repeat-all,
each new cycle is reshuffled and avoids starting with the same song that ended the previous
cycle when another song is available. Duplicate playlist entries are preserved.

Use a terminal with mouse support. The layout works at 80 × 24 and looks best at 100 × 28 or larger.

**Themes:** Ctrl+T cycles through nine palettes: noneto-dark, catppuccin-mocha, dracula,
tokyo-night, nord, rose-pine, flexoki, catppuccin-latte, and rose-pine-dawn. The visualizer uses
the active theme's colors. Noneto remembers your last theme.

**Language:** English is the default on every Windows language setting. Use `--lang es` for
Spanish or `--lang en` for English. Playlist names, song metadata, and lyrics keep their original language.

**Windows media controls:** Noneto publishes the song, artist, album, cover art, and progress
to the system media session. It accepts play/pause, previous/next, and seek commands from the
system panel. Windows decides how that panel looks; audio remains in mpv.
Noneto creates a **Noneto CLI** Start menu shortcut to identify the application. After
uninstalling with uv, you can also delete that shortcut.

| Shortcut | Action |
| --- | --- |
| Ctrl+L | Open Library; choosing a playlist focuses its songs for arrow-key navigation |
| Ctrl+F | Focus search |
| Ctrl+N | Create a playlist |
| Enter in the song table | Play from the selected song |
| Space outside a text field | Play / pause |
| Ctrl+Right | Next song |
| Ctrl+T | Change theme |
| Ctrl+Q / Ctrl+C | Quit and stop audio |

```powershell
noneto --playlist "https://tidal.com/browse/playlist/YOUR-PLAYLIST-UUID"
noneto                             # request Hi-Res FLAC, up to 24-bit / 192 kHz
noneto --quality LOSSLESS          # cap the request at CD quality
noneto --quality HIGH              # use less data (AAC 320 kbps)
noneto --lang es                   # use the Spanish interface
noneto --logout                    # delete the local session
```

## Privacy

- Noneto does not ask for or save your password. Tokens are encrypted with Windows DPAPI for
  your user account and stored in `%LOCALAPPDATA%\Noneto\session.bin`. `--logout` deletes the
  local session; it does not revoke the tokens at TIDAL.
- Music, lyrics, and cover art are not saved to disk.
- Logs in `%LOCALAPPDATA%\Noneto\logs\noneto.log` contain timestamps, track IDs, processing
  stages, HTTP status codes, and timings. They exclude tokens, searches, signed URLs, and
  audio, and remain on your computer.

## Scope and limitations

Noneto CLI provides the client interface, controls, and local integration. The catalog,
audio availability, and account access depend on TIDAL and the interfaces used by `tidalapi`.
Noneto does not provide a music service or access independent of your TIDAL account.

If an API changes or an integration becomes unavailable, affected features may change,
be disabled, or be removed. Continuous availability and fixes for every third-party change
are not guaranteed. The software is provided as-is, subject to the warranty terms in
[LICENSE](LICENSE). This notice does not replace dependency licenses or TIDAL's service terms.

- This production version supports Windows only.
- No offline downloads. You can create playlists and add songs; renaming and deleting are not implemented.
- Streams are resolved when each song is selected, so a brief pause between tracks is possible.

## Tests

```powershell
uv venv --python 3.12
uv pip install --python .venv/Scripts/python.exe -e ".[windows-test]"
.venv\Scripts\python.exe -m noneto --install-mpv
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests do not require a TIDAL account. mpv tests use local audio and a null audio output.
The visualizer capture test uses the actual audio device at an inaudible volume.

## License

The [MIT license](LICENSE) covers Noneto CLI's own code and documentation. Third-party
dependencies retain their own licenses; MIT does not replace them.

In particular, **`tidalapi`**, the Python library used to access TIDAL, is a separate,
unmodified dependency under
[LGPL-3.0-or-later](https://github.com/EbbLabs/python-tidal/blob/v0.8.11/LICENSE).
It is free software with different conditions from MIT. Noneto's license grants no rights
to TIDAL's service, APIs, music, lyrics, cover art, or trademarks.

Other main dependencies include Textual and Rich (MIT), Requests (Apache-2.0), NumPy (BSD),
and Pillow (MIT-CMU). mpv (GPL/LGPL) is downloaded separately from
[shinchiro/mpv-winbuild-cmake](https://github.com/shinchiro/mpv-winbuild-cmake) and runs as a separate process.
