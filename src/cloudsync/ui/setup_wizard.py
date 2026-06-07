"""First-run setup wizard using Adw.NavigationView.

Shown automatically when no credentials exist.  Guides the user through:

  Page 1 — Welcome
  Page 2 — Choose provider (Google Drive / Amazon S3)
  Page 3a — Sign in with Google      (gdrive path)
  Page 3b — Enter AWS credentials    (s3 path)
  Page 4 — Add first sync folder
  Page 5 — All done
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, TYPE_CHECKING

log = logging.getLogger(__name__)

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..core.config import resolve_portal_path

if TYPE_CHECKING:
    from ..app import CloudSyncApp


class SetupWizard(Adw.Window):
    """Modal first-run wizard.  Calls *on_complete* when finished."""

    def __init__(self, app: "CloudSyncApp", on_complete: Callable[[], None], parent: Gtk.Window):
        super().__init__(
            title="Set Up CloudSync",
            modal=True,
            transient_for=parent,
            default_width=480,
            default_height=560,
            resizable=False,
        )
        self._app = app
        self._on_complete = on_complete
        self._chosen_provider: str = "gdrive"  # set when user picks a provider

        self._nav = Adw.NavigationView()
        self.set_content(self._nav)

        self._nav.push(self._build_welcome_page())

    # ------------------------------------------------------------------ #
    # Page 1 — Welcome                                                     #
    # ------------------------------------------------------------------ #

    def _build_welcome_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Welcome", tag="welcome")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar(show_back_button=False))

        status = Adw.StatusPage(
            icon_name="folder-sync-symbolic",
            title="Welcome to CloudSync",
            description=(
                "Sync your files between your computer and the cloud.\n\n"
                "This wizard will guide you through the setup in a few steps."
            ),
        )

        btn = Gtk.Button(label="Get Started", css_classes=["pill", "suggested-action"],
                         halign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda _: self._nav.push(self._build_provider_page()))
        status.set_child(btn)

        toolbar.set_content(status)
        return page

    # ------------------------------------------------------------------ #
    # Page 2 — Choose provider                                            #
    # ------------------------------------------------------------------ #

    def _build_provider_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Choose Provider", tag="provider")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        status = Adw.StatusPage(
            icon_name="network-server-symbolic",
            title="Choose a Storage Provider",
            description="Select where you want to store and sync your files.",
        )
        toolbar.set_content(status)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      halign=Gtk.Align.CENTER)
        status.set_child(box)

        gdrive_btn = Gtk.Button(css_classes=["pill", "suggested-action"])
        gdrive_btn.set_child(_provider_button_content(
            "Google Drive", "folder-remote-symbolic"
        ))
        gdrive_btn.connect("clicked", self._on_gdrive_chosen)
        box.append(gdrive_btn)

        dropbox_btn = Gtk.Button(css_classes=["pill"])
        dropbox_btn.set_child(_provider_button_content(
            "Dropbox", "folder-remote-symbolic"
        ))
        dropbox_btn.connect("clicked", self._on_dropbox_chosen)
        box.append(dropbox_btn)

        s3_btn = Gtk.Button(css_classes=["pill"])
        s3_btn.set_child(_provider_button_content(
            "Amazon S3", "network-server-symbolic"
        ))
        s3_btn.connect("clicked", self._on_s3_chosen)
        box.append(s3_btn)

        b2_btn = Gtk.Button(css_classes=["pill"])
        b2_btn.set_child(_provider_button_content(
            "Backblaze B2", "network-server-symbolic"
        ))
        b2_btn.connect("clicked", self._on_b2_chosen)
        box.append(b2_btn)

        r2_btn = Gtk.Button(css_classes=["pill"])
        r2_btn.set_child(_provider_button_content(
            "Cloudflare R2", "network-server-symbolic"
        ))
        r2_btn.connect("clicked", self._on_r2_chosen)
        box.append(r2_btn)

        return page

    def _on_gdrive_chosen(self, _btn) -> None:
        self._chosen_provider = "gdrive"
        self._nav.push(self._build_signin_page())

    def _on_dropbox_chosen(self, _btn) -> None:
        self._chosen_provider = "dropbox"
        self._nav.push(self._build_dropbox_page())

    def _on_s3_chosen(self, _btn) -> None:
        self._chosen_provider = "s3"
        self._nav.push(self._build_s3_page())

    def _on_b2_chosen(self, _btn) -> None:
        self._chosen_provider = "s3"
        self._nav.push(self._build_s3_page(preset="backblaze"))

    def _on_r2_chosen(self, _btn) -> None:
        self._chosen_provider = "s3"
        self._nav.push(self._build_s3_page(preset="cloudflare"))


    # ------------------------------------------------------------------ #
    # Page 3a — Sign in with Google                                        #
    # ------------------------------------------------------------------ #

    def _build_signin_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Sign In", tag="signin")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        self._signin_status = Adw.StatusPage(
            icon_name="avatar-default-symbolic",
            title="Sign in with Google",
            description="Click the button below to authorise access to your Google Drive.",
        )
        toolbar.set_content(self._signin_status)

        self._signin_btn = Gtk.Button(
            label="Sign in with Google",
            css_classes=["pill", "suggested-action"],
            halign=Gtk.Align.CENTER,
        )
        self._signin_btn.connect("clicked", self._on_signin_clicked)
        self._signin_status.set_child(self._signin_btn)

        return page

    def _on_signin_clicked(self, _btn) -> None:
        from .auth_dialog import has_webkit, AuthDialog

        self._signin_btn.set_sensitive(False)
        self._signin_btn.set_label("Opening sign-in…")

        try:
            auth_url = self._app.auth.build_auth_url()
        except Exception as exc:
            self._signin_btn.set_sensitive(True)
            self._signin_btn.set_label("Sign in with Google")
            self._show_error(str(exc))
            return

        if has_webkit():
            def _on_code(code: str):
                def _exchange():
                    try:
                        self._app.auth.exchange_code(code)
                        email = self._app.init_provider("gdrive")
                        self._app.add_provider_account("gdrive", email)
                        GLib.idle_add(self._signin_success, email)
                    except Exception as exc:
                        GLib.idle_add(self._signin_failed, str(exc))
                threading.Thread(target=_exchange, daemon=True).start()

            AuthDialog(auth_url=auth_url, on_code=_on_code,
                       on_cancel=lambda: GLib.idle_add(self._signin_cancelled),
                       parent=self, title="Sign in with Google").present()
        else:
            def _ext():
                try:
                    self._app.auth.authenticate_external()
                    email = self._app.init_provider("gdrive")
                    self._app.add_provider_account("gdrive", email)
                    GLib.idle_add(self._signin_success, email)
                except Exception as exc:
                    GLib.idle_add(self._signin_failed, str(exc))
            threading.Thread(target=_ext, daemon=True).start()

    def _signin_success(self, email: str) -> None:
        self._signin_status.set_icon_name("emblem-ok-symbolic")
        self._signin_status.set_title(f"Signed in as {email or 'Google Account'}")
        self._signin_status.set_description("Your Google Drive is connected.")
        next_btn = Gtk.Button(label="Continue", css_classes=["pill", "suggested-action"],
                              halign=Gtk.Align.CENTER)
        next_btn.connect("clicked", lambda _: self._nav.push(self._build_folder_page()))
        self._signin_status.set_child(next_btn)

    def _signin_failed(self, msg: str) -> None:
        self._signin_btn.set_sensitive(True)
        self._signin_btn.set_label("Try Again")
        self._show_error(f"Sign-in failed:\n{msg}")

    def _signin_cancelled(self) -> None:
        self._signin_btn.set_sensitive(True)
        self._signin_btn.set_label("Sign in with Google")

    # ------------------------------------------------------------------ #
    # Page 3b — Dropbox sign-in                                            #
    # ------------------------------------------------------------------ #

    def _build_dropbox_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="Dropbox", tag="dropbox")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=16,
            margin_top=16, margin_bottom=16,
            margin_start=16, margin_end=16,
        )
        toolbar.set_content(box)

        intro = Adw.StatusPage(
            icon_name="folder-remote-symbolic",
            title="Sign in with Dropbox",
            description="Click below to connect your Dropbox account.",
        )
        box.append(intro)

        self._dbx_btn = Gtk.Button(
            label="Sign in with Dropbox",
            css_classes=["pill", "suggested-action"],
            halign=Gtk.Align.CENTER,
            margin_top=8,
        )
        self._dbx_btn.connect("clicked", self._on_dropbox_signin_clicked)
        box.append(self._dbx_btn)

        return page

    def _on_dropbox_signin_clicked(self, _btn) -> None:
        from .auth_dialog import has_webkit, AuthDialog

        self._dbx_btn.set_sensitive(False)
        self._dbx_btn.set_label("Opening sign-in…")

        try:
            auth_url = self._app.dropbox_auth.build_auth_url()
        except Exception as exc:
            self._dbx_btn.set_sensitive(True)
            self._dbx_btn.set_label("Sign in with Dropbox")
            self._show_error(str(exc))
            return

        if has_webkit():
            def _on_redirect(url: str):
                def _exchange():
                    try:
                        self._app.dropbox_auth.exchange_code(url)
                        email = self._app.init_provider("dropbox")
                        self._app.add_provider_account("dropbox", email)
                        GLib.idle_add(self._dbx_signin_success, email)
                    except Exception as exc:
                        GLib.idle_add(self._dbx_signin_failed, str(exc))
                threading.Thread(target=_exchange, daemon=True).start()

            AuthDialog(
                auth_url=auth_url,
                on_code=_on_redirect,
                on_cancel=lambda: GLib.idle_add(
                    self._dbx_btn.set_sensitive, True
                ),
                parent=self,
                title="Sign in with Dropbox",
                capture_full_url=True,
            ).present()
        else:
            try:
                auth_url = self._app.dropbox_auth.start_external_auth()
            except Exception as exc:
                self._dbx_btn.set_sensitive(True)
                self._dbx_btn.set_label("Sign in with Dropbox")
                self._show_error(str(exc))
                return
            import webbrowser
            webbrowser.open(auth_url)
            self._show_dropbox_code_dialog()

    def _show_dropbox_code_dialog(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Enter Dropbox code",
            body="Dropbox opened in your browser. After authorizing, copy the code shown and paste it below.",
        )
        entry = Gtk.Entry(placeholder_text="Paste code here", activates_default=True)
        entry.set_margin_top(8)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "Connect")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def _on_response(d, response):
            if response != "ok":
                self._dbx_btn.set_sensitive(True)
                self._dbx_btn.set_label("Sign in with Dropbox")
                return
            code = entry.get_text().strip()
            if not code:
                self._show_dropbox_code_dialog()
                return

            def _finish():
                try:
                    self._app.dropbox_auth.finish_external_auth(code)
                    email = self._app.init_provider("dropbox")
                    self._app.add_provider_account("dropbox", email)
                    GLib.idle_add(self._dbx_signin_success, email)
                except Exception as exc:
                    GLib.idle_add(self._dbx_signin_failed, str(exc))
            threading.Thread(target=_finish, daemon=True).start()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _dbx_signin_success(self, email: str) -> None:
        from .auth_dialog import has_webkit  # noqa
        # Reuse the same success pattern as Google
        self._nav.push(self._build_folder_page())

    def _dbx_signin_failed(self, msg: str) -> None:
        self._dbx_btn.set_sensitive(True)
        self._dbx_btn.set_label("Sign in with Dropbox")
        self._show_error(f"Sign-in failed:\n{msg}")

    # ------------------------------------------------------------------ #
    # Page 3c — AWS S3 / B2 / R2 credentials                              #
    # ------------------------------------------------------------------ #

    def _build_s3_page(self, preset: str = "aws") -> Adw.NavigationPage:
        from ..core.s3_auth import S3_PRESETS
        p = S3_PRESETS.get(preset, S3_PRESETS["aws"])

        page = Adw.NavigationPage(title=p["label"], tag="s3")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        outer = Adw.StatusPage(
            icon_name="network-server-symbolic",
            title=f"Connect to {p['label']}",
            description=(
                "Enter your credentials. The key needs read, write, "
                f"delete, and list permissions.\n\n{p['notes']}"
                if p.get("notes") else
                "Enter your credentials."
            ),
        )
        toolbar.set_content(outer)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_child(content)

        help_group = Adw.PreferencesGroup(
            title="Keep this connection working",
            description=(
                "Simple setup steps for most users."
            ),
        )
        content.append(help_group)

        help_text = Gtk.Label(
            xalign=0,
            wrap=True,
            selectable=True,
            label=(
                "1) In AWS, create an Access Key for this app.\n"
                "2) Paste Access Key ID and Secret Access Key here.\n"
                "3) Keep Region as-is unless your admin gave you a different one.\n"
                "4) Leave Endpoint URL blank for normal AWS S3.\n\n"
                "You will choose which bucket and folder to sync on the next step.\n\n"
                "If Connect fails, ask your AWS admin to allow this key to "
                "list, read, upload, and delete files in the target bucket."
            ),
        )
        help_text.set_margin_top(6)
        help_text.set_margin_bottom(6)
        help_group.add(help_text)

        prefs = Adw.PreferencesGroup()
        content.append(prefs)

        self._s3_access_row = Adw.EntryRow(title="Access Key ID")
        self._s3_secret_row = Adw.PasswordEntryRow(title="Secret Access Key")
        self._s3_region_row = Adw.EntryRow(title="Region")
        self._s3_region_row.set_text(p.get("region", "us-east-1"))
        self._s3_endpoint_row = Adw.EntryRow(title="Endpoint URL")
        self._s3_endpoint_row.set_text(p.get("endpoint_url", ""))
        self._s3_endpoint_row.set_tooltip_text(
            "Leave blank for AWS. Pre-filled for B2/R2 — adjust if needed."
        )

        for row in (
            self._s3_access_row,
            self._s3_secret_row,
            self._s3_region_row,
            self._s3_endpoint_row,
        ):
            prefs.add(row)

        self._s3_connect_btn = Gtk.Button(
            label="Connect",
            css_classes=["pill", "suggested-action"],
            halign=Gtk.Align.CENTER,
            margin_top=16,
        )
        self._s3_connect_btn.connect("clicked", self._on_s3_connect)
        prefs.add(self._s3_connect_btn)

        return page

    def _on_s3_connect(self, _btn) -> None:
        access_key = self._s3_access_row.get_text().strip()
        secret_key = self._s3_secret_row.get_text().strip()
        region = self._s3_region_row.get_text().strip() or "us-east-1"
        endpoint = self._s3_endpoint_row.get_text().strip() or None

        if not access_key or not secret_key:
            self._show_error("Access Key ID and Secret Access Key are required.")
            return

        self._s3_connect_btn.set_sensitive(False)
        self._s3_connect_btn.set_label("Connecting…")

        def _validate():
            try:
                self._app.s3_auth.validate(
                    access_key,
                    secret_key,
                    region,
                    endpoint,
                )
                self._app.s3_auth.save(access_key, secret_key, region, endpoint)
                GLib.idle_add(self._s3_connect_success)
            except Exception as exc:
                GLib.idle_add(self._s3_connect_failed, str(exc))

        threading.Thread(target=_validate, daemon=True).start()

    def _s3_connect_success(self) -> None:
        self._s3_connect_btn.set_label("Starting engine…")

        def _init():
            try:
                email = self._app.init_provider("s3")
                self._app.add_provider_account("s3", email)
            except Exception as exc:
                log.error("S3 engine init error: %s", exc)
            GLib.idle_add(self._nav.push, self._build_folder_page())

        threading.Thread(target=_init, daemon=True).start()

    def _s3_connect_failed(self, msg: str) -> None:
        self._s3_connect_btn.set_sensitive(True)
        self._s3_connect_btn.set_label("Connect")
        self._show_error(f"Connection failed:\n{msg}")

    # ------------------------------------------------------------------ #
    # Page 4 — Add first folder                                            #
    # ------------------------------------------------------------------ #

    def _build_folder_page(self, default_remote_id: str = "") -> Adw.NavigationPage:
        if default_remote_id:
            self._default_remote_id = default_remote_id
        elif self._chosen_provider == "s3":
            self._default_remote_id = ""
        else:
            self._default_remote_id = "root"

        page = Adw.NavigationPage(title="Sync Folder", tag="folder")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())

        self._folder_status = Adw.StatusPage(
            icon_name="folder-symbolic",
            title="Choose a Folder to Sync",
            description="Select a local folder to keep in sync with the cloud.",
        )
        toolbar.set_content(self._folder_status)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          halign=Gtk.Align.CENTER)
        self._folder_status.set_child(btn_box)

        choose_btn = Gtk.Button(label="Choose Local Folder…", css_classes=["pill", "suggested-action"])
        choose_btn.connect("clicked", self._on_choose_folder)
        btn_box.append(choose_btn)

        self._skip_btn = Gtk.Button(label="Skip for now", css_classes=["pill", "flat"])
        self._skip_btn.connect("clicked", lambda _: self._nav.push(self._build_done_page()))
        btn_box.append(self._skip_btn)

        return page

    def _on_choose_folder(self, _btn) -> None:
        dialog = Gtk.FileDialog(title="Select folder to sync")
        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if not folder:
                return
            local_path = resolve_portal_path(folder.get_path())

            if self._chosen_provider == "s3":
                self._pending_local_path = local_path
                from .cloud_folder_picker import CloudFolderPickerDialog
                CloudFolderPickerDialog(
                    app=self._app,
                    provider="s3",
                    on_selected=self._on_cloud_folder_picked,
                    parent=self,
                ).present()

            elif self._chosen_provider in ("gdrive", "dropbox"):
                # Ask which cloud subfolder to sync with before finalising
                self._pending_local_path = local_path
                if self._chosen_provider not in self._app._providers:
                    # Engine not ready yet — fall back to root
                    self._finish_add_folder(local_path, self._default_remote_id,
                                            "My Drive" if self._chosen_provider == "gdrive" else "Dropbox")
                    return
                from .cloud_folder_picker import CloudFolderPickerDialog
                CloudFolderPickerDialog(
                    app=self._app,
                    provider=self._chosen_provider,
                    on_selected=self._on_cloud_folder_picked,
                    parent=self,
                ).present()

            else:
                self._finish_add_folder(local_path, self._default_remote_id, "Cloud Root")

        except GLib.Error:
            pass

    def _on_cloud_folder_picked(self, folder_id: str, folder_name: str) -> None:
        self._finish_add_folder(self._pending_local_path, folder_id, folder_name)

    def _finish_add_folder(self, local_path: str, remote_id: str, remote_name: str) -> None:
        from ..core.config import SyncFolder
        existing = [f.local_path for f in self._app.config.sync_folders]
        if local_path not in existing:
            self._app.config.sync_folders.append(SyncFolder(
                local_path=local_path,
                remote_folder_id=remote_id,
                remote_folder_name=remote_name,
                provider=self._chosen_provider,
            ))
            self._app.save_config()
        self._nav.push(self._build_done_page())

    # ------------------------------------------------------------------ #
    # Page 5 — Done                                                        #
    # ------------------------------------------------------------------ #

    def _build_done_page(self) -> Adw.NavigationPage:
        page = Adw.NavigationPage(title="All Set", tag="done")
        toolbar = Adw.ToolbarView()
        page.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar(show_back_button=False))

        status = Adw.StatusPage(
            icon_name="emblem-ok-symbolic",
            title="You're all set!",
            description=(
                "CloudSync is running.\n"
                "Your files will sync automatically in the background."
            ),
        )
        toolbar.set_content(status)

        done_btn = Gtk.Button(label="Start Syncing", css_classes=["pill", "suggested-action"],
                              halign=Gtk.Align.CENTER)
        done_btn.connect("clicked", self._on_done)
        status.set_child(done_btn)

        return page

    def _on_done(self, _btn) -> None:
        self.close()
        self._on_complete()

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _show_error(self, message: str) -> None:
        dialog = Adw.MessageDialog(transient_for=self, heading="Error", body=message)
        dialog.add_response("ok", "OK")
        dialog.present()


def _provider_button_content(label: str, icon_name: str) -> Gtk.Box:
    """Build a labelled icon box for a provider choice button."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10,
                  halign=Gtk.Align.CENTER, margin_top=4, margin_bottom=4,
                  margin_start=8, margin_end=8)
    icon = Gtk.Image(icon_name=icon_name, pixel_size=20)
    lbl = Gtk.Label(label=label)
    box.append(icon)
    box.append(lbl)
    return box
