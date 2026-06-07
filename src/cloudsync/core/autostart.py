"""Manage the XDG autostart entry for start-on-login.

Places / removes a .desktop file in ``~/.config/autostart/`` which is the
standard mechanism honoured by GNOME, KDE, and most other desktop environments.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "com.seravault.cloudsync.desktop"

_TEMPLATE = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=CloudSync
Comment=Sync files with cloud storage providers
Exec={exec} --background
Icon=folder-remote
Terminal=false
Hidden=false
X-GNOME-Autostart-enabled=true
"""


def is_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def enable() -> None:
    """Install the autostart .desktop entry pointing at the running executable."""
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    exec_path = _find_executable()
    AUTOSTART_FILE.write_text(_TEMPLATE.format(exec=exec_path))


def disable() -> None:
    """Remove the autostart .desktop entry."""
    AUTOSTART_FILE.unlink(missing_ok=True)


def set_enabled(enabled: bool) -> None:
    enable() if enabled else disable()


def _find_executable() -> str:
    # Prefer the installed console_script on PATH
    cloudsync = shutil.which("cloudsync")
    if cloudsync:
        return cloudsync
    # Fall back to running the module via the current interpreter
    return f"{sys.executable} -m cloudsync"
