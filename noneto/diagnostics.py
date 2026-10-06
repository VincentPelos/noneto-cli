"""Allowlisted JSON diagnostics. Never persist exception text, bodies or media URLs."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import traceback
from urllib.parse import urlsplit
import uuid

LOGGER = logging.getLogger("noneto.diagnostics")
LOGGER.addHandler(logging.NullHandler())
LOGGER.propagate = False
_run = None


def configure(directory=None):
    global _run
    directory = Path(directory) if directory is not None else Path(os.environ["LOCALAPPDATA"]) / "Noneto" / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "noneto.log"
    handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    for previous in LOGGER.handlers[:]:
        LOGGER.removeHandler(previous)
        previous.close()
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    _run = uuid.uuid4().hex[:12]
    emit("diagnostics.started")
    return path


def emit(event, **fields):
    # Call sites supply structured facts, never exception text or arbitrary dictionaries.
    allowed = {"stage", "track_id", "index", "count", "quality", "method", "host", "route",
               "status", "elapsed_ms", "manifest_type", "target_kind", "category",
               "error_types", "codes", "frames", "reason", "component", "command", "request_id"}
    record = {"time": datetime.now(timezone.utc).isoformat(), "run": _run,
              "event": event, **{k: v for k, v in fields.items() if k in allowed}}
    LOGGER.info(json.dumps(record, ensure_ascii=True))


def classify(text):
    lower = text.lower()
    for category, patterns in (
        ("tls_certificate", ("certificate verify failed", "certificate verification failed", "unable to get local issuer")),
        ("dns", ("getaddrinfo failed", "name resolution", "name or service not known", "failed to resolve")),
        ("connection_reset", ("connection reset", "forcibly closed", "10054", "remote end closed")),
        ("connection_refused", ("connection refused", "10061")),
        ("timeout", ("timed out", "timeout")),
        ("protocol_blocked", ("not on whitelist", "protocol not found")),
        ("tls", ("ssl", "tls")),
        ("http", ("http error", "server returned", "status code")),
        ("media_decode", ("invalid data", "failed to recognize file format", "decoder")),
    ):
        if any(pattern in lower for pattern in patterns):
            return category
    return "unknown"


def failure(event, error, **fields):
    # Inspect causes in memory; only fixed categories, class names, integer codes and
    # source locations leave this function. No traceback source lines or local values.
    pending, seen, errors = [error], set(), []
    while pending and len(errors) < 8:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        errors.append(current)
        pending.extend(item for item in (current.__cause__, current.__context__,
                       getattr(current, "reason", None), *current.args) if isinstance(item, BaseException))
    category = "unknown"
    for current in errors:
        candidate = classify(str(current))
        if candidate != "unknown":
            category = candidate
            break
    codes = [value for current in errors for key in ("errno", "winerror")
             if isinstance(value := getattr(current, key, None), int)]
    frames = [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
              for frame in traceback.extract_tb(error.__traceback__)[-8:]]
    emit(event, **fields, category=category, error_types=[type(e).__name__ for e in errors],
         codes=codes, frames=frames)


def request_target(url):
    parsed = urlsplit(url)
    # Keep a small route vocabulary; exclude user IDs, searches and all query strings.
    path = parsed.path
    if re.fullmatch(r"/v1/tracks/\d+/playbackinfopostpaywall", path):
        route = "/v1/tracks/:id/playbackinfopostpaywall"
    elif path in {"/v1/oauth2/token", "/v1/oauth2/device_authorization", "/v1/sessions"}:
        route = path
    else:
        route = "catalog"
    return {"host": parsed.hostname, "route": route}


def track_id(track):
    value = getattr(track, "id", None)
    return value if isinstance(value, int) else None


def mpv_message(message):
    # Native mpv logs often contain signed URLs. Classify and discard raw text.
    text = str(message.get("text", ""))
    prefix = message.get("prefix")
    component = prefix if prefix in {"ffmpeg", "lavf", "cplayer", "demux", "ao", "ad"} else "mpv"
    status = re.search(r"(?:HTTP (?:error )?|Server returned )(\d{3})\b", text, re.I)
    emit("mpv.warning", component=component, category=classify(text),
         status=int(status.group(1)) if status else None)
