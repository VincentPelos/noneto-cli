"""Composition root: connects UI, auth, catalog and audio."""
import argparse
import os
import tempfile

from .auth.service import login, logout
from .auth.storage import save_session
from .audio.controller import PlaybackController
from .audio.mpv_install import install_mpv
from .audio.player import find_mpv
from .audio.streams import stream_target
from .services.library import Library
from .services.tidal import Catalog, create_session
from .ui.terminal import Terminal
from .ui.tui import NonetoApp
from .diagnostics import configure, emit, failure


def main(argv=None):
    parser = argparse.ArgumentParser(prog="noneto", description="Noneto CLI: cliente no oficial de TIDAL para la terminal.")
    parser.add_argument("--playlist", help="Enlace o UUID de la playlist que se abre al iniciar")
    parser.add_argument("--quality", choices=["HIGH", "LOSSLESS", "HI_RES_LOSSLESS"], default="LOSSLESS")
    parser.add_argument("--logout", action="store_true", help="Elimina la sesion local y termina")
    parser.add_argument("--install-mpv", action="store_true",
                        help="Descarga mpv (version fijada, SHA-256 verificado) y termina")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("Por ahora Noneto CLI funciona solo en Windows.")
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
        login(session, ui.notify)
        catalog = Catalog(session)
        with tempfile.TemporaryDirectory(prefix="noneto-") as directory:
            library = Library(catalog, persist=lambda: save_session(session))
            playback = PlaybackController(lambda track: library.resolve(stream_target, track, directory))
            try:
                interface = NonetoApp(library, playback, args.quality, args.playlist,
                                      getattr(session.user, "first_name", None) or "Usuario")
                interface.run()
            finally:
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
