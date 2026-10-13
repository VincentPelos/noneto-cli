"""Console messages shown before the TUI opens (login, logs) and after it closes."""
import webbrowser

from ..errors import AppError, error_label
from ..i18n import _
from .login_window import WindowUnavailable, clipboard_login, window_login


class Terminal:
    def notify(self, event, **data):
        if event == "logs-ready":
            print(_("Diagnostics: {path}", path=data["path"]))
        elif event == "logs-unavailable":
            print(_("Could not open the diagnostics file."))
        elif event == "welcome":
            print("NONETO CLI")
        elif event == "logout":
            print(_("Local session deleted."))
        elif event == "mpv-installing":
            print(_("Installing mpv (if missing)..."))
        elif event == "mpv-ready":
            print(_("mpv ready: {path}", path=data["path"]))
        elif event == "bye":
            print("\n" + _("Goodbye."))
        elif event == "error":
            error = data["error"]
            if isinstance(error, AppError):
                print(f"\n{error}")
            else:
                print("\n" + _("The operation could not be completed ({label}).", label=error_label(error)))
                print(_("Check your connection and your account. To sign in again: noneto --logout"))
        elif event == "session-restored":
            print(_("Session restored."))
        elif event == "session-invalid":
            print(_("Could not restore the session. Please sign in again."))
        elif event == "session-upgrade":
            print(_("Your session is an older type without Hi-Res access. Please sign in again (only this once)."))
        elif event == "authorize-window":
            print(_("Sign in in the TIDAL window that just opened. It closes by itself when you are done."))
        elif event == "authorize-browser":
            print("\n" + _("Could not open the window. Sign in to TIDAL in your browser:"))
            print(data["url"])
            print("\n" + _("When you finish, TIDAL shows an error page (\"Oops\"). That is expected."))
            print(_("Copy that page's address (Ctrl+L, Ctrl+C): Noneto picks it up by itself. (Ctrl+C here cancels)"))
        elif event == "session-started":
            print(_("Signed in."))

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
