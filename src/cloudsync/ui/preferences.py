"""Preferences window (sync settings, conflict resolution, etc.)."""
from __future__ import annotations

import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core.config import (  # noqa: E402
    CONFLICT_LABELS,
    CONFLICT_OPTIONS,
    INTERVAL_LABELS,
    INTERVAL_OPTIONS,
    Config,
)
from ..core.autostart import (  # noqa: E402
    is_enabled as autostart_is_enabled,
    set_enabled as autostart_set_enabled,
)
from ..core import license as lic  # noqa: E402


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, config: Config, on_save: callable, **kwargs):
        super().__init__(title="Preferences", **kwargs)
        self.set_search_enabled(False)
        self._config = config
        self._on_save = on_save

        self._build_sync_page()
        self._build_advanced_page()
        self._build_subscription_page()

    # ------------------------------------------------------------------ #
    # Pages                                                                #
    # ------------------------------------------------------------------ #

    def _build_sync_page(self) -> None:
        page = Adw.PreferencesPage(
            title="Sync",
            icon_name="emblem-synchronizing-symbolic",
        )
        self.add(page)

        # --- Sync interval ---
        group = Adw.PreferencesGroup(
            title="Default Sync Interval",
            description=(
                "Used for any sync folder that does not have its own "
                "interval set."
            ),
        )
        page.add(group)

        self._interval_row = Adw.ComboRow(title="Check for changes every")
        model = Gtk.StringList()
        for label in INTERVAL_LABELS:
            model.append(label)
        self._interval_row.set_model(model)
        try:
            idx = INTERVAL_OPTIONS.index(self._config.sync_interval_seconds)
        except ValueError:
            idx = 1
        self._interval_row.set_selected(idx)
        self._interval_row.connect(
            "notify::selected", self._on_interval_changed
        )
        group.add(self._interval_row)

        # --- Notifications ---
        notif_group = Adw.PreferencesGroup(title="Notifications")
        page.add(notif_group)

        self._notif_row = Adw.SwitchRow(
            title="Desktop notifications",
            subtitle=(
                "Show a notification when sync completes or encounters "
                "an error"
            ),
        )
        self._notif_row.set_active(self._config.notifications_enabled)
        self._notif_row.connect("notify::active", self._on_notif_changed)
        notif_group.add(self._notif_row)

        # --- Start on login ---
        startup_group = Adw.PreferencesGroup(title="Startup")
        page.add(startup_group)

        self._startup_row = Adw.SwitchRow(
            title="Start on login",
            subtitle="Launch automatically when you sign into your desktop",
        )
        self._startup_row.set_active(autostart_is_enabled())
        self._startup_row.connect("notify::active", self._on_startup_changed)
        startup_group.add(self._startup_row)

    def _build_advanced_page(self) -> None:
        page = Adw.PreferencesPage(
            title="Advanced",
            icon_name="preferences-other-symbolic",
        )
        self.add(page)

        # --- Conflict resolution ---
        group = Adw.PreferencesGroup(
            title="Default Conflict Resolution",
            description=(
                "Applied to any sync folder that does not have its own "
                "conflict strategy set."
            ),
        )
        page.add(group)

        self._conflict_row = Adw.ComboRow(title="Conflict strategy")
        model = Gtk.StringList()
        for label in CONFLICT_LABELS:
            model.append(label)
        self._conflict_row.set_model(model)
        try:
            idx = CONFLICT_OPTIONS.index(self._config.conflict_resolution)
        except ValueError:
            idx = 0
        self._conflict_row.set_selected(idx)
        self._conflict_row.connect(
            "notify::selected", self._on_conflict_changed
        )
        group.add(self._conflict_row)

        # --- Conflict resolution help ----------------------------------- #
        help_group = Adw.PreferencesGroup(title="What each strategy does")
        page.add(help_group)

        _help = [
            (
                "Keep both copies",
                "Your local version is renamed with a .conflict_TIMESTAMP "
                "suffix and uploaded alongside the remote copy. The remote "
                "version is then downloaded to the original filename. Both "
                "versions end up on all devices — you resolve manually.",
            ),
            (
                "Local copy wins",
                "Your local version overwrites the remote file. "
                "Any changes made on other devices since the last sync "
                "are discarded.",
            ),
            (
                "Remote copy wins",
                "The remote version overwrites your local file. "
                "Any local changes made since the last sync are discarded.",
            ),
        ]
        for title, body in _help:
            row = Adw.ActionRow(title=title, subtitle=body)
            row.set_activatable(False)
            row.set_subtitle_lines(0)
            help_group.add(row)

    # ------------------------------------------------------------------ #
    # Callbacks                                                            #
    # ------------------------------------------------------------------ #

    def _on_interval_changed(self, row, _param) -> None:
        self._config.sync_interval_seconds = INTERVAL_OPTIONS[
            row.get_selected()
        ]
        self._on_save(self._config)

    def _on_notif_changed(self, row, _param) -> None:
        self._config.notifications_enabled = row.get_active()
        self._on_save(self._config)

    def _on_conflict_changed(self, row, _param) -> None:
        self._config.conflict_resolution = CONFLICT_OPTIONS[
            row.get_selected()
        ]
        self._on_save(self._config)

    def _on_startup_changed(self, row, _param) -> None:
        enabled = row.get_active()
        self._config.start_on_login = enabled
        autostart_set_enabled(enabled)
        self._on_save(self._config)

    # ------------------------------------------------------------------ #
    # Subscription page                                                    #
    # ------------------------------------------------------------------ #

    def _build_subscription_page(self) -> None:
        page = Adw.PreferencesPage(
            name="subscription",
            title="Subscription",
            icon_name="security-high-symbolic",
        )
        self.add(page)

        # --- Status group ----------------------------------------------- #
        status_group = Adw.PreferencesGroup(title="Subscription Status")
        page.add(status_group)

        self._status_row = Adw.ActionRow(title="Checking…", subtitle="")
        self._status_row.set_activatable(False)
        status_group.add(self._status_row)

        threading.Thread(target=self._refresh_status, daemon=True).start()

        # --- Sign-in group ---------------------------------------------- #
        stored = lic.load()
        signin_group = Adw.PreferencesGroup(
            title="Account",
            description=(
                "Enter the email address used to purchase your "
                "subscription. The free tier is limited to 1 provider "
                "and 1 sync folder."
            ),
        )
        page.add(signin_group)

        self._email_row = Adw.EntryRow(title="Email address")
        self._email_row.set_input_purpose(Gtk.InputPurpose.EMAIL)
        if stored.email:
            self._email_row.set_text(stored.email)
        signin_group.add(self._email_row)

        btn_row = Adw.ActionRow()
        self._signin_btn = Gtk.Button(
            label="Sign In",
            css_classes=["suggested-action"],
            valign=Gtk.Align.CENTER,
        )
        self._signin_btn.connect("clicked", self._on_signin_clicked)
        btn_row.add_suffix(self._signin_btn)

        self._signout_btn = Gtk.Button(
            label="Sign Out",
            css_classes=["destructive-action"],
            valign=Gtk.Align.CENTER,
            visible=stored.is_signed_in,
        )
        self._signout_btn.connect("clicked", self._on_signout_clicked)
        btn_row.add_suffix(self._signout_btn)
        signin_group.add(btn_row)

        # --- Buy link --------------------------------------------------- #
        buy_group = Adw.PreferencesGroup(title="Don't have a subscription?")
        page.add(buy_group)

        buy_row = Adw.ActionRow(
            title="Purchase a subscription",
            subtitle=lic.SUBSCRIBE_URL,
            activatable=True,
        )
        buy_row.add_suffix(Gtk.Image(icon_name="external-link-symbolic"))
        buy_row.connect(
            "activated",
            lambda _: self._open_url(lic.SUBSCRIBE_URL),
        )
        buy_group.add(buy_row)

    # ------------------------------------------------------------------ #
    # Subscription callbacks                                               #
    # ------------------------------------------------------------------ #

    def _refresh_status(self) -> None:
        """Background thread: validate stored email and update the UI."""
        info = lic.load()
        if not info.is_signed_in:
            title, subtitle = info.status_summary()
            GLib.idle_add(self._set_status, title, subtitle, False)
            return
        active, _ = lic.validate()
        info = lic.load()
        title, subtitle = info.status_summary()
        GLib.idle_add(self._set_status, title, subtitle, active)

    def _set_status(self, title: str, subtitle: str, active: bool) -> None:
        self._status_row.set_title(title)
        self._status_row.set_subtitle(subtitle)
        icon = "emblem-ok-symbolic" if active else "dialog-warning-symbolic"
        img = Gtk.Image(icon_name=icon)
        for child in list(self._status_row.observe_children()):
            if isinstance(child, Gtk.Image):
                self._status_row.remove(child)
        self._status_row.add_suffix(img)
        info = lic.load()
        self._signout_btn.set_visible(info.is_signed_in)

    def _on_signin_clicked(self, _btn) -> None:
        email = self._email_row.get_text().strip()
        self._signin_btn.set_sensitive(False)
        self._signin_btn.set_label("Signing in…")

        def _work():
            ok, msg = lic.sign_in(email)
            GLib.idle_add(self._after_signin, ok, msg)

        threading.Thread(target=_work, daemon=True).start()

    def _after_signin(self, ok: bool, msg: str) -> None:
        self._signin_btn.set_sensitive(True)
        self._signin_btn.set_label("Sign In")
        dialog = Adw.AlertDialog(heading="Subscription", body=msg)
        dialog.add_response("ok", "OK")
        dialog.present(self)
        if ok:
            threading.Thread(
                target=self._refresh_status, daemon=True
            ).start()

    def _on_signout_clicked(self, _btn) -> None:
        dialog = Adw.AlertDialog(
            heading="Sign Out?",
            body=(
                "This will remove your account from this machine. "
                "The app will revert to the free tier: "
                "1 provider and 1 sync folder."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("signout", "Sign Out")
        dialog.set_response_appearance(
            "signout", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.connect("response", self._on_signout_confirmed)
        dialog.present(self)

    def _on_signout_confirmed(self, _dialog, response: str) -> None:
        if response != "signout":
            return
        lic.sign_out()
        self._email_row.set_text("")
        threading.Thread(target=self._refresh_status, daemon=True).start()

    @staticmethod
    def _open_url(url: str) -> None:
        subprocess.Popen(["xdg-open", url])
