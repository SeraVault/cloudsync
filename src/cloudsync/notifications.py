"""Desktop notifications via Gio.Notification."""
from __future__ import annotations

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio


class Notifier:
    def __init__(self, app: Gio.Application):
        self._app = app

    def send(self, title: str, body: str, icon: str = "folder-sync-symbolic") -> None:
        note = Gio.Notification.new(title)
        note.set_body(body)
        note.set_icon(Gio.ThemedIcon.new(icon))
        self._app.send_notification(None, note)

    def sync_finished(self, uploaded: int, downloaded: int) -> None:
        """Notify only when files actually moved — silent if already in sync."""
        if uploaded == 0 and downloaded == 0:
            return
        parts = []
        if uploaded:
            parts.append(f"{uploaded} uploaded")
        if downloaded:
            parts.append(f"{downloaded} downloaded")
        self.send("Sync complete", ", ".join(parts) + ".")

    def sync_error(self, message: str) -> None:
        self.send("Sync error", message, icon="dialog-error-symbolic")
