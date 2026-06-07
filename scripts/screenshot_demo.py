#!/usr/bin/env python3
"""Launch the main window pre-populated with fake demo data for screenshots.

Does NOT read or write any real config or credentials.

Usage:
    python scripts/screenshot_demo.py
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# Make the source tree importable without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gio, Gtk

from cloudsync.core.config import Config, ProviderAccount, SyncFolder
from cloudsync.core.activity_log import ActivityLog, ActivityEntry
from cloudsync.ui.window import MainWindow, FolderRow

# --------------------------------------------------------------------------- #
# Fake data                                                                     #
# --------------------------------------------------------------------------- #

_FAKE_ACCOUNTS = [
    ProviderAccount(provider="gdrive",  display_name="alice@gmail.com"),
    ProviderAccount(provider="dropbox", display_name="alice@example.com"),
    ProviderAccount(provider="s3",      display_name="my-backup-bucket"),
]

_FAKE_FOLDERS = [
    SyncFolder(
        local_path="/home/alice/Documents",
        remote_folder_name="Documents",
        provider="gdrive",
        enabled=True,
    ),
    SyncFolder(
        local_path="/home/alice/Photos",
        remote_folder_name="Camera Uploads",
        provider="dropbox",
        enabled=True,
    ),
    SyncFolder(
        local_path="/home/alice/Projects",
        remote_folder_name="projects/",
        provider="s3",
        enabled=True,
    ),
    SyncFolder(
        local_path="/home/alice/Music",
        remote_folder_name="Music",
        provider="gdrive",
        enabled=False,
    ),
]

_FAKE_LOG: list[ActivityEntry] = []  # empty — keeps the "No recent errors" state


# --------------------------------------------------------------------------- #
# Minimal stub app that MainWindow can talk to                                 #
# --------------------------------------------------------------------------- #

class _DemoApp(Adw.Application):
    """Minimal Adw.Application stub — just enough for MainWindow to render."""

    def __init__(self):
        super().__init__(
            application_id="com.seravault.cloudsync.demo",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.config = Config(
            connected_providers=_FAKE_ACCOUNTS,
            sync_folders=_FAKE_FOLDERS,
            sync_interval_seconds=60,
            notifications_enabled=True,
            start_on_login=False,
        )
        self.activity_log = _FakeActivityLog()
        self.account_email = "alice@gmail.com"

    def do_activate(self) -> None:
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            icons_dir = Path(__file__).resolve().parent.parent / "data" / "icons"
            if icons_dir.is_dir():
                theme.add_search_path(str(icons_dir))

        win = _DemoWindow(self)
        win.set_icon_name("com.seravault.cloudsync")
        win.present()

        # Populate some realistic-looking sync status labels
        GLib.timeout_add(300, lambda: (
            win.set_folder_status("/home/alice/Documents", "Last synced 2 min ago"),
            win.set_folder_status("/home/alice/Photos", "Last synced 5 min ago"),
            win.set_folder_status("/home/alice/Projects", "Syncing…"),
            win.set_folder_progress("/home/alice/Projects", 14, 38),
            win.set_folder_detail("/home/alice/Projects", "Uploading main.py"),
            win.set_folder_status("/home/alice/Music", "Disabled"),
            False,  # don't repeat
        ) and False)

    # Stubs for callbacks triggered by the window's buttons
    def _show_add_provider_wizard(self): pass
    def remove_provider_account(self, p): pass
    def remove_sync_folder(self, f): pass
    def trigger_sync(self): pass
    def trigger_folder_sync(self, f): pass
    def save_config(self, c=None): pass

    @property
    def _providers(self):
        return {}


class _FakeActivityLog:
    def recent(self, n=50):
        return _FAKE_LOG


class _DemoWindow(MainWindow):
    """MainWindow subclass that disables every button action."""

    def _on_add_account_clicked(self, _btn): pass
    def _on_disconnect_provider(self, p): pass
    def _on_edit_account(self, p): pass
    def _on_add_folder_clicked(self, _btn): pass
    def _on_remove_folder(self, f): pass
    def _on_edit_folder(self, f): pass
    def _on_sync_folder(self, f): pass
    def _on_sync_clicked(self, _btn): pass
    def _on_prefs_clicked(self, _btn): pass
    def _on_help_clicked(self, _btn): pass
    def _on_copy_logs_clicked(self, _btn): pass
    def _on_clear_logs_clicked(self, _btn): pass


# --------------------------------------------------------------------------- #
# Entry point                                                                   #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    app = _DemoApp()
    sys.exit(app.run([]))
