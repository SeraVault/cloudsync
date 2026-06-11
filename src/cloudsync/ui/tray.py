"""System tray icon — pure GLib/Gio D-Bus, no GTK3 or XApp required.

Two protocols are supported and auto-detected at runtime:

* **org.x.StatusIcon** — Cinnamon-native.  Cinnamon's panel applet watches
  for bus names matching ``org.x.StatusIcon.*`` and calls ButtonPress/Release.

* **StatusNotifierItem (SNI)** — used by KDE Plasma, XFCE (with the
  StatusNotifier plugin), MATE, LXQt, and Wayland compositors that support
  the freedesktop / KDE appindicator protocol.

For SNI the watcher name is watched persistently: the item re-registers
whenever a watcher (re)appears — covering plasmashell restarts and the
GNOME AppIndicator extension being enabled mid-session.  GNOME without
the extension has no system tray; the class stays dormant there and the
``on_active_changed`` hook reports the (in)active state to the app.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import gi
gi.require_version("GLib", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import GLib, Gio

if TYPE_CHECKING:
    from ..app import CloudSyncApp

from .. import APP_ID  # noqa: E402  (must be after gi.require_version)

log = logging.getLogger(__name__)

# ── Cinnamon org.x.StatusIcon ─────────────────────────────────────────────── #

_XSI_BUS_NAME    = "org.x.StatusIcon.cloudsync"
_XSI_ROOT_PATH   = "/org/x/StatusIcon"
_XSI_OBJECT_PATH = "/org/x/StatusIcon/Icon"

# ObjectManager at the root — libxapp creates a GDBusObjectManager which
# expects GetManagedObjects to be answerable at /org/x/StatusIcon.
_OBJMGR_IFACE_XML = """
<node>
  <interface name="org.freedesktop.DBus.ObjectManager">
    <method name="GetManagedObjects">
      <arg type="a{oa{sa{sv}}}" name="object_paths_interfaces_and_properties" direction="out"/>
    </method>
    <signal name="InterfacesAdded">
      <arg type="o" name="object_path"/>
      <arg type="a{sa{sv}}" name="interfaces_and_properties"/>
    </signal>
    <signal name="InterfacesRemoved">
      <arg type="o" name="object_path"/>
      <arg type="as" name="interfaces"/>
    </signal>
  </interface>
</node>
"""

_XSI_IFACE_XML = """
<node>
  <interface name="org.x.StatusIcon">
    <method name="ButtonPress">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
      <arg type="u" name="button" direction="in"/>
      <arg type="u" name="time" direction="in"/>
      <arg type="i" name="panel_position" direction="in"/>
    </method>
    <method name="ButtonRelease">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
      <arg type="u" name="button" direction="in"/>
      <arg type="u" name="time" direction="in"/>
      <arg type="i" name="panel_position" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="i" name="orientation" direction="in"/>
      <arg type="u" name="time" direction="in"/>
    </method>
    <property name="Name"                type="s" access="read"/>
    <property name="IconName"            type="s" access="read"/>
    <property name="TooltipText"         type="s" access="read"/>
    <property name="Label"               type="s" access="read"/>
    <property name="Visible"             type="b" access="read"/>
    <property name="IconSize"            type="i" access="readwrite"/>
    <property name="PrimaryMenuIsOpen"   type="b" access="read"/>
    <property name="SecondaryMenuIsOpen" type="b" access="read"/>
    <property name="Metadata"            type="s" access="read"/>
  </interface>
</node>
"""

# ── StatusNotifierItem (KDE / XFCE / MATE / Wayland) ─────────────────────── #

_SNI_OBJECT_PATH = "/StatusNotifierItem"
_SNI_WATCHER     = "org.kde.StatusNotifierWatcher"
_SNI_WATCHER_X   = "org.x.StatusNotifierWatcher"  # XFCE / LXQt fallback
_DBUSMENU_PATH   = "/MenuBar"

_SNI_IFACE_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s" name="Status"/></signal>
    <property name="Category"           type="s"        access="read"/>
    <property name="Id"                 type="s"        access="read"/>
    <property name="Title"              type="s"        access="read"/>
    <property name="Status"             type="s"        access="read"/>
    <property name="WindowId"           type="i"        access="read"/>
    <property name="IconName"           type="s"        access="read"/>
    <property name="IconPixmap"         type="a(iiay)"  access="read"/>
    <property name="OverlayIconName"    type="s"        access="read"/>
    <property name="AttentionIconName"  type="s"        access="read"/>
    <property name="AttentionMovieName" type="s"        access="read"/>
    <property name="ToolTip"            type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu"         type="b"        access="read"/>
    <property name="IconThemePath"      type="s"        access="read"/>
    <property name="Menu"               type="o"        access="read"/>
  </interface>
</node>
"""

# com.canonical.dbusmenu — used by KDE, XFCE, and any SNI host that wants to
# render the menu natively rather than sending ContextMenu coordinates back.
# The host calls GetLayout to discover the menu tree, then sends Event("clicked")
# when the user selects an item.  This avoids all window-positioning headaches
# on Wayland and looks fully native on every SNI desktop.
_DBUSMENU_IFACE_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i"          name="parentId"      direction="in"/>
      <arg type="i"          name="recursionDepth" direction="in"/>
      <arg type="as"         name="propertyNames" direction="in"/>
      <arg type="u"          name="revision"      direction="out"/>
      <arg type="(ia{sv}av)" name="layout"        direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai"          name="ids"           direction="in"/>
      <arg type="as"          name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})"   name="properties"    direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i"  name="id"       direction="in"/>
      <arg type="s"  name="name"     direction="in"/>
      <arg type="v"  name="value"    direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id"        direction="in"/>
      <arg type="s" name="eventId"   direction="in"/>
      <arg type="v" name="data"      direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events"    direction="in"/>
      <arg type="ai"      name="idErrors"  direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id"          direction="in"/>
      <arg type="b" name="needUpdate"  direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids"           direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors"      direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)"    name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
    <property name="Version"        type="u" access="read"/>
    <property name="TextDirection"  type="s" access="read"/>
    <property name="Status"         type="s" access="read"/>
    <property name="IconThemePath"  type="as" access="read"/>
  </interface>
</node>
"""

# Menu item IDs (stable integers the host uses to refer to items in Event calls)
_MENU_ID_ROOT      = 0
_MENU_ID_OPEN      = 1
_MENU_ID_SYNC      = 2
_MENU_ID_SEP       = 3
_MENU_ID_QUIT      = 4


def _flatpak_install_base() -> str:
    """Return the host-side flatpak installation base (user or system).

    /.flatpak-info records the app-path of the running instance, which tells
    us whether the app was installed per-user or system-wide — the exports
    (icons visible to the host panel) live under the matching base.
    """
    user_base = os.path.expanduser("~/.local/share/flatpak")
    try:
        import configparser
        info = configparser.ConfigParser(interpolation=None)
        info.read("/.flatpak-info")
        app_path = info.get("Instance", "app-path", fallback="")
        if app_path and not app_path.startswith(user_base):
            return "/var/lib/flatpak"
    except Exception:
        pass
    return user_base


def _icon_name() -> str:
    """Return an icon path/name usable by the HOST desktop environment.

    Inside a flatpak, /.flatpak-info exists and we return the stable exports
    symlink path — this is visible to the host panel (Cinnamon, etc).
    Outside a flatpak, return the app-id theme name.
    """
    if os.path.exists("/.flatpak-info"):
        return (_flatpak_install_base()
                + "/exports/share/icons/hicolor/scalable/apps"
                  "/com.seravault.cloudsync.svg")
    return "com.seravault.cloudsync"


def _icon_theme_path() -> str:
    """Extra icon-theme search path for SNI hosts (KDE honours IconThemePath)."""
    if os.path.exists("/.flatpak-info"):
        return _flatpak_install_base() + "/exports/share/icons"
    return ""


def _has_bus_prefix(conn: Gio.DBusConnection, prefix: str) -> bool:
    """Return True if any currently-owned bus name starts with *prefix*."""
    try:
        result = conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            GLib.VariantType.new("(as)"),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        names: list[str] = result[0]
        return any(n.startswith(prefix) for n in names)
    except Exception:
        return False


class TrayIcon:
    """System tray icon — auto-detects Cinnamon or SNI protocol.

    Call ``start()`` once the GLib main loop is running.
    """

    def __init__(self, app: "CloudSyncApp") -> None:
        self._app = app
        self._conn: Gio.DBusConnection | None = None
        self._name_id: int = 0
        self._reg_id: int = 0
        self._objmgr_reg_id: int = 0
        self._dbusmenu_reg_id: int = 0
        self._active_watcher: str = ""  # watcher we are (or were) registered with
        self._tooltip: str = "CloudSync — Google Drive sync"
        self._protocol: str = ""  # "xsi" | "sni" | ""
        self._menu_revision: int = 1
        self._layout_sig: tuple = ()  # menu labels at last GetLayout
        self._watcher_watch_ids: list[int] = []
        self._sni_active: bool = False    # watcher accepted our registration
        self._sni_pending: bool = False   # RegisterStatusNotifierItem in flight
        # Called with is_active() whenever the tray gains/loses a host
        # (watcher appears late, plasmashell restarts, …).
        self.on_active_changed: object = None
        # Keep node info objects alive (GC'd immediately if not stored)
        self._node_info: object = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        try:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except Exception as exc:
            log.warning("Tray: cannot connect to session bus: %s", exc)
            return

        if _has_bus_prefix(conn, "org.x.StatusIconMonitor."):
            self._protocol = "xsi"
            log.debug("Tray: Cinnamon detected — using org.x.StatusIcon")
            self._start_xsi()
            return

        # SNI everywhere else.  Register the item/menu objects once, then keep
        # a persistent watch on both watcher names: name_appeared fires
        # immediately if a watcher is already running, and again every time a
        # host (re)starts — plasmashell crash/restart, the GNOME AppIndicator
        # extension being enabled mid-session — at which point the item must
        # be re-registered or the icon stays gone.
        self._protocol = "sni"
        self._register_sni_objects(conn)
        for watcher_name in (_SNI_WATCHER, _SNI_WATCHER_X):
            self._watcher_watch_ids.append(Gio.bus_watch_name(
                Gio.BusType.SESSION,
                watcher_name,
                Gio.BusNameWatcherFlags.NONE,
                lambda _c, wn, _owner: self._on_watcher_appeared(wn),
                lambda _c, wn: self._on_watcher_vanished(wn),
            ))

        if not (_has_bus_prefix(conn, _SNI_WATCHER)
                or _has_bus_prefix(conn, _SNI_WATCHER_X)):
            desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
            if "GNOME" in desktop.upper():
                log.info(
                    "Tray: no StatusNotifier watcher on the bus — GNOME needs the "
                    "'AppIndicator and KStatusNotifierItem Support' extension "
                    "(https://extensions.gnome.org/extension/615/). The icon will "
                    "appear automatically if it is enabled."
                )
            else:
                log.info("Tray: no StatusNotifier watcher on the bus yet — "
                         "the icon will appear if one starts (%s)", desktop)

    def _on_watcher_appeared(self, watcher_name: str) -> None:
        if not self._reg_id:
            return  # SNI object registration failed; nothing to offer a host
        if self._sni_active or self._sni_pending:
            return  # already hosted (or registering) via the other watcher
        log.debug("Tray SNI: watcher appeared (%s) — registering item", watcher_name)
        self._active_watcher = watcher_name
        self._register_with_watcher()

    def _on_watcher_vanished(self, watcher_name: str) -> None:
        # Fires once at watch setup when the name has no owner, and later if
        # the host dies.  Only react if this was the watcher hosting us.
        if watcher_name != self._active_watcher:
            return
        if self._sni_active or self._sni_pending:
            log.debug("Tray SNI: watcher vanished (%s) — waiting for a watcher "
                      "to return", watcher_name)
        self._sni_pending = False
        self._set_sni_active(False)

    def _set_sni_active(self, active: bool) -> None:
        if active == self._sni_active:
            return
        self._sni_active = active
        self._notify_active_changed()

    def _notify_active_changed(self) -> None:
        cb = self.on_active_changed
        if callable(cb):
            try:
                cb(self.is_active())
            except Exception:
                log.exception("Tray: on_active_changed callback failed")

    def _cancel_watcher_watches(self) -> None:
        for watch_id in self._watcher_watch_ids:
            Gio.bus_unwatch_name(watch_id)
        self._watcher_watch_ids.clear()

    def stop(self) -> None:
        if self._conn and self._reg_id:
            self._conn.unregister_object(self._reg_id)
        if self._conn and self._objmgr_reg_id:
            self._conn.unregister_object(self._objmgr_reg_id)
        if self._conn and self._dbusmenu_reg_id:
            self._conn.unregister_object(self._dbusmenu_reg_id)
        if self._name_id:
            Gio.bus_unown_name(self._name_id)
            self._name_id = 0
        self._cancel_watcher_watches()
        self._reg_id = 0
        self._objmgr_reg_id = 0
        self._dbusmenu_reg_id = 0
        self._name_id = 0
        self._protocol = ""
        self._sni_active = False
        self._sni_pending = False
        self._active_watcher = ""

    def is_active(self) -> bool:
        if self._protocol == "sni":
            # Only count as active once a watcher has accepted the item —
            # having the objects on the bus shows nothing on screen.
            return self._sni_active
        return self._reg_id > 0

    def set_status(self, syncing: bool) -> None:
        self._tooltip = "CloudSync — syncing…" if syncing else "CloudSync — Google Drive sync"
        if self._protocol == "xsi":
            self._emit_props_changed(
                "org.x.StatusIcon", _XSI_OBJECT_PATH, {"TooltipText": GLib.Variant("s", self._tooltip)}
            )
        elif self._protocol == "sni":
            self._emit_signal(_SNI_OBJECT_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)

    # ------------------------------------------------------------------ #
    # Cinnamon org.x.StatusIcon                                            #
    # ------------------------------------------------------------------ #

    def _start_xsi(self) -> None:
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_XSI_IFACE_XML)
        self._objmgr_node_info = Gio.DBusNodeInfo.new_for_xml(_OBJMGR_IFACE_XML)
        iface = self._node_info.interfaces[0]
        objmgr_iface = self._objmgr_node_info.interfaces[0]
        self._name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            _XSI_BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            lambda conn, name: self._xsi_bus_acquired(conn, name, iface, objmgr_iface),
            lambda conn, name: log.debug("Tray XSI name acquired: %s", name),
            lambda conn, name: log.warning("Tray XSI name lost/denied: %s", name),
        )
        log.debug("Tray XSI: bus_own_name registered, waiting for callbacks…")

    def _xsi_bus_acquired(self, conn: Gio.DBusConnection, name: str, iface, objmgr_iface) -> None:
        self._conn = conn
        try:
            # Register ObjectManager at root so libxapp's GDBusObjectManager succeeds
            self._objmgr_reg_id = conn.register_object(
                _XSI_ROOT_PATH, objmgr_iface,
                self._objmgr_method, None, lambda *_: False,
            )
            self._reg_id = conn.register_object(
                _XSI_OBJECT_PATH, iface,
                self._xsi_method, self._xsi_get_prop, lambda *_: False,
            )
            log.debug("Tray XSI registered at %s (reg_id=%d)", _XSI_OBJECT_PATH, self._reg_id)
            self._notify_active_changed()
        except Exception as exc:
            log.error("Tray XSI: register_object failed: %s", exc)

    def _objmgr_method(self, conn, sender, path, iface, method, params, invocation) -> None:
        if method == "GetManagedObjects":
            props = {
                "Name":                GLib.Variant("s", "cloudsync"),
                "IconName":            GLib.Variant("s", _icon_name()),
                "TooltipText":         GLib.Variant("s", self._tooltip),
                "Label":               GLib.Variant("s", ""),
                "Visible":             GLib.Variant("b", True),
                "IconSize":            GLib.Variant("i", 22),
                "PrimaryMenuIsOpen":   GLib.Variant("b", False),
                "SecondaryMenuIsOpen": GLib.Variant("b", False),
                "Metadata":            GLib.Variant("s", ""),
            }
            result = GLib.Variant(
                "(a{oa{sa{sv}}})",
                ({_XSI_OBJECT_PATH: {"org.x.StatusIcon": props}},),
            )
            invocation.return_value(result)
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _xsi_method(self, conn, sender, path, iface, method, params, invocation) -> None:
        if method == "ButtonRelease":
            _x, _y, button, _time, panel_pos = params
            if button == 3:
                GLib.idle_add(self._show_menu, _x, _y, panel_pos)
            else:
                GLib.idle_add(self._action_open)
        invocation.return_value(None)

    def _xsi_get_prop(self, conn, sender, path, iface, prop) -> GLib.Variant | None:
        return {
            "Name":                GLib.Variant("s", "cloudsync"),
            "IconName":            GLib.Variant("s", _icon_name()),
            "TooltipText":         GLib.Variant("s", self._tooltip),
            "Label":               GLib.Variant("s", ""),
            "Visible":             GLib.Variant("b", True),
            "IconSize":            GLib.Variant("i", 22),
            "PrimaryMenuIsOpen":   GLib.Variant("b", False),
            "SecondaryMenuIsOpen": GLib.Variant("b", False),
            "Metadata":            GLib.Variant("s", ""),
        }.get(prop)

    # ------------------------------------------------------------------ #
    # SNI (KDE / XFCE / MATE / Wayland)                                   #
    # ------------------------------------------------------------------ #

    def _register_sni_objects(self, conn: Gio.DBusConnection) -> None:
        """Export the SNI item and DBusMenu objects on the session bus.

        Done once, independently of any watcher: the objects sit on the bus
        and watchers are pointed at them via RegisterStatusNotifierItem as
        they (re)appear.
        """
        self._conn = conn
        self._node_info = Gio.DBusNodeInfo.new_for_xml(_SNI_IFACE_XML)
        self._dbusmenu_node_info = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_IFACE_XML)
        try:
            self._reg_id = conn.register_object(
                _SNI_OBJECT_PATH, self._node_info.interfaces[0],
                self._sni_method, self._sni_get_prop, lambda *_: False,
            )
            self._dbusmenu_reg_id = conn.register_object(
                _DBUSMENU_PATH, self._dbusmenu_node_info.interfaces[0],
                self._dbusmenu_method, self._dbusmenu_get_prop, lambda *_: False,
            )
            log.debug("Tray SNI registered at %s (reg_id=%d), DBusMenu at %s",
                      _SNI_OBJECT_PATH, self._reg_id, _DBUSMENU_PATH)
        except Exception as exc:
            log.error("Tray SNI: register_object failed: %s", exc)

    def _register_with_watcher(self) -> None:
        # We pass the app's own well-known name (com.seravault.cloudsync)
        # to RegisterStatusNotifierItem; Flatpak automatically allows the proxy
        # to forward calls addressed to the app's own ID, so the watcher can
        # reach /StatusNotifierItem on the app's existing bus connection.
        self._sni_pending = True
        try:
            self._conn.call(
                self._active_watcher,
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (APP_ID,)),  # app's own well-known name; proxy always allows calls to it
                None,
                Gio.DBusCallFlags.NONE,
                -1,
                None,
                self._sni_registered,
                None,
            )
        except Exception as exc:
            self._sni_pending = False
            log.warning("Tray SNI: could not call RegisterStatusNotifierItem: %s", exc)

    def _sni_registered(self, conn, result, user_data) -> None:
        self._sni_pending = False
        try:
            conn.call_finish(result)
            log.debug("Tray SNI: registered with watcher %s", self._active_watcher)
            self._set_sni_active(True)
        except Exception as exc:
            log.warning("Tray SNI: watcher registration failed: %s", exc)

    def _sni_method(self, conn, sender, path, iface, method, params, invocation) -> None:
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self._action_open)
        elif method == "ContextMenu":
            # Hosts that don't support DBusMenu fall back to sending coordinates.
            x, y = params
            GLib.idle_add(self._show_menu, x, y, 0)
        invocation.return_value(None)

    def _sni_get_prop(self, conn, sender, path, iface, prop) -> GLib.Variant | None:
        return {
            "Category":           GLib.Variant("s", "ApplicationStatus"),
            "Id":                 GLib.Variant("s", "cloudsync"),
            "Title":              GLib.Variant("s", "CloudSync"),
            "Status":             GLib.Variant("s", "Active"),
            "WindowId":           GLib.Variant("i", 0),
            "IconName":           GLib.Variant("s", _icon_name()),
            "IconPixmap":         GLib.Variant("a(iiay)", []),
            "OverlayIconName":    GLib.Variant("s", ""),
            "AttentionIconName":  GLib.Variant("s", ""),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip":            GLib.Variant("(sa(iiay)ss)", ("", [], "CloudSync", self._tooltip)),
            "ItemIsMenu":         GLib.Variant("b", False),
            "IconThemePath":      GLib.Variant("s", _icon_theme_path()),
            "Menu":               GLib.Variant("o", _DBUSMENU_PATH),
        }.get(prop)

    # ------------------------------------------------------------------ #
    # DBusMenu (com.canonical.dbusmenu)                                    #
    # ------------------------------------------------------------------ #

    def _dbusmenu_items(self) -> list[tuple[int, dict]]:
        """Return the flat menu item list as (id, properties) pairs."""
        win = self._app._window
        open_label = (
            "Hide CloudSync"
            if (win and win.get_visible() and win.is_active())
            else "Open CloudSync"
        )
        return [
            (_MENU_ID_OPEN, {"label": GLib.Variant("s", open_label),
                             "enabled": GLib.Variant("b", True),
                             "visible": GLib.Variant("b", True)}),
            (_MENU_ID_SYNC, {"label": GLib.Variant("s", "Sync Now"),
                             "enabled": GLib.Variant("b", True),
                             "visible": GLib.Variant("b", True)}),
            (_MENU_ID_SEP,  {"type":    GLib.Variant("s", "separator"),
                             "enabled": GLib.Variant("b", True),
                             "visible": GLib.Variant("b", True)}),
            (_MENU_ID_QUIT, {"label": GLib.Variant("s", "Quit"),
                             "enabled": GLib.Variant("b", True),
                             "visible": GLib.Variant("b", True)}),
        ]

    @staticmethod
    def _layout_signature(items: list[tuple[int, dict]]) -> tuple:
        """Hashable snapshot of the menu used to detect stale host caches."""
        return tuple(str(props.get("label", "")) for _id, props in items)

    # Hosts (KDE in particular) need children-display on the root node to
    # treat it as a menu container.
    _ROOT_PROPS_TMPL = {"children-display": "submenu"}

    def _root_props(self) -> dict:
        return {k: GLib.Variant("s", v) for k, v in self._ROOT_PROPS_TMPL.items()}

    def _dbusmenu_method(self, conn, sender, path, iface, method, params, invocation) -> None:
        if method == "GetLayout":
            parent_id, _depth, _prop_names = params
            items = self._dbusmenu_items()
            self._layout_sig = self._layout_signature(items)
            if parent_id == _MENU_ID_ROOT:
                # NOTE: do not wrap in GLib.Variant("v", …) — PyGObject boxes
                # each "av" element into a variant itself, and an explicit
                # wrapper produces v(v(tuple)) on the wire, which GJS-based
                # hosts (GNOME appindicator extension) fail to unpack.
                children = [
                    GLib.Variant("(ia{sv}av)", (item_id, props, []))
                    for item_id, props in items
                ]
                node = (_MENU_ID_ROOT, self._root_props(), children)
            else:
                # Flat menu: any non-root id is a leaf with no children.
                node = (parent_id, dict(items).get(parent_id, {}), [])
            invocation.return_value(GLib.Variant(
                "(u(ia{sv}av))",
                (self._menu_revision, node),
            ))

        elif method == "GetGroupProperties":
            ids, prop_names = params
            result = []
            all_items = {i: p for i, p in self._dbusmenu_items()}
            all_items[_MENU_ID_ROOT] = self._root_props()
            for item_id in ids:
                props = all_items.get(item_id, {})
                if prop_names:
                    props = {k: v for k, v in props.items() if k in prop_names}
                result.append((item_id, props))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))

        elif method == "GetProperty":
            item_id, prop_name = params
            all_items = {i: p for i, p in self._dbusmenu_items()}
            props = all_items.get(item_id, {})
            value = props.get(prop_name, GLib.Variant("s", ""))
            invocation.return_value(GLib.Variant("(v)", (value,)))

        elif method == "AboutToShow":
            # The "Open/Hide CloudSync" label depends on current window state;
            # hosts cache the layout, so tell them to refetch when it changed.
            changed = self._layout_signature(self._dbusmenu_items()) != self._layout_sig
            if changed:
                self._menu_revision += 1
                self._emit_signal(
                    _DBUSMENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                    GLib.Variant("(ui)", (self._menu_revision, _MENU_ID_ROOT)),
                )
            invocation.return_value(GLib.Variant("(b)", (changed,)))

        elif method == "AboutToShowGroup":
            ids, = params
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))

        elif method == "Event":
            item_id, event_id, _data, _ts = params
            if event_id == "clicked":
                if item_id == _MENU_ID_OPEN:
                    GLib.idle_add(self._action_open)
                elif item_id == _MENU_ID_SYNC:
                    GLib.idle_add(self._app.trigger_sync)
                elif item_id == _MENU_ID_QUIT:
                    GLib.idle_add(self._app.quit)
            invocation.return_value(None)

        elif method == "EventGroup":
            events, = params
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    if item_id == _MENU_ID_OPEN:
                        GLib.idle_add(self._action_open)
                    elif item_id == _MENU_ID_SYNC:
                        GLib.idle_add(self._app.trigger_sync)
                    elif item_id == _MENU_ID_QUIT:
                        GLib.idle_add(self._app.quit)
            invocation.return_value(GLib.Variant("(ai)", ([],)))

        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _dbusmenu_get_prop(self, conn, sender, path, iface, prop) -> GLib.Variant | None:
        return {
            "Version":       GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status":        GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(prop)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _emit_props_changed(self, iface_name: str, path: str, changed: dict) -> None:
        if not (self._conn and self._reg_id):
            return
        self._conn.emit_signal(
            None, path,
            "org.freedesktop.DBus.Properties", "PropertiesChanged",
            GLib.Variant("(sa{sv}as)", (iface_name, changed, [])),
        )

    def _emit_signal(self, path: str, iface: str, signal: str, params) -> None:
        if not (self._conn and self._reg_id):
            return
        self._conn.emit_signal(None, path, iface, signal, params)

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _close_menu(self) -> None:
        """Close the popup menu if it is open."""
        menu_win = getattr(self, "_menu_win", None)
        if menu_win:
            try:
                menu_win.close()
            except Exception:
                pass
            self._menu_win = None

    def _show_menu(self, x: int, y: int, panel_pos: int = 0) -> None:
        """Show (or dismiss) the popup context menu near the tray icon.

        A second right-click while the menu is open closes it instead of
        opening a second copy.

        panel_pos follows GtkPositionType: 0=LEFT, 1=RIGHT, 2=TOP, 3=BOTTOM.
        Positioning uses X11 override-redirect + XMoveWindow on X11, and a
        best-effort default-size hint on Wayland.  Size is measured after the
        first layout pass via a GLib idle callback so GTK reports the real
        allocated size.
        """
        import ctypes
        from gi.repository import Gdk, Gtk

        # Toggle: dismiss if already open
        if getattr(self, "_menu_win", None):
            self._close_menu()
            return

        # Build the menu window
        menu_win = Gtk.Window()
        menu_win.set_decorated(False)
        menu_win.set_resizable(False)
        menu_win.set_application(self._app)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu_win.set_child(box)

        def add_item(label: str, cb) -> None:
            btn = Gtk.Button(label=label)
            btn.set_has_frame(False)
            btn.connect("clicked", lambda _b: (self._close_menu(), GLib.idle_add(cb)))
            box.append(btn)

        app_win = self._app._window
        open_label = (
            "Hide CloudSync"
            if (app_win and app_win.get_visible() and app_win.is_active())
            else "Open CloudSync"
        )
        add_item(open_label, self._action_open)
        add_item("Sync Now", self._app.trigger_sync)
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        add_item("Quit", self._app.quit)

        # Close on focus loss
        fc = Gtk.EventControllerFocus.new()
        fc.connect("leave", lambda _c: GLib.idle_add(self._close_menu))
        menu_win.add_controller(fc)

        # Track the window before presenting so the toggle check above works
        self._menu_win = menu_win

        # Realize the window (creates the native surface, does NOT map/show it)
        # so we can measure and position before the first paint.
        menu_win.realize()

        # Force a layout pass so measure() returns real values
        menu_win.queue_resize()
        display = Gdk.Display.get_default()
        if display:
            display.sync()

        _, h_nat, _, _ = menu_win.measure(Gtk.Orientation.VERTICAL, -1)
        _, w_nat, _, _ = menu_win.measure(Gtk.Orientation.HORIZONTAL, -1)
        menu_h = h_nat if h_nat > 10 else 130
        menu_w = w_nat if w_nat > 10 else 200

        # Compute position relative to the tray click
        if panel_pos == 2:      # top panel → open below
            menu_x, menu_y = x, y + 2
        elif panel_pos == 0:    # left panel → open right
            menu_x, menu_y = x + 2, y
        elif panel_pos == 1:    # right panel → open left
            menu_x, menu_y = x - menu_w - 2, y
        else:                   # bottom panel (3) → open above
            menu_x, menu_y = x, y - menu_h - 2

        # Clamp to the monitor containing the click point
        if display:
            monitors = display.get_monitors()
            for i in range(monitors.get_n_items()):
                mon = monitors.get_item(i)
                geo = mon.get_geometry()
                if (geo.x <= x < geo.x + geo.width and
                        geo.y <= y < geo.y + geo.height):
                    menu_x = max(geo.x, min(menu_x, geo.x + geo.width  - menu_w))
                    menu_y = max(geo.y, min(menu_y, geo.y + geo.height - menu_h))
                    break

        log.debug("Tray menu: click=(%d,%d) panel=%d size=(%d,%d) → (%d,%d)",
                  x, y, panel_pos, menu_w, menu_h, menu_x, menu_y)

        # Apply X11 override_redirect and position BEFORE present() so the
        # window manager never gets a chance to move the window.
        try:
            from gi.repository import GdkX11
            surface = menu_win.get_surface()
            if isinstance(surface, GdkX11.X11Surface):
                xid = surface.get_xid()
                xlib = ctypes.CDLL("libX11.so.6")
                xlib.XOpenDisplay.restype = ctypes.c_void_p
                xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                dpy = xlib.XOpenDisplay(None)
                if dpy:
                    class _XAttr(ctypes.Structure):
                        _fields_ = [
                            ("background_pixmap",     ctypes.c_ulong),
                            ("background_pixel",      ctypes.c_ulong),
                            ("border_pixmap",         ctypes.c_ulong),
                            ("border_pixel",          ctypes.c_ulong),
                            ("bit_gravity",           ctypes.c_int),
                            ("win_gravity",           ctypes.c_int),
                            ("backing_store",         ctypes.c_int),
                            ("_p0",                   ctypes.c_int),
                            ("backing_planes",        ctypes.c_ulong),
                            ("backing_pixel",         ctypes.c_ulong),
                            ("save_under",            ctypes.c_int),
                            ("_p1",                   ctypes.c_int),
                            ("event_mask",            ctypes.c_long),
                            ("do_not_propagate_mask", ctypes.c_long),
                            ("override_redirect",     ctypes.c_int),
                        ]
                    CWOverrideRedirect = 1 << 9
                    attr = _XAttr()
                    attr.override_redirect = 1
                    xlib.XChangeWindowAttributes.argtypes = [
                        ctypes.c_void_p, ctypes.c_ulong,
                        ctypes.c_ulong, ctypes.c_void_p,
                    ]
                    xlib.XMoveWindow.argtypes = [
                        ctypes.c_void_p, ctypes.c_ulong,
                        ctypes.c_int, ctypes.c_int,
                    ]
                    xlib.XFlush.argtypes = [ctypes.c_void_p]
                    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
                    xlib.XChangeWindowAttributes(dpy, xid, CWOverrideRedirect,
                                                 ctypes.byref(attr))
                    xlib.XMoveWindow(dpy, xid, menu_x, menu_y)
                    xlib.XFlush(dpy)
                    xlib.XCloseDisplay(dpy)
            else:
                # Wayland: hint at size, compositor places it best-effort
                menu_win.set_default_size(menu_w, menu_h)
        except Exception as exc:
            log.debug("Tray menu positioning: %s", exc)

        menu_win.present()

    def _action_open(self) -> None:
        win = self._app._window
        if not win:
            return
        if win.get_visible() and win.is_active():
            win.hide()
        else:
            win.present()
