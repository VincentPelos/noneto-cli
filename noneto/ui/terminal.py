"""Console messages shown before the TUI opens (login, logs) and after it closes."""
import webbrowser

from ..errors import AppError, error_label
from ..i18n import _
from .login_window import WindowUnavailable, clipboard_login, window_login


class Terminal:
    def notify(self, event, **data):
        if event == "logs-ready":
            print(_("Diagnostico: {path}", path=data["path"]))
        elif event == "logs-unavailable":
            print(_("No se pudo abrir el archivo de diagnostico."))
        elif event == "welcome":
            print("NONETO CLI")
        elif event == "logout":
            print(_("Sesion local eliminada."))
        elif event == "mpv-installing":
            print(_("Instalando mpv (si falta)..."))
        elif event == "mpv-ready":
            print(_("mpv listo: {path}", path=data["path"]))
        elif event == "bye":
            print("\n" + _("Hasta luego."))
        elif event == "error":
            error = data["error"]
            if isinstance(error, AppError):
                print(f"\n{error}")
            else:
                print("\n" + _("No se pudo completar la operacion ({label}).", label=error_label(error)))
                print(_("Comprueba tu conexion y tu cuenta. Para iniciar sesion otra vez: noneto --logout"))
        elif event == "session-restored":
            print(_("Sesion recuperada."))
        elif event == "session-invalid":
            print(_("No se pudo recuperar la sesion. Inicia sesion nuevamente."))
        elif event == "session-upgrade":
            print(_("Tu sesion es de un tipo antiguo sin acceso a Hi-Res. Inicia sesion otra vez (solo esta vez)."))
        elif event == "authorize-window":
            print(_("Inicia sesion en la ventana de TIDAL que se abrio. Se cierra sola al terminar."))
        elif event == "authorize-browser":
            print("\n" + _("No se pudo abrir la ventana. Inicia sesion en TIDAL desde tu navegador:"))
            print(data["url"])
            print("\n" + _("Al terminar, TIDAL muestra una pagina de error (\"Oops\"). Es normal."))
            print(_("Copia la direccion de esa pagina (Ctrl+L, Ctrl+C): Noneto la detecta sola. (Ctrl+C aqui cancela)"))
        elif event == "session-started":
            print(_("Sesion iniciada."))

    def ask(self, question, **data):
        if question != "redirect":
            raise ValueError(question)
        self.notify("authorize-window")
        try:
            return window_login(data["url"], data["redirect"])
        except WindowUnavailable:
            pass
        self.notify("authorize-browser", url=data["url"])
        try:
            webbrowser.open(data["url"])
        except webbrowser.Error:
            pass
        return clipboard_login(data["redirect"])
