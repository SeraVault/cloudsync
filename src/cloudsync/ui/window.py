"""Main application window."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..core.activity_log import ActivityEntry  # noqa: E402
from ..core.config import SyncFolder, resolve_portal_path  # noqa: E402
from ..core import license as lic  # noqa: E402
from .preferences import PreferencesWindow  # noqa: E402
from .help_window import HelpWindow  # noqa: E402

_PROVIDER_LABELS = {
    "gdrive": "Google Drive",
    "s3": "Amazon S3",
    "backblaze": "Backblaze B2",
    "cloudflare": "Cloudflare R2",
    "dropbox": "Dropbox",
    "onedrive": "Microsoft OneDrive",
}

if TYPE_CHECKING:
    from ..app import CloudSyncApp


class ProviderSyncRow(Adw.ActionRow):
    """Account row with edit/disconnect controls."""

    def __init__(
        self,
        provider: str,
        display_name: str,
        on_edit: callable,
        on_disconnect: callable,
    ):
        label = _PROVIDER_LABELS.get(provider, provider)
        super().__init__(title=label, subtitle=display_name or "Connected")

        edit_btn = Gtk.Button(
            icon_name="document-edit-symbolic",
            tooltip_text="Edit credentials",
            valign=Gtk.Align.CENTER,
        )
        edit_btn.add_css_class("flat")
        edit_btn.connect("clicked", lambda _: on_edit(provider))
        self.add_suffix(edit_btn)

        disconnect_btn = Gtk.Button(
            icon_name="list-remove-symbolic",
            tooltip_text="Disconnect",
            valign=Gtk.Align.CENTER,
        )
        disconnect_btn.add_css_class("flat")
        disconnect_btn.connect("clicked", lambda _: on_disconnect(provider))
        self.add_suffix(disconnect_btn)


class FolderRow(Adw.ExpanderRow):
    """Sync-folder row that expands to show per-folder progress."""

    def __init__(
        self,
        folder: SyncFolder,
        on_remove: callable,
        on_edit: callable,
        on_sync: callable,
    ):
        local = Path(folder.local_path).expanduser()
        provider_label = _PROVIDER_LABELS.get(
            folder.provider, folder.provider
        )
        super().__init__(
            title=local.name,
            subtitle=(
                f"{folder.local_path}  →  {folder.remote_folder_name}"
                f"  [{provider_label}]"
            ),
            show_enable_switch=False,
        )
        self._folder = folder

        self._status_label = Gtk.Label(
            label="Idle",
            halign=Gtk.Align.END,
            hexpand=False,
            width_chars=18,
            max_width_chars=18,
            css_classes=["dim-label", "caption"],
            ellipsize=3,  # Pango.EllipsizeMode.END
            xalign=1.0,
        )
        self.add_suffix(self._status_label)

        toggle = Gtk.Switch(valign=Gtk.Align.CENTER)
        toggle.set_active(folder.enabled)
        toggle.connect("notify::active", self._on_toggle)
        self.add_suffix(toggle)

        sync_btn = Gtk.Button(
            icon_name="view-refresh-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Sync this folder now",
        )
        sync_btn.add_css_class("flat")
        sync_btn.connect("clicked", lambda _: on_sync(folder))
        self.add_suffix(sync_btn)

        edit_btn = Gtk.Button(
            icon_name="document-edit-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Edit folder",
        )
        edit_btn.add_css_class("flat")
        edit_btn.connect("clicked", lambda _: on_edit(folder))
        self.add_suffix(edit_btn)

        remove_btn = Gtk.Button(
            icon_name="list-remove-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Remove folder",
        )
        remove_btn.add_css_class("flat")
        remove_btn.connect("clicked", lambda _: on_remove(folder))
        self.add_suffix(remove_btn)

        progress_row = Adw.ActionRow(activatable=False)
        inner = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        self._progress_bar = Gtk.ProgressBar(show_text=True, hexpand=True)
        self._progress_bar.set_fraction(0.0)
        self._progress_bar.set_text("Idle")
        self._detail_label = Gtk.Label(
            label="",
            halign=Gtk.Align.START,
            ellipsize=3,  # Pango.EllipsizeMode.END
            css_classes=["dim-label", "caption"],
            visible=False,
        )
        inner.append(self._progress_bar)
        inner.append(self._detail_label)
        progress_row.add_suffix(inner)
        self.add_row(progress_row)

    # ------------------------------------------------------------------ #
    # Public update API (safe to call via GLib.idle_add)                  #
    # ------------------------------------------------------------------ #

    def set_sync_status(self, message: str) -> None:
        self._status_label.set_label(message)

    def set_sync_detail(self, message: str) -> None:
        if message:
            self._detail_label.set_label(message)
            self._detail_label.set_visible(True)
        else:
            self._detail_label.set_label("")
            self._detail_label.set_visible(False)

    def set_sync_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self._progress_bar.set_fraction(0.0)
            self._progress_bar.set_text("Idle")
        else:
            self._progress_bar.set_fraction(done / total)
            self._progress_bar.set_text(f"{done} / {total}")

    def _on_toggle(self, switch, _param) -> None:
        self._folder.enabled = switch.get_active()


class ActivityLogRow(Adw.ActionRow):
    def __init__(self, entry: ActivityEntry):
        super().__init__(activatable=False)
        self.set_title(entry.message)
        self.set_subtitle(_format_activity_subtitle(entry))
        badge = Gtk.Label(
            label=entry.level.upper(),
            valign=Gtk.Align.CENTER,
            css_classes=["caption", "dim-label"],
        )
        self.add_suffix(badge)


def _format_activity_subtitle(entry: ActivityEntry) -> str:
    try:
        stamp = datetime.fromisoformat(entry.timestamp)
        ts_text = stamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        ts_text = entry.timestamp
    if entry.provider:
        provider = _PROVIDER_LABELS.get(entry.provider, entry.provider)
        return f"{ts_text}  [{provider}]"
    return ts_text


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: "CloudSyncApp", **kwargs):
        super().__init__(application=app, title="CloudSync", **kwargs)
        self.set_default_size(920, 760)
        self._app = app
        self._folder_rows_by_path: Dict[str, FolderRow] = {}
        self._build_ui()
        self._refresh_accounts()
        self._refresh_folder_list()
        self.refresh_activity_log()

    # ------------------------------------------------------------------ #
    # UI construction                                                       #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        icon_path = (
            Path(__file__).parents[3]
            / "data/icons/com.seravault.cloudsync.svg"
        )
        app_icon = Gtk.Image(valign=Gtk.Align.CENTER)
        if icon_path.exists():
            app_icon.set_from_file(str(icon_path))
            app_icon.set_pixel_size(32)
        else:
            app_icon.set_from_icon_name("com.seravault.cloudsync")
            app_icon.set_pixel_size(32)
        header.pack_start(app_icon)

        title_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            spacing=0,
        )
        title_label = Gtk.Label(label="CloudSync", css_classes=["title"])
        subtitle_label = Gtk.Label(
            label="Two-Way Cloud Sync for the Linux Desktop",
            css_classes=["subtitle"],
        )
        title_box.append(title_label)
        title_box.append(subtitle_label)
        header.set_title_widget(title_box)

        prefs_btn = Gtk.Button(
            icon_name="preferences-system-symbolic",
            tooltip_text="Preferences",
        )
        prefs_btn.connect("clicked", self._on_prefs_clicked)
        header.pack_end(prefs_btn)

        help_btn = Gtk.Button(
            icon_name="help-about-symbolic",
            tooltip_text="Help",
        )
        help_btn.connect("clicked", self._on_help_clicked)
        header.pack_end(help_btn)

        sync_btn = Gtk.Button(
            icon_name="emblem-synchronizing-symbolic",
            tooltip_text="Sync now",
        )
        sync_btn.connect("clicked", self._on_sync_clicked)
        header.pack_end(sync_btn)

        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        toolbar_view.set_content(scroll)

        main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
            spacing=18,
        )
        scroll.set_child(main_box)

        # --- Accounts section ---
        accounts_header = Gtk.Box(spacing=6, margin_top=4)
        accounts_label = Gtk.Label(
            label="Cloud Accounts",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
        )
        add_account_btn = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Add account",
        )
        add_account_btn.add_css_class("flat")
        add_account_btn.connect("clicked", self._on_add_account_clicked)
        accounts_header.append(accounts_label)
        accounts_header.append(add_account_btn)
        main_box.append(accounts_header)

        self._accounts_group = Adw.PreferencesGroup()
        self._account_rows: list = []
        main_box.append(self._accounts_group)

        self._no_accounts_label = Gtk.Label(
            label="No accounts connected.\nClick + to add a provider.",
            css_classes=["dim-label"],
            justify=Gtk.Justification.CENTER,
        )
        main_box.append(self._no_accounts_label)

        # --- Sync folders section ---
        folders_header = Gtk.Box(spacing=6, margin_top=4)
        folders_label = Gtk.Label(
            label="Sync Folders",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
        )
        add_btn = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Add folder",
        )
        add_btn.add_css_class("flat")
        add_btn.connect("clicked", self._on_add_folder_clicked)
        folders_header.append(folders_label)
        folders_header.append(add_btn)
        main_box.append(folders_header)

        self._folders_group = Adw.PreferencesGroup()
        self._folder_rows: list = []
        main_box.append(self._folders_group)

        self._empty_label = Gtk.Label(
            label="No sync folders configured.\nClick + to add a folder.",
            css_classes=["dim-label"],
            justify=Gtk.Justification.CENTER,
        )
        main_box.append(self._empty_label)

        # --- Activity log section ---
        logs_header = Gtk.Box(spacing=6, margin_top=4)
        logs_label = Gtk.Label(
            label="Recent Errors",
            halign=Gtk.Align.START,
            hexpand=True,
            css_classes=["heading"],
        )
        copy_logs_btn = Gtk.Button(
            icon_name="edit-copy-symbolic",
            tooltip_text="Copy activity log",
        )
        copy_logs_btn.add_css_class("flat")
        copy_logs_btn.connect("clicked", self._on_copy_logs_clicked)
        clear_logs_btn = Gtk.Button(
            icon_name="edit-clear-symbolic",
            tooltip_text="Clear activity log",
        )
        clear_logs_btn.add_css_class("flat")
        clear_logs_btn.connect("clicked", self._on_clear_logs_clicked)
        logs_header.append(logs_label)
        logs_header.append(copy_logs_btn)
        logs_header.append(clear_logs_btn)
        main_box.append(logs_header)

        self._logs_group = Adw.PreferencesGroup()
        self._log_rows: list = []
        main_box.append(self._logs_group)

        self._empty_logs_label = Gtk.Label(
            label="No recent failures or errors.",
            css_classes=["dim-label"],
            justify=Gtk.Justification.CENTER,
        )
        main_box.append(self._empty_logs_label)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(toolbar_view)
        self.set_content(self._toast_overlay)

    # ------------------------------------------------------------------ #
    # Public updaters                                                      #
    # ------------------------------------------------------------------ #

    def set_provider_status(self, provider: str, message: str) -> None:
        for row in self._folder_rows_by_path.values():
            if row._folder.provider == provider:
                row.set_sync_status(message)

    def set_provider_detail(self, provider: str, message: str) -> None:
        for row in self._folder_rows_by_path.values():
            if row._folder.provider == provider:
                row.set_sync_detail(message)

    def set_provider_progress(
        self, provider: str, done: int, total: int
    ) -> None:
        for row in self._folder_rows_by_path.values():
            if row._folder.provider == provider:
                row.set_sync_progress(done, total)

    def set_folder_status(self, local_path: str, message: str) -> None:
        row = self._folder_rows_by_path.get(local_path)
        if row:
            row.set_sync_status(message)

    def set_folder_detail(self, local_path: str, message: str) -> None:
        row = self._folder_rows_by_path.get(local_path)
        if row:
            row.set_sync_detail(message)

    def set_folder_progress(
        self, local_path: str, done: int, total: int
    ) -> None:
        row = self._folder_rows_by_path.get(local_path)
        if row:
            row.set_sync_progress(done, total)

    def set_status(self, message: str) -> None:
        for row in self._folder_rows_by_path.values():
            row.set_sync_status(message)

    def set_account(self, email: str) -> None:  # noqa: ARG002
        self._refresh_accounts()

    def show_toast(self, message: str) -> None:
        self._toast_overlay.add_toast(Adw.Toast(title=message, timeout=3))

    def refresh(self) -> None:
        self._refresh_accounts()
        self._refresh_folder_list()
        self.refresh_activity_log()

    def refresh_activity_log(self) -> None:
        for row in self._log_rows:
            self._logs_group.remove(row)
        self._log_rows.clear()
        entries = [
            e for e in self._app.activity_log.recent()
            if e.level.lower() in {"error", "warning", "warn", "failure"}
        ]
        self._empty_logs_label.set_visible(not entries)
        for entry in entries:
            row = ActivityLogRow(entry)
            self._logs_group.add(row)
            self._log_rows.append(row)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _refresh_accounts(self) -> None:
        for row in self._account_rows:
            self._accounts_group.remove(row)
        self._account_rows.clear()
        accounts = self._app.config.connected_providers
        self._no_accounts_label.set_visible(not accounts)
        for acct in accounts:
            row = ProviderSyncRow(
                provider=acct.provider,
                display_name=acct.display_name or "Connected",
                on_edit=self._on_edit_account,
                on_disconnect=self._on_disconnect_provider,
            )
            self._accounts_group.add(row)
            self._account_rows.append(row)

    def _refresh_folder_list(self) -> None:
        for row in self._folder_rows:
            self._folders_group.remove(row)
        self._folder_rows.clear()
        self._folder_rows_by_path.clear()
        folders = self._app.config.sync_folders
        self._empty_label.set_visible(not folders)
        for folder in folders:
            row = FolderRow(
                folder,
                on_remove=self._on_remove_folder,
                on_edit=self._on_edit_folder,
                on_sync=self._on_sync_folder,
            )
            self._folders_group.add(row)
            self._folder_rows.append(row)
            self._folder_rows_by_path[folder.local_path] = row

    # ------------------------------------------------------------------ #
    # Signal handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_add_account_clicked(self, _btn) -> None:
        info = lic.load()
        limit = info.provider_limit()
        n = len(self._app.config.connected_providers)
        if limit is not None and n >= limit:
            self._app._show_upgrade_dialog()
            return
        self._app._show_add_provider_wizard()

    def _on_disconnect_provider(self, provider: str) -> None:
        label = _PROVIDER_LABELS.get(provider, provider)
        dialog = Adw.AlertDialog(
            heading="Disconnect account?",
            body=(
                f"This will remove the {label} account and all its "
                "sync folders."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disconnect", "Disconnect")
        dialog.set_response_appearance(
            "disconnect", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: self._on_disconnect_confirmed(r, provider),
        )
        dialog.present(self)

    def _on_disconnect_confirmed(self, response: str, provider: str) -> None:
        if response == "disconnect":
            self._app.remove_provider_account(provider)

    def _on_sync_clicked(self, _btn) -> None:
        self._app.trigger_sync()

    def _on_prefs_clicked(self, _btn) -> None:
        self.show_preferences()

    def show_preferences(self, page: str = "") -> None:
        win = PreferencesWindow(
            config=self._app.config,
            on_save=self._app.save_config,
            transient_for=self,
            modal=True,
        )
        if page:
            win.set_visible_page_name(page)
        win.present()

    def _on_help_clicked(self, _btn) -> None:
        HelpWindow(parent=self).present()

    def _on_add_folder_clicked(self, _btn) -> None:
        accounts = self._app.config.connected_providers
        if not accounts:
            self.show_toast("Connect a provider account first.")
            return
        info = lic.load()
        limit = info.folder_limit()
        if limit is not None and len(self._app.config.sync_folders) >= limit:
            self._app._show_upgrade_dialog()
            return
        if len(accounts) == 1:
            self._pick_local_folder(accounts[0].provider)
        else:
            self._show_provider_picker()

    def _show_provider_picker(self) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Choose Provider",
            body="Which account should this folder sync with?",
        )
        accounts = self._app.config.connected_providers
        for acct in accounts:
            label = _PROVIDER_LABELS.get(acct.provider, acct.provider)
            subtitle = acct.display_name or ""
            rid = acct.provider
            dialog.add_response(
                rid, f"{label} — {subtitle}" if subtitle else label
            )
        dialog.add_response("cancel", "Cancel")
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: r != "cancel" and self._pick_local_folder(r),
        )
        dialog.present()

    def _pick_local_folder(self, provider: str) -> None:
        file_dialog = Gtk.FileDialog(title="Select folder to sync")
        file_dialog.select_folder(
            self, None,
            lambda d, r: self._on_folder_selected(d, r, provider),
        )

    def _on_folder_selected(self, dialog, result, provider: str) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if not folder:
                return
            path = resolve_portal_path(folder.get_path())
            self._pending_local_path = path
            self._pending_provider = provider
            if provider in ("gdrive", "dropbox"):
                if self._app.get_client(provider) is not None:
                    from .cloud_folder_picker import CloudFolderPickerDialog
                    CloudFolderPickerDialog(
                        app=self._app,
                        provider=provider,
                        on_selected=self._on_cloud_folder_picked,
                        parent=self,
                    ).present()
                else:
                    self._app.add_sync_folder(path, provider=provider)
                    self._refresh_folder_list()
            elif provider == "s3":
                bucket = ""
                for f in self._app.config.sync_folders:
                    if (
                        f.provider == "s3"
                        and f.remote_folder_id not in ("", "root")
                    ):
                        bucket = f.remote_folder_id.split("/")[0]
                        break
                from .cloud_folder_picker import CloudFolderPickerDialog
                CloudFolderPickerDialog(
                    app=self._app,
                    provider="s3",
                    on_selected=self._on_cloud_folder_picked,
                    parent=self,
                    initial_folder_id=bucket or None,
                    initial_folder_name=bucket or None,
                ).present()
            else:
                self._app.add_sync_folder(path, provider=provider)
                self._refresh_folder_list()
        except ValueError as exc:
            self._show_error_dialog(str(exc))
        except GLib.Error:
            pass

    def _on_cloud_folder_picked(
        self, folder_id: str, folder_name: str
    ) -> None:
        try:
            self._app.add_sync_folder(
                self._pending_local_path,
                provider=self._pending_provider,
                remote_folder_id=folder_id,
                remote_folder_name=folder_name,
            )
            self._refresh_folder_list()
        except ValueError as exc:
            self._show_error_dialog(str(exc))

    def _on_edit_account(self, provider: str) -> None:
        from .edit_account_dialog import EditAccountDialog
        EditAccountDialog(
            app=self._app,
            provider=provider,
            on_saved=self._refresh_accounts,
            parent=self,
        ).present()

    def _on_edit_folder(self, folder: SyncFolder) -> None:
        from .edit_folder_dialog import EditFolderDialog
        EditFolderDialog(
            app=self._app,
            folder=folder,
            on_saved=self._refresh_folder_list,
            parent=self,
        ).present()

    def _on_sync_folder(self, folder: SyncFolder) -> None:
        self._app.trigger_folder_sync(folder)

    def _on_remove_folder(self, folder: SyncFolder) -> None:
        name = Path(folder.local_path).name
        dialog = Adw.AlertDialog(
            heading="Remove sync folder?",
            body=(
                f'"{name}" will be removed from CloudSync. '
                "Your local files will not be deleted."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance(
            "remove", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: self._on_remove_folder_confirmed(r, folder),
        )
        dialog.present(self)

    def _on_remove_folder_confirmed(
        self, response: str, folder: SyncFolder
    ) -> None:
        if response == "remove":
            self._app.remove_sync_folder(folder)
            self._refresh_folder_list()

    def _on_clear_logs_clicked(self, _btn) -> None:
        dialog = Adw.AlertDialog(
            heading="Clear activity log?",
            body="All sync history will be permanently deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance(
            "clear", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_clear_logs_confirmed)
        dialog.present(self)

    def _on_clear_logs_confirmed(self, _dialog, response: str) -> None:
        if response == "clear":
            self._app.clear_activity_log()
            self.show_toast("Activity log cleared.")

    def _on_copy_logs_clicked(self, _btn) -> None:
        text = self._app.activity_log.recent_text()
        self.get_clipboard().set(text)
        self.show_toast("Activity log copied.")

    def _show_error_dialog(self, message: str) -> None:
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Error",
            body=message,
        )
        dialog.add_response("copy", "Copy")
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.connect(
            "response",
            lambda _d, r: r == "copy" and self.get_clipboard().set(message),
        )
        dialog.present()
