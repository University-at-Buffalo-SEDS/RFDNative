"""Per-user storage without depending on the working directory."""
import os
from pathlib import Path
import sys


def data_directory() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'RFD Native'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'RFD Native'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'rfd-native'


def log_directory() -> Path:
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'RFD Native'
    if sys.platform == 'win32':
        return data_directory() / 'Logs'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')) / 'rfd-native' / 'logs'
