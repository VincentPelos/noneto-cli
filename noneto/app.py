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
from .i18n import LANGUAGES, _, set_language, system_language
from .settings import load_settings, save_settings


def main(argv=None):
    # The language must be set before help texts are built and before the TUI module is imported.
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--lang", choices=LANGUAGES)
    set_language(early.parse_known_args(argv)[0].lang or system_language())
    parser = argparse.ArgumentParser(prog="noneto", description=_("Noneto CLI: cliente no oficial de TIDAL para la terminal."))
    parser.add_argument("--playlist", help=_("Enlace o UUID de la playlist que se abre al iniciar"))
    parser.add_argument("--quality", choices=["HIGH", "LOSSLESS", "HI_RES_LOSSLESS"], default="HI_RES_LOSSLESS",
                        help=_("Calidad maxima pedida a TIDAL; por defecto, la mejor disponible"))
    parser.add_argument("--lang", choices=LANGUAGES, help=_("Idioma de la interfaz (por defecto, el de Windows)"))
    parser.add_argument("--logout", action="store_true", help=_("Elimina la sesion local y termina"))
    parser.add_argument("--install-mpv", action="store_true",
                        help=_("Descarga mpv (version fijada, SHA-256 verificado) y termina"))
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error(_("Por ahora Noneto CLI funciona solo en Windows."))
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
