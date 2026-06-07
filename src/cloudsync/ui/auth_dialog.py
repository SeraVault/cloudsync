"""In-app OAuth2 login dialog using an embedded WebKit webview.

Shows Google's login page inside a native GNOME dialog.  When the OAuth
redirect fires (``http://localhost/?code=…``) the dialog intercepts it,
extracts the auth code, and resolves the flow — never leaving the app.

WebKit availability
-------------------
Tries, in order:
  1. WebKit  6.0  (GTK 4, newer distros)
  2. WebKit2 4.1  (GTK 4, transitional)
  3. WebKit2 4.0  (GTK 3 era — may work under GTK 4 with compatibility layer)

Falls back to the external-browser flow if none are found.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WebKit import — try modern → legacy
# ---------------------------------------------------------------------------
_webkit_module: Optional[str] = None
WebKit = None  # type: ignore

for _ver, _mod in [("6.0", "WebKit"), ("4.1", "WebKit2"), ("4.0", "WebKit2")]:
    try:
        gi.require_version(_mod, _ver)
        from gi.repository import WebKit2 as _wk  # noqa: F401 — just probing
        WebKit = _wk
        _webkit_module = f"{_mod} {_ver}"
        break
    except (ValueError, ImportError):
        pass

# Also try the GTK4-native "WebKit" name at 6.0
if WebKit is None:
    try:
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit as _wk  # noqa: F401
        WebKit = _wk
        _webkit_module = "WebKit 6.0"
    except (ValueError, ImportError):
        pass

WEBKIT_AVAILABLE = WebKit is not None


def has_webkit() -> bool:
    return WEBKIT_AVAILABLE


# ---------------------------------------------------------------------------
# Auth dialog
# ---------------------------------------------------------------------------

class AuthDialog(Adw.Window):
    """Modal window containing a WebKit webview for an OAuth2 sign-in flow.

    By default the callback receives only the ``code`` query parameter from the
    redirect URL.  Pass ``capture_full_url=True`` to receive the complete
    redirect URL instead — needed for providers (e.g. OneDrive / MSAL) whose
    token-exchange call requires the full URL with all query parameters.
    """

    _REDIRECT_SCHEME = "http://localhost"

    def __init__(
        self,
        auth_url: str,
        on_code: Callable[[str], None],
        on_cancel: Callable[[], None],
        parent: Gtk.Window,
        title: str = "Sign in",
        capture_full_url: bool = False,
        width: int = 960,
        height: int = 760,
    ):
        super().__init__(
            title=title,
            modal=True,
            transient_for=parent,
            default_width=width,
            default_height=height,
            resizable=True,
        )
        self._on_code = on_code
        self._on_cancel = on_cancel
        self._capture_full_url = capture_full_url

        # Toolbar + header
        toolbar_view = Adw.ToolbarView()
        self.set_content(toolbar_view)

        header = Adw.HeaderBar(show_title=False)
        toolbar_view.add_top_bar(header)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", self._on_cancel_clicked)
        header.pack_start(cancel_btn)

        self._spinner = Gtk.Spinner()
        self._spinner.start()
        header.pack_end(self._spinner)

        # WebKit webview
        self._webview = WebKit.WebView()
        settings = self._webview.get_settings()
        settings.set_javascript_enabled(True)
        settings.set_allow_universal_access_from_file_urls(False)
        self._webview.connect("decide-policy", self._on_decide_policy)
        self._webview.connect("load-changed", self._on_load_changed)
        toolbar_view.set_content(self._webview)

        self._webview.load_uri(auth_url)
        self.connect("close-request", self._on_close_request)

    # ------------------------------------------------------------------ #
    # WebKit callbacks                                                     #
    # ------------------------------------------------------------------ #

    def _on_decide_policy(self, _view, decision, decision_type) -> bool:
        """Intercept the OAuth redirect to localhost."""
        if hasattr(WebKit, "PolicyDecisionType"):
            nav_type = WebKit.PolicyDecisionType.NAVIGATION_ACTION
        else:
            nav_type = WebKit.WebKit2.PolicyDecisionType.NAVIGATION_ACTION

        if decision_type != nav_type:
            return False  # let default handle it

        nav = decision.get_navigation_action()
        uri = nav.get_request().get_uri()

        if uri.startswith(self._REDIRECT_SCHEME + "/?") or uri.startswith(
            self._REDIRECT_SCHEME + "/"
        ):
            parsed = urllib.parse.urlparse(uri)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                decision.ignore()
                value = uri if self._capture_full_url else params["code"][0]
                GLib.idle_add(self._finish, value)
                return True

        return False

    def _on_load_changed(self, _view, event) -> None:
        finished = (
            event == WebKit.LoadEvent.FINISHED
            if hasattr(WebKit, "LoadEvent")
            else event == WebKit.WebKit2.LoadEvent.FINISHED
        )
        if finished:
            self._spinner.stop()
            self._spinner.set_visible(False)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _finish(self, code: str) -> None:
        self.close()
        self._on_code(code)

    def _on_cancel_clicked(self, _btn) -> None:
        self.close()
        self._on_cancel()

    def _on_close_request(self, _win) -> bool:
        self._on_cancel()
        return False  # allow close
