"""Dialog for editing an existing sync folder mapping."""
from __future__ import annotations

from typing import Callable, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from ..core.config import (
    CONFLICT_LABELS, CONFLICT_OPTIONS,
    INTERVAL_LABELS, INTERVAL_OPTIONS, SyncFolder, resolve_portal_path
)

if TYPE_CHECKING:
    from ..app import CloudSyncApp


class EditFolderDialog(Adw.Window):
    """Modal dialog for editing a sync folder mapping.

    Editable fields:
      - Local folder path (browse button)
      - Remote folder ID / bucket prefix
      - Remote folder display name (cosmetic)
      - Per-folder sync interval (or inherit global default)
    """

    def __init__(
        self,
        app: "CloudSyncApp",
        folder: SyncFolder,
        on_saved: Callable[[], None],
        parent: Gtk.Window,
    ):
        super().__init__(
            title="Edit Sync Folder",
            modal=True,
            transient_for=parent,
            default_width=500,
            resizable=False,
        )
        self._app = app
        self._folder = folder
        self._on_saved = on_saved
        self._pending_local_path: str = folder.local_path

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(self._build_content())

    # ------------------------------------------------------------------ #
    # UI                                                                   #
    # ------------------------------------------------------------------ #

    def _build_content(self) -> Gtk.Widget:
        from .window import _PROVIDER_LABELS
        provider_label = _PROVIDER_LABELS.get(self._folder.provider, self._folder.provider)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=16, margin_bottom=16,
            margin_start=16, margin_end=16,
        )

        # ---- Local path ------------------------------------------------ #
        local_group = Adw.PreferencesGroup(
            title="Local Folder",
            description=f"Provider: {provider_label}",
        )
        box.append(local_group)

        self._local_row = Adw.ActionRow(title="Path")
        self._local_row.set_subtitle(self._folder.local_path)
        browse_btn = Gtk.Button(
            label="Browse…",
            valign=Gtk.Align.CENTER,
            css_classes=["pill"],
        )
        browse_btn.connect("clicked", self._on_browse_clicked)
        self._local_row.add_suffix(browse_btn)
        local_group.add(self._local_row)

        # ---- Remote folder --------------------------------------------- #
        remote_group = Adw.PreferencesGroup(title="Remote Folder")
        box.append(remote_group)

        self._remote_id_row = Adw.EntryRow(title="Folder ID / Bucket Prefix")
        self._remote_id_row.set_text(self._folder.remote_folder_id)
        self._remote_id_row.set_tooltip_text(
            "Google Drive: folder ID from the URL  •  "
            "S3: bucket name or bucket/prefix  •  "
            "OneDrive: item ID (or 'root')"
        )
        # For Google Drive, Dropbox, and S3 add a Browse button to pick a folder visually
        if self._folder.provider in ("gdrive", "dropbox", "s3"):
            browse_btn = Gtk.Button(
                label="Browse\u2026",
                valign=Gtk.Align.CENTER,
                css_classes=["pill"],
                tooltip_text="Browse cloud folders",
            )
            browse_btn.connect("clicked", self._on_browse_cloud_folder)
            self._remote_id_row.add_suffix(browse_btn)
        remote_group.add(self._remote_id_row)

        self._remote_name_row = Adw.EntryRow(title="Display Name")
        self._remote_name_row.set_text(self._folder.remote_folder_name)
        self._remote_name_row.set_tooltip_text(
            "Human-readable label shown in the UI — does not rename the remote folder"
        )
        remote_group.add(self._remote_name_row)

        # ---- Sync timing ---------------------------------------------- #
        timing_group = Adw.PreferencesGroup(
            title="Sync Timing",
            description="How often to check for changes. Folders without an override follow the global default.",
        )
        box.append(timing_group)

        global_interval = self._app.config.sync_interval_seconds
        try:
            _global_interval_label = INTERVAL_LABELS[INTERVAL_OPTIONS.index(global_interval)]
        except ValueError:
            _global_interval_label = f"{global_interval}s"
        all_interval_labels = [f"Default ({_global_interval_label})"] + INTERVAL_LABELS
        self._interval_row = Adw.ComboRow(title="Check every")
        int_model = Gtk.StringList()
        for label in all_interval_labels:
            int_model.append(label)
        self._interval_row.set_model(int_model)

        stored_interval = self._folder.sync_interval_seconds
        if stored_interval == 0 or stored_interval not in INTERVAL_OPTIONS:
            self._interval_row.set_selected(0)
        else:
            self._interval_row.set_selected(INTERVAL_OPTIONS.index(stored_interval) + 1)
        timing_group.add(self._interval_row)

        # ---- Conflict resolution --------------------------------------- #
        conflict_group = Adw.PreferencesGroup(
            title="Conflict Resolution",
            description="What to do when a file changes in both locations since the last sync.",
        )
        box.append(conflict_group)

        global_conflict = self._app.config.conflict_resolution
        try:
            _global_conflict_label = CONFLICT_LABELS[CONFLICT_OPTIONS.index(global_conflict)]
        except ValueError:
            _global_conflict_label = global_conflict
        all_conflict_labels = [f"Default ({_global_conflict_label})"] + CONFLICT_LABELS
        self._conflict_row = Adw.ComboRow(title="Strategy")
        conflict_model = Gtk.StringList()
        for label in all_conflict_labels:
            conflict_model.append(label)
        self._conflict_row.set_model(conflict_model)

        stored_conflict = self._folder.conflict_resolution
        if stored_conflict and stored_conflict in CONFLICT_OPTIONS:
            self._conflict_row.set_selected(CONFLICT_OPTIONS.index(stored_conflict) + 1)
        else:
            self._conflict_row.set_selected(0)
        conflict_group.add(self._conflict_row)

        btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
        )
        box.append(btn_box)

        save_btn = Gtk.Button(label="Save", css_classes=["pill", "suggested-action"])
        save_btn.connect("clicked", self._on_save_clicked)
        btn_box.append(save_btn)

        cancel_btn = Gtk.Button(label="Cancel", css_classes=["pill"])
        cancel_btn.connect("clicked", lambda _: self.close())
        btn_box.append(cancel_btn)

        return box

    # ------------------------------------------------------------------ #
    # Handlers                                                             #
    # ------------------------------------------------------------------ #

    def _on_browse_clicked(self, _btn) -> None:
        dialog = Gtk.FileDialog(title="Select local sync folder")
        dialog.select_folder(self, None, self._on_folder_chosen)

    def _on_folder_chosen(self, dialog, result) -> None:
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = resolve_portal_path(folder.get_path())
                self._local_row.set_subtitle(path)
                self._pending_local_path = path
        except GLib.Error:
            pass

    def _on_browse_cloud_folder(self, _btn) -> None:
        provider = self._folder.provider
        client = self._app.get_client(provider)
        if client is None and provider != "s3":
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Not Connected",
                body="Please reconnect the provider before browsing cloud folders.",
            )
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        from .cloud_folder_picker import CloudFolderPickerDialog

        # For S3, pre-fill bucket from the current remote_folder_id
        current_id = self._remote_id_row.get_text().strip()
        initial_id = None
        initial_name = None
        if provider == "s3" and current_id:
            bucket = current_id.split("/")[0]
            initial_id = current_id
            initial_name = bucket

        def _on_selected(folder_id: str, folder_name: str) -> None:
            self._remote_id_row.set_text(folder_id)
            self._remote_name_row.set_text(folder_name)

        CloudFolderPickerDialog(
            app=self._app,
            provider=provider,
            on_selected=_on_selected,
            parent=self,
            initial_folder_id=initial_id,
            initial_folder_name=initial_name,
        ).present()

    def _on_save_clicked(self, _btn) -> None:
        new_local = self._pending_local_path
        new_remote_id = self._remote_id_row.get_text().strip()
        new_remote_name = self._remote_name_row.get_text().strip()
        interval_idx = self._interval_row.get_selected()

        if not new_local:
            return

        if not new_remote_id:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Remote Folder Required",
                body="Please enter a remote folder ID or bucket name.",
            )
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        # Check for duplicate local path (excluding self)
        for sf in self._app.config.sync_folders:
            if sf.local_path == new_local and sf is not self._folder:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Duplicate Folder",
                    body=f"{new_local} is already configured as a sync folder.",
                )
                dialog.add_response("ok", "OK")
                dialog.present()
                return

        self._folder.local_path = new_local
        self._folder.remote_folder_id = new_remote_id
        if new_remote_name:
            self._folder.remote_folder_name = new_remote_name
        self._folder.sync_interval_seconds = (
            0 if interval_idx == 0 else INTERVAL_OPTIONS[interval_idx - 1]
        )

        conflict_idx = self._conflict_row.get_selected()
        self._folder.conflict_resolution = (
            "" if conflict_idx == 0 else CONFLICT_OPTIONS[conflict_idx - 1]
        )

        self._app.save_config()
        self.close()
        self._on_saved()
