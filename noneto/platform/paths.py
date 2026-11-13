"""User directories, preserving existing Windows data locations."""
import os
from pathlib import Path
import sys


def _xdg(variable, fallback):
    value = os.environ.get(variable)
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def data_dir():
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Noneto"
    return _xdg("XDG_DATA_HOME", ".local/share") / "noneto"


def config_dir():
    if sys.platform == "win32":
        return data_dir()
    return _xdg("XDG_CONFIG_HOME", ".config") / "noneto"


def state_dir():
    if sys.platform == "win32":
        return data_dir()
    return _xdg("XDG_STATE_HOME", ".local/state") / "noneto"
