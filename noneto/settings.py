"""Interface preferences (only the theme for now) in %LOCALAPPDATA%/Noneto/settings.json.

Nothing secret lives here. Unknown keys and invalid values are dropped on read.
"""
import json
import os
from pathlib import Path
import tempfile

MAX_SETTINGS_BYTES = 4096


def settings_path():
    return Path(os.environ["LOCALAPPDATA"]) / "Noneto" / "settings.json"


def load_settings(allowed_themes, path=None):
    path = Path(path) if path is not None else settings_path()
    try:
        with path.open("rb") as source:
            data = json.loads(source.read(MAX_SETTINGS_BYTES + 1)[:MAX_SETTINGS_BYTES])
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("theme") not in allowed_themes:
        return {}
    return {"theme": data["theme"]}


def save_settings(settings, path=None):
    path = Path(path) if path is not None else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".settings-",
                                         suffix=".tmp", delete=False) as output:
            temp = Path(output.name)
            json.dump(settings, output)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
