"""OAuth service; output is delivered as events, not printed here."""
import webbrowser

import requests
from tidalapi.exceptions import AuthenticationError

from ..errors import AppError
from ..security import https_url
from .storage import read_session, save_session, session_path


def login(session, notify):
    if session_path().exists():
        try:
            restored = session.load_oauth_session(**read_session())
        except (requests.ConnectionError, requests.Timeout):
            raise
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code not in (401, 403):
                raise
            restored = False
        except (OSError, ValueError, TypeError, KeyError, AuthenticationError):
            restored = False
        if restored:
            save_session(session)
            notify("session-restored")
            return
        notify("session-invalid")
    link = session.get_link_login()
    url = link.verification_uri_complete
    if isinstance(url, str) and "://" not in url:
        url = "https://" + url
    https_url(url, hosts={"link.tidal.com", "login.tidal.com", "tidal.com", "www.tidal.com"})
    notify("authorize", url=url)
    try:
        webbrowser.open(url)
    except webbrowser.Error:
        pass
    if not session.process_link_login(link):
        raise AppError("No se pudo completar el inicio de sesion.")
    save_session(session)
    notify("session-started")


def logout():
    session_path().unlink(missing_ok=True)
