"""Writable application state; resources stay inside the immutable bundle."""
import os
from pathlib import Path
import sys


def data_directory():
    if not getattr(sys, 'frozen', False):
        return Path(__file__).resolve().parent
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'MSU2Controller'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'MSU2Controller'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'MSU2Controller'


def log_directory():
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'MSU2Controller'
    return data_directory() / 'logs'
