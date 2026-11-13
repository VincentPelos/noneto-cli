"""Composition root: connects UI, auth, catalog and audio."""
import argparse
import os
import sys
import tempfile

from .auth.service import login, logout
from .auth.storage import SessionPersistence
from .audio.controller import PlaybackController
from .audio.mpv_install import install_mpv
from .audio.player import Player, find_mpv
from .audio.streams import stream_target
from .services.library import Library
from .services.tidal import Catalog, create_session
from .ui.terminal import Terminal
from .ui.themes import available_themes, load_user_themes
from .ui.visualizer import available_visualizer_modes
from .diagnostics import configure, emit, failure
from .i18n import LANGUAGES, _, set_language, DEFAULT_LANGUAGE
from .settings import load_settings, save_settings
from .state.queue import QueueStore


def main(argv=None):
    # The language must be set before help texts are built and before the TUI module is imported.
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--lang", choices=LANGUAGES)
    set_language(early.parse_known_args(argv)[0].lang or DEFAULT_LANGUAGE)
    parser = argparse.ArgumentParser(prog="noneto", description=_("Noneto CLI: unofficial TIDAL client for the terminal."))
    parser.add_argument("--playlist", help=_("Link or UUID of the playlist to open at startup"))
    parser.add_argument("--quality", choices=["HIGH", "LOSSLESS", "HI_RES_LOSSLESS"], default="HI_RES_LOSSLESS",
                        help=_("Highest quality requested from TIDAL; by default, the best available"))
    parser.add_argument("--lang", choices=LANGUAGES, help=_("Interface language (default: English)"))
    parser.add_argument("--logout", action="store_true", help=_("Delete the local session and exit"))
    parser.add_argument("--install-mpv", action="store_true",
                        help=_("Download mpv (pinned version, SHA-256 verified) and exit"))
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print(_("This release supports Windows only. Linux support is still in development."), file=sys.stderr)
        return 1
    from .ui.cover import terminal_artwork
    from .ui.lyrics import LAYOUTS
    from .ui.tui import NonetoApp
    session = None
    ui = Terminal()
    try:
        if args.logout:
            logout()
            ui.notify("logout")
            return 0
        if args.install_mpv:
            ui.notify("mpv-installing")
            ui.notify("mpv-ready", path=install_mpv())
            return 0
        try:
            log_path = configure()
        except OSError:
            ui.notify("logs-unavailable")
        else:
            ui.notify("logs-ready", path=log_path)
        emit("app.started", quality=args.quality)
        find_mpv()
        ui.notify("welcome")
        session = create_session(args.quality)
        login(session, ui.notify, ui.ask)
        catalog = Catalog(session)
        with tempfile.TemporaryDirectory(prefix="noneto-") as directory:
            library = Library(catalog, persist=SessionPersistence(session))
            controls = None
            playback = PlaybackController(lambda track: library.resolve(stream_target, track, directory),
                                          player_factory=lambda: Player(media_controls=not (controls and controls.available)),
                                          queue_store=QueueStore(session.user.id))
            try:
                if os.name == "nt":
                    from .audio.media_controls import MediaControls
                    controls = MediaControls(playback, lambda track: library.submit("media_cover", track))
                load_user_themes()
                settings = load_settings(available_themes(), allowed_visualizer_modes=available_visualizer_modes(),
                                         allowed_lyrics_layouts=LAYOUTS)

                def update_setting(key, value):
                    settings[key] = value
                    save_settings(settings)

                artwork = terminal_artwork()  # probes the terminal before Textual takes stdin
                interface = NonetoApp(library, playback, args.quality, args.playlist,
                                      getattr(session.user, "first_name", None), theme=settings.get("theme"),
                                      on_theme=lambda name: update_setting("theme", name),
                                      visualizer_mode=settings.get("visualizer_mode"),
                                      on_visualizer_mode=lambda mode: update_setting("visualizer_mode", mode),
                                      artwork=artwork, lyrics_layout=settings.get("lyrics_layout"),
                                      on_lyrics_layout=lambda layout: update_setting("lyrics_layout", layout))
                interface.run()
            finally:
                if controls is not None:
                    controls.close()
                playback.close()
                library.close()
        return interface.return_code
    except (KeyboardInterrupt, EOFError):
        ui.notify("bye")
        return 0
    except Exception as error:
        failure("app.failed", error)
        ui.notify("error", error=error)
        return 1
    finally:
        if session is not None:
            session.request_session.close()
        emit("app.stopped")
