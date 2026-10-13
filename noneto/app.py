"""Composition root: connects UI, auth, catalog and audio."""
import argparse
import os
import tempfile

from .auth.service import login, logout
from .auth.storage import save_session
from .audio.controller import PlaybackController
from .audio.media_controls import MediaControls
from .audio.mpv_install import install_mpv
from .audio.player import Player, find_mpv
from .audio.streams import stream_target
from .services.library import Library
from .services.tidal import Catalog, create_session
from .ui.terminal import Terminal
from .ui.themes import THEMES
from .diagnostics import configure, emit, failure
from .i18n import LANGUAGES, _, set_language, DEFAULT_LANGUAGE
from .settings import load_settings, save_settings


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
    if os.name != "nt":
        parser.error(_("For now Noneto CLI only runs on Windows."))
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
            library = Library(catalog, persist=lambda: save_session(session))
            controls = None
            playback = PlaybackController(lambda track: library.resolve(stream_target, track, directory),
                                          player_factory=lambda: Player(media_controls=not (controls and controls.available)))
            try:
                controls = MediaControls(playback, lambda track: library.submit("media_cover", track))
                settings = load_settings(THEMES)
                interface = NonetoApp(library, playback, args.quality, args.playlist,
                                      getattr(session.user, "first_name", None), theme=settings.get("theme"),
                                      on_theme=lambda name: save_settings({**settings, "theme": name}))
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
