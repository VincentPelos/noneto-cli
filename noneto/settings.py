"""Non-secret interface preferences in the platform config directory.

Nothing secret lives here. Unknown keys and invalid values are dropped on read.
"""
import json
from pathlib import Path
from .platform.paths import config_dir
import tempfile

MAX_SETTINGS_BYTES = 4096


def settings_path():
    return config_dir() / "settings.json"


def load_settings(allowed_themes, path=None, allowed_visualizer_modes=None, allowed_lyrics_layouts=None):
    path = Path(path) if path is not None else settings_path()
    try:
        with path.open("rb") as source:
            data = json.loads(source.read(MAX_SETTINGS_BYTES + 1)[:MAX_SETTINGS_BYTES])
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    if data.get("theme") in allowed_themes:
        result["theme"] = data["theme"]
    if allowed_visualizer_modes is not None and data.get("visualizer_mode") in allowed_visualizer_modes:
        result["visualizer_mode"] = data["visualizer_mode"]
    if allowed_lyrics_layouts is not None and data.get("lyrics_layout") in allowed_lyrics_layouts:
        result["lyrics_layout"] = data["lyrics_layout"]
    return result


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
