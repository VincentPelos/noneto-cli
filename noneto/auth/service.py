"""OAuth service; output is delivered as events, not printed here.

Login uses PKCE: TIDAL only serves Hi-Res FLAC (up to 24-bit / 192 kHz) to PKCE sessions.
Device-code sessions stored by earlier versions are replaced by a new PKCE login.
The UI shows the login page and returns the redirect URL it reaches; it is validated here.
"""
from urllib.parse import urlencode

import requests
from tidalapi.exceptions import AuthenticationError

from ..errors import AppError
from ..i18n import _
from ..security import https_url, pkce_code
from .storage import read_session, save_session, session_path


def login(session, notify, ask):
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
        if restored and session.is_pkce is True:
            save_session(session)
            notify("session-restored")
            return
        notify("session-upgrade" if restored else "session-invalid")
    redirect = session.config.pkce_uri_redirect
    url = https_url(session.pkce_login_url(), hosts={"login.tidal.com"})
    answer = ask("redirect", url=url, redirect=redirect)
    if not answer or not answer.strip():
        raise AppError(_("Inicio de sesion cancelado."))
    code = pkce_code(answer, redirect)
    try:
        # Rebuilt from the validated code: nothing else from the pasted text reaches TIDAL.
        token = session.pkce_get_auth_token(f"{redirect}?{urlencode({'code': code})}")
    except requests.HTTPError:
        raise AppError(_("TIDAL rechazo el codigo; puede haber caducado. Ejecuta noneto otra vez.")) from None
    session.process_auth_token(token, is_pkce_token=True)
    save_session(session)
    notify("session-started")


def logout():
    session_path().unlink(missing_ok=True)
