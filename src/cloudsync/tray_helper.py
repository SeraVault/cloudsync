"""Standalone GTK3/XApp tray subprocess.

Run as ``python3 tray_helper.py``.  Communicates with the parent process via
JSON-lines on stdin/stdout.  Stderr goes to the parent's stderr for logging.

Protocol (parent → helper):
  {"type": "quit"}
  {"type": "set-tooltip", "text": "…"}
  {"type": "set-status", "syncing": true/false}

Protocol (helper → parent):
  {"type": "ready", "has_monitor": true/false}
  {"type": "action", "name": "open" | "sync" | "quit"}
"""
from __future__ import annotations

import gi
gi.require_version("XApp", "1.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gtk, XApp  # noqa: E402  (must be after require_version)

import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.DEBUG,
    stream=sys.stderr,
    format="%(levelname)s tray_helper: %(message)s",
)
log = logging.getLogger("tray_helper")


def _send(event_type: str, **kwargs: object) -> None:
    msg = json.dumps({"type": event_type, **kwargs})
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _icon_path() -> str:
    candidates = [
        "/app/share/icons/hicolor/scalable/apps/com.seravault.cloudsync.svg",
        "/app/share/icons/hicolor/48x48/apps/com.seravault.cloudsync.png",
        os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "../../data/icons/com.seravault.cloudsync.svg",
        )),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return "drive-harddisk"


class _TrayHelper:
    def __init__(self) -> None:
        self._loop = GLib.MainLoop()
        self._icon: XApp.StatusIcon | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        self._icon = XApp.StatusIcon()
        self._icon.set_name("cloudsync")
        self._icon.set_tooltip_text("CloudSync — Google Drive sync")
        self._icon.set_icon_name(_icon_path())
        self._icon.set_visible(True)
        self._icon.set_secondary_menu(self._build_menu())

        has_monitor = XApp.StatusIcon.any_monitors()
        _send("ready", has_monitor=has_monitor)
        log.debug("started — has_monitor=%s", has_monitor)

        channel = GLib.IOChannel.unix_new(sys.stdin.fileno())
        GLib.io_add_watch(
            channel,
            GLib.IOCondition.IN | GLib.IOCondition.HUP,
            self._on_stdin,
        )

        self._loop.run()
        log.debug("main loop exited")

    def _quit(self) -> None:
        if self._icon:
            self._icon.set_visible(False)
        self._loop.quit()

    # ------------------------------------------------------------------ #
    # Menu                                                                 #
    # ------------------------------------------------------------------ #

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        item_open = Gtk.MenuItem(label="Open CloudSync")
        item_open.connect("activate", lambda *_: _send("action", name="open"))
        menu.append(item_open)

        item_sync = Gtk.MenuItem(label="Sync Now")
        item_sync.connect("activate", lambda *_: _send("action", name="sync"))
        menu.append(item_sync)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit CloudSync")
        item_quit.connect("activate", lambda *_: _send("action", name="quit"))
        menu.append(item_quit)

        menu.show_all()
        return menu

    # ------------------------------------------------------------------ #
    # IPC                                                                  #
    # ------------------------------------------------------------------ #

    def _on_stdin(self, channel: GLib.IOChannel, condition: GLib.IOCondition) -> bool:
        if condition & GLib.IOCondition.HUP:
            log.debug("stdin HUP — quitting")
            self._loop.quit()
            return False

        line = channel.readline().strip()
        if not line:
            return True

        try:
            self._handle_command(json.loads(line))
        except json.JSONDecodeError:
            log.warning("invalid JSON from parent: %r", line)
        return True

    def _handle_command(self, cmd: dict) -> None:
        t = cmd.get("type")
        if t == "quit":
            self._quit()
        elif t == "set-tooltip" and self._icon:
            self._icon.set_tooltip_text(cmd.get("text", ""))
        elif t == "set-status" and self._icon:
            syncing = cmd.get("syncing", False)
            tip = "CloudSync — syncing…" if syncing else "CloudSync — Google Drive sync"
            self._icon.set_tooltip_text(tip)
        else:
            log.debug("unknown command: %s", t)


if __name__ == "__main__":
    _TrayHelper().run()
