"""Dialog for editing and testing an existing provider account's credentials."""
from __future__ import annotations

import logging
import threading
from typing import Callable, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

if TYPE_CHECKING:
    from ..app import CloudSyncApp

log = logging.getLogger(__name__)


class EditAccountDialog(Adw.Window):
    """Modal dialog for editing a connected provider account.

    For S3: shows editable credential fields with a Test button.
    For Google Drive / OneDrive / Dropbox: shows account info and a
    Re-authenticate button.
    """

    def __init__(
        self,
        app: "CloudSyncApp",
        provider: str,
        on_saved: Callable[[], None],
        parent: Gtk.Window,
    ):
        super().__init__(
            title="Edit Account",
            modal=True,
            transient_for=parent,
            default_width=480,
            resizable=False,
        )
        self._app = app
        self._provider = provider
        self._on_saved = on_saved

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        if provider == "s3":
            toolbar.set_content(self._build_s3_content())
        else:
            toolbar.set_content(self._build_oauth_content())

    # ------------------------------------------------------------------ #
    # S3 credential editor                                                 #
    # ------------------------------------------------------------------ #

    def _build_s3_content(self) -> Gtk.Widget:
        auth = self._app.s3_auth
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16, margin_bottom=16,
            margin_start=16, margin_end=16,
        )

        group = Adw.PreferencesGroup(title="AWS Credentials")
        box.append(group)

        help_group = Adw.PreferencesGroup(
            title="Keep this connection working",
            description=(
                "Simple setup steps for most users."
            ),
        )
        box.append(help_group)

        help_text = Gtk.Label(
            xalign=0,
            wrap=True,
            selectable=True,
            label=(
                "1) Get an Access Key ID and Secret Access Key from AWS.\n"
                "2) Enter them here and keep Region correct for your account.\n"
                "3) Click Save — credentials are verified automatically.\n\n"
                "You will choose which bucket and folder to sync when adding a sync folder."
            ),
        )
        help_text.set_margin_top(6)
        help_text.set_margin_bottom(6)
        help_group.add(help_text)

        self._access_row = Adw.EntryRow(title="Access Key ID")
        self._access_row.set_text(auth.access_key)

        self._secret_row = Adw.PasswordEntryRow(title="Secret Access Key")
        self._secret_row.set_text(auth.secret_key)

        self._region_row = Adw.EntryRow(title="Region")
        self._region_row.set_text(auth.region or "us-east-1")

        self._endpoint_row = Adw.EntryRow(title="Custom Endpoint (optional)")
        self._endpoint_row.set_text(auth.endpoint_url or "")
        self._endpoint_row.set_tooltip_text(
            "Leave blank for AWS. Set for MinIO, Backblaze B2, etc."
        )

        for row in (self._access_row, self._secret_row,
                    self._region_row, self._endpoint_row):
            group.add(row)

        # Status label (test result)
        self._status_label = Gtk.Label(
            label="",
            halign=Gtk.Align.CENTER,
            wrap=True,
            max_width_chars=50,
        )
        box.append(self._status_label)

        # Buttons
        btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        box.append(btn_box)

        self._test_btn = Gtk.Button(label="Test Connection", css_classes=["pill"])
        self._test_btn.connect("clicked", self._on_test_clicked)
        btn_box.append(self._test_btn)

        self._save_btn = Gtk.Button(
            label="Save", css_classes=["pill", "suggested-action"]
        )
        self._save_btn.connect("clicked", self._on_s3_save_clicked)
        btn_box.append(self._save_btn)

        policy_btn = Gtk.Button(label="Example Bucket Policy", css_classes=["pill"])
        policy_btn.connect("clicked", self._on_s3_policy_example_clicked)
        btn_box.append(policy_btn)

        cancel_btn = Gtk.Button(label="Cancel", css_classes=["pill"])
        cancel_btn.connect("clicked", lambda _: self.close())
        btn_box.append(cancel_btn)

        return box

    def _on_test_clicked(self, _btn) -> None:
        self._set_s3_busy(True, "Testing…")

        access_key = self._access_row.get_text().strip()
        secret_key = self._secret_row.get_text().strip()
        region = self._region_row.get_text().strip() or "us-east-1"
        endpoint = self._endpoint_row.get_text().strip() or None

        def _test():
            try:
                self._app.s3_auth.validate(access_key, secret_key, region, endpoint)
                GLib.idle_add(self._set_test_result, True, "Connection successful.")
            except Exception as exc:
                GLib.idle_add(self._set_test_result, False, str(exc))

        threading.Thread(target=_test, daemon=True).start()

    def _on_s3_save_clicked(self, _btn) -> None:
        self._set_s3_busy(True, "Saving…")

        access_key = self._access_row.get_text().strip()
        secret_key = self._secret_row.get_text().strip()
        region = self._region_row.get_text().strip() or "us-east-1"
        endpoint = self._endpoint_row.get_text().strip() or None

        if not access_key or not secret_key:
            self._set_test_result(False, "Access Key ID and Secret Access Key are required.")
            return

        def _save():
            try:
                self._app.s3_auth.validate(access_key, secret_key, region, endpoint)
                self._app.s3_auth.save(access_key, secret_key, region, endpoint)
                email = self._app.init_provider("s3")
                self._app.add_provider_account("s3", email)
                GLib.idle_add(self._on_saved_ok)
            except Exception as exc:
                GLib.idle_add(self._set_test_result, False, str(exc))

        threading.Thread(target=_save, daemon=True).start()

    def _set_s3_busy(self, busy: bool, label: str = "") -> None:
        self._test_btn.set_sensitive(not busy)
        self._save_btn.set_sensitive(not busy)
        if label:
            self._status_label.set_label(label)
            self._status_label.remove_css_class("success")
            self._status_label.remove_css_class("error")

    def _set_test_result(self, success: bool, message: str) -> None:
        self._set_s3_busy(False)
        self._status_label.set_label(message)
        self._status_label.remove_css_class("success" if not success else "error")
        self._status_label.add_css_class("success" if success else "error")

    def _on_s3_policy_example_clicked(self, _btn) -> None:
        target = self._bucket_row.get_text().strip()
        if not target:
            self._set_test_result(
                False,
                "Enter Bucket or Bucket/Path first (example: my-bucket/photos).",
            )
            return

        from ..core.s3_auth import example_bucket_policy

        try:
            policy = example_bucket_policy(target)
        except Exception as exc:
            self._set_test_result(False, str(exc))
            return

        dialog = Adw.AlertDialog(
            heading="Example Bucket Policy",
            body=(
                "Replace <YOUR_ACCOUNT_ID> with your AWS account ID or IAM "
                "principal."
            ),
        )
        text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        text.get_buffer().set_text(policy)
        text.set_vexpand(True)

        scroller = Gtk.ScrolledWindow(
            min_content_height=220,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        scroller.set_child(text)
        dialog.set_extra_child(scroller)

        dialog.add_response("copy", "Copy")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")

        def _on_response(_dlg, response):
            if response == "copy":
                self.get_clipboard().set(policy)

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _on_saved_ok(self) -> None:
        self.close()
        self._on_saved()

    # ------------------------------------------------------------------ #
    # OAuth provider editor (Google Drive / OneDrive / Dropbox)           #
    # ------------------------------------------------------------------ #

    def _build_oauth_content(self) -> Gtk.Widget:
        from ..ui.window import _PROVIDER_LABELS
        provider_label = _PROVIDER_LABELS.get(self._provider, self._provider)

        if self._provider == "gdrive":
            auth = self._app.auth
            email = auth.user_email or self._app.account_email or "Unknown"
        elif self._provider == "dropbox":
            auth = self._app.dropbox_auth
            email = auth.user_email or "Unknown"
        else:
            auth = self._app.onedrive_auth
            email = auth.user_email or "Unknown"

        status = Adw.StatusPage(
            icon_name="avatar-default-symbolic",
            title=provider_label,
            description=f"Signed in as {email}\n\nTo use a different account, re-authenticate below.",
        )

        self._reauth_btn = Gtk.Button(
            label=f"Re-authenticate with {provider_label}",
            css_classes=["pill", "suggested-action"],
            halign=Gtk.Align.CENTER,
        )
        self._reauth_btn.connect("clicked", self._on_reauth_clicked)
        status.set_child(self._reauth_btn)

        return status

    def _on_reauth_clicked(self, _btn) -> None:
        from .auth_dialog import has_webkit, AuthDialog

        self._reauth_btn.set_sensitive(False)
        self._reauth_btn.set_label("Opening sign-in…")

        if self._provider == "gdrive":
            try:
                auth_url = self._app.auth.build_auth_url()
            except Exception as exc:
                self._reauth_btn.set_sensitive(True)
                self._reauth_btn.set_label("Re-authenticate with Google Drive")
                self._show_error(str(exc))
                return

            if has_webkit():
                def _on_code(code: str):
                    def _exchange():
                        try:
                            self._app.auth.exchange_code(code)
                            email = self._app.init_provider("gdrive")
                            self._app.add_provider_account("gdrive", email)
                            GLib.idle_add(self._on_saved_ok)
                        except Exception as exc:
                            GLib.idle_add(self._show_error, str(exc))
                    threading.Thread(target=_exchange, daemon=True).start()

                AuthDialog(
                    auth_url=auth_url,
                    on_code=_on_code,
                    on_cancel=lambda: GLib.idle_add(
                        self._reauth_btn.set_sensitive, True
                    ),
                    parent=self,
                    title="Re-authenticate with Google",
                ).present()
            else:
                def _ext():
                    try:
                        self._app.auth.authenticate_external()
                        email = self._app.init_provider("gdrive")
                        self._app.add_provider_account("gdrive", email)
                        GLib.idle_add(self._on_saved_ok)
                    except Exception as exc:
                        GLib.idle_add(self._show_error, str(exc))
                threading.Thread(target=_ext, daemon=True).start()

        elif self._provider == "dropbox":
            try:
                auth_url = self._app.dropbox_auth.build_auth_url()
            except Exception as exc:
                self._reauth_btn.set_sensitive(True)
                self._reauth_btn.set_label("Re-authenticate with Dropbox")
                self._show_error(str(exc))
                return

            if has_webkit():
                def _on_redirect(url: str):
                    def _exchange():
                        try:
                            self._app.dropbox_auth.exchange_code(url)
                            email = self._app.init_provider("dropbox")
                            self._app.add_provider_account("dropbox", email)
                            GLib.idle_add(self._on_saved_ok)
                        except Exception as exc:
                            GLib.idle_add(self._show_error, str(exc))
                    threading.Thread(target=_exchange, daemon=True).start()

                AuthDialog(
                    auth_url=auth_url,
                    on_code=_on_redirect,
                    on_cancel=lambda: GLib.idle_add(
                        self._reauth_btn.set_sensitive, True
                    ),
                    parent=self,
                    title="Re-authenticate with Dropbox",
                    capture_full_url=True,
                ).present()
            else:
                def _ext_dbx():
                    try:
                        auth_url = self._app.dropbox_auth.start_external_auth()
                        import webbrowser
                        webbrowser.open(auth_url)
                        GLib.idle_add(self._show_dropbox_code_dialog)
                    except Exception as exc:
                        GLib.idle_add(self._show_error, str(exc))
                threading.Thread(target=_ext_dbx, daemon=True).start()

        else:  # onedrive
            try:
                auth_url = self._app.onedrive_auth.build_auth_url()
            except Exception as exc:
                self._reauth_btn.set_sensitive(True)
                self._reauth_btn.set_label("Re-authenticate with Microsoft OneDrive")
                self._show_error(str(exc))
                return

            if has_webkit():
                def _on_redirect(url: str):
                    def _exchange():
                        try:
                            self._app.onedrive_auth.exchange_code(url)
                            email = self._app.init_provider("onedrive")
                            self._app.add_provider_account("onedrive", email)
                            GLib.idle_add(self._on_saved_ok)
                        except Exception as exc:
                            GLib.idle_add(self._show_error, str(exc))
                    threading.Thread(target=_exchange, daemon=True).start()

                AuthDialog(
                    auth_url=auth_url,
                    on_code=_on_redirect,
                    on_cancel=lambda: GLib.idle_add(
                        self._reauth_btn.set_sensitive, True
                    ),
                    parent=self,
                    title="Re-authenticate with Microsoft",
                    capture_full_url=True,
                ).present()
            else:
                def _ext_od():
                    try:
                        self._app.onedrive_auth.authenticate_external()
                        email = self._app.init_provider("onedrive")
                        self._app.add_provider_account("onedrive", email)
                        GLib.idle_add(self._on_saved_ok)
                    except Exception as exc:
                        GLib.idle_add(self._show_error, str(exc))
                threading.Thread(target=_ext_od, daemon=True).start()

    def _show_dropbox_code_dialog(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Enter Dropbox code",
            body=(
                "Dropbox opened in your browser. After authorizing, "
                "copy the code shown and paste it below."
            ),
        )
        entry = Gtk.Entry(
            placeholder_text="Paste code here",
            activates_default=True,
        )
        entry.set_margin_top(8)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Connect")
        dialog.set_response_appearance(
            "ok", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.set_default_response("ok")

        def _on_response(_dialog, response: str) -> None:
            if response != "ok":
                self._reauth_btn.set_sensitive(True)
                return

            code = entry.get_text().strip()
            if not code:
                self._show_dropbox_code_dialog()
                return

            def _finish() -> None:
                try:
                    self._app.dropbox_auth.finish_external_auth(code)
                    email = self._app.init_provider("dropbox")
                    self._app.add_provider_account("dropbox", email)
                    GLib.idle_add(self._on_saved_ok)
                except Exception as exc:
                    GLib.idle_add(self._show_error, str(exc))

            threading.Thread(target=_finish, daemon=True).start()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _show_error(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self, heading="Error", body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()
        self._reauth_btn.set_sensitive(True)
        from ..ui.window import _PROVIDER_LABELS
        self._reauth_btn.set_label(
            f"Re-authenticate with {_PROVIDER_LABELS.get(self._provider, self._provider)}"
        )
