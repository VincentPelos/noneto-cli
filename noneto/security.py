"""Validation at external input boundaries; never include rejected values in errors."""
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from .errors import AppError


def https_url(value, *, hosts=None):
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise AppError("Destino de red no valido.")
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or "\\" in value:
        raise AppError("Destino de red no valido.")
    try:
        url = urlsplit(value)
        host = (url.hostname or "").lower()
        port = url.port
    except ValueError:
        raise AppError("Destino de red no valido.") from None
    if (url.scheme != "https" or not host or url.username is not None
            or url.password is not None or port not in (None, 443) or url.fragment):
        raise AppError("Solo se permiten destinos HTTPS validos.")
    if hosts is not None and host not in hosts:
        raise AppError("El destino no pertenece al servicio autorizado.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or "." not in host or ".." in host
                or host.endswith((".localhost", ".local", ".internal"))
                or host.rsplit(".", 1)[-1].isdigit()):
            raise AppError("Destino de red no valido.")
    else:
        if not address.is_global:
            raise AppError("No se permiten direcciones de red privadas.")
    return value


def terminal_text(value):
    # Remote metadata must not emit ESC/OSC, C1 controls or bidi format controls.
    return "".join(c for c in str(value) if not unicodedata.category(c).startswith("C"))[:2000]
