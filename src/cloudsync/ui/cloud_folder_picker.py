"""Cloud folder picker dialog — lets users browse and select a remote folder.

Supports Google Drive (folder IDs), Dropbox (path strings), and S3 (bucket/prefix).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

if TYPE_CHECKING:
    from ..app import CloudSyncApp

log = logging.getLogger(__name__)

# Root folder sentinel values for each supported provider
_ROOTS: Dict[str, Tuple[str, str]] = {
    "gdrive":  ("root", "My Drive"),
    "dropbox": ("",     "Dropbox"),
    # S3 root is dynamic (bucket name) — callers pass initial_folder_id/name
}


class CloudFolderPickerDialog(Adw.Window):
    """Modal dialog for browsing and selecting a cloud provider folder.

    Calls ``on_selected(folder_id, display_name)`` when the user confirms.

    *folder_id* is the Drive folder ID (for GDrive) or the Dropbox path
    string (for Dropbox).  The root values are ``"root"`` and ``""``
    respectively.
    """

    def __init__(
        self,
        app: "CloudSyncApp",
        provider: str,
        on_selected: Callable[[str, str], None],
        parent: Gtk.Window,
        initial_folder_id: Optional[str] = None,
        initial_folder_name: Optional[str] = None,
    ) -> None:
        # Resolve root: callers can override (required for S3 whose root is the bucket)
        if initial_folder_id is not None:
            root_id = initial_folder_id
            root_name = initial_folder_name or initial_folder_id
        else:
            root_id, root_name = _ROOTS.get(provider, ("root", "Cloud"))
        super().__init__(
            title=f"Choose {root_name} Folder",
            modal=True,
            transient_for=parent,
            default_width=420,
            default_height=520,
        )
        self._app = app
        self._provider = provider
        self._on_selected = on_selected
        self._root_id = root_id
        self._root_name = root_name

        # Navigation history — each entry is (folder_id, display_name).
        # Empty means we're currently at root.
        self._nav_stack: List[Tuple[str, str]] = []

        # ---------------------------------------------------------------- #
        # Layout                                                             #
        # ---------------------------------------------------------------- #
        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        self._back_btn = Gtk.Button(
            icon_name="go-previous-symbolic",
            tooltip_text="Go back",
        )
        self._back_btn.set_sensitive(False)
        self._back_btn.connect("clicked", self._on_back)
        header.pack_start(self._back_btn)

        self._select_btn = Gtk.Button(
            label="Use This Folder",
            css_classes=["suggested-action"],
        )
        self._select_btn.connect("clicked", self._on_select_current)
        header.pack_end(self._select_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        toolbar.set_content(outer)

        # ── S3: persistent bucket name bar ──────────────────────────────
        self._bucket_bar: Optional[Gtk.Box] = None
        self._bucket_entry: Optional[Gtk.Entry] = None
        if provider == "s3":
            bucket_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=6,
                margin_top=8,
                margin_bottom=0,
                margin_start=16,
                margin_end=16,
            )
            bucket_label = Gtk.Label(
                label="Bucket:",
                css_classes=["dim-label"],
            )
            self._bucket_entry = Gtk.Entry(
                placeholder_text="e.g. my-bucket  or  my-bucket/photos",
                hexpand=True,
            )
            if initial_folder_id:
                self._bucket_entry.set_text(initial_folder_id.split("/")[0])
            self._bucket_entry.connect("activate", self._on_bucket_browse)
            browse_btn = Gtk.Button(
                icon_name="folder-open-symbolic",
                tooltip_text="Browse this bucket",
                css_classes=["flat"],
            )
            browse_btn.connect("clicked", self._on_bucket_browse)
            bucket_box.append(bucket_label)
            bucket_box.append(self._bucket_entry)
            bucket_box.append(browse_btn)
            outer.append(bucket_box)
            self._bucket_bar = bucket_box
        # ────────────────────────────────────────────────────────────────

        self._location_label = Gtk.Label(
            label=root_name,
            halign=Gtk.Align.START,
            css_classes=["heading"],
            margin_top=10,
            margin_bottom=4,
            margin_start=16,
            margin_end=16,
            ellipsize=3,  # Pango.EllipsizeMode.END
        )
        outer.append(self._location_label)

        self._stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            vexpand=True,
        )
        outer.append(self._stack)

        # Loading page
        loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
            spacing=12,
        )
        loading_box.append(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        loading_box.append(Gtk.Label(label="Loading folders…", css_classes=["dim-label"]))
        self._stack.add_named(loading_box, "loading")

        # Folder list page
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        self._list_box = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=["boxed-list"],
            margin_top=8,
            margin_bottom=8,
            margin_start=16,
            margin_end=16,
        )
        scroller.set_child(self._list_box)
        self._stack.add_named(scroller, "list")

        # Empty state
        self._stack.add_named(
            Adw.StatusPage(
                icon_name="folder-symbolic",
                title="No Subfolders",
                description="This folder has no subfolders to navigate into.",
            ),
            "empty",
        )

        # Error state
        self._error_page = Adw.StatusPage(
            icon_name="dialog-error-symbolic",
            title="Could Not Load Folders",
        )
        self._stack.add_named(self._error_page, "error")

        # S3 no-bucket prompt
        self._stack.add_named(
            Adw.StatusPage(
                icon_name="network-server-symbolic",
                title="Enter a Bucket Name",
                description="Type your bucket name in the field above and click the browse button.",
            ),
            "no_bucket",
        )

        # Begin at root (or no_bucket prompt for S3 with no initial folder)
        if provider == "s3" and not initial_folder_id:
            self._stack.set_visible_child_name("no_bucket")
            self._select_btn.set_sensitive(False)
        else:
            self._show_folder(root_id, root_name)

    def _on_bucket_browse(self, _widget) -> None:
        """Called when the user clicks Browse or presses Enter in the bucket field."""
        if self._bucket_entry is None:
            return
        bucket = self._bucket_entry.get_text().strip()
        if not bucket:
            return
        self._root_id = bucket
        self._root_name = bucket
        self._nav_stack.clear()
        self._select_btn.set_sensitive(True)
        self._show_folder(bucket, bucket)

    # ------------------------------------------------------------------ #
    # Navigation state                                                     #
    # ------------------------------------------------------------------ #

    def _current_id(self) -> str:
        return self._nav_stack[-1][0] if self._nav_stack else self._root_id

    def _current_name(self) -> str:
        return self._nav_stack[-1][1] if self._nav_stack else self._root_name

    # ------------------------------------------------------------------ #
    # Folder loading                                                       #
    # ------------------------------------------------------------------ #

    def _show_folder(self, folder_id: str, folder_name: str) -> None:
        """Fetch and display the subfolders of *folder_id* (does not touch stack)."""
        self._back_btn.set_sensitive(bool(self._nav_stack))
        self._location_label.set_label(folder_name)
        self._stack.set_visible_child_name("loading")

        def _fetch() -> None:
            try:
                client = self._app.get_client(self._provider)
                if client is None:
                    raise RuntimeError("Provider not connected. Please sign in first.")
                folders = client.list_subfolders(folder_id)
                GLib.idle_add(self._populate, folders)
            except Exception as exc:
                GLib.idle_add(self._on_load_error, str(exc))

        threading.Thread(target=_fetch, daemon=True).start()

    def _populate(self, folders: List[Dict]) -> None:
        # Clear previous rows
        child = self._list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt

        if not folders:
            self._stack.set_visible_child_name("empty")
            return

        for item in folders:
            fid, fname = item["id"], item["name"]
            row = Adw.ActionRow(title=fname, activatable=True)
            row.set_icon_name("folder-symbolic")
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
            row.connect(
                "activated",
                lambda _r, fid=fid, fn=fname: self._on_row_activated(fid, fn),
            )
            self._list_box.append(row)

        self._stack.set_visible_child_name("list")

    def _on_load_error(self, message: str) -> None:
        self._error_page.set_description(message)
        self._stack.set_visible_child_name("error")

    # ------------------------------------------------------------------ #
    # Signal handlers                                                      #
    # ------------------------------------------------------------------ #

    def _on_row_activated(self, folder_id: str, folder_name: str) -> None:
        self._nav_stack.append((folder_id, folder_name))
        self._show_folder(folder_id, folder_name)

    def _on_back(self, _btn) -> None:
        if self._nav_stack:
            self._nav_stack.pop()
        if self._nav_stack:
            parent_id, parent_name = self._nav_stack[-1]
            # Pop and re-push via _on_row_activated so the display is consistent
            self._nav_stack.pop()
            self._nav_stack.append((parent_id, parent_name))
            self._show_folder(parent_id, parent_name)
        else:
            self._show_folder(self._root_id, self._root_name)

    def _on_select_current(self, _btn) -> None:
        folder_id = self._current_id()
        folder_name = self._current_name()
        self.close()
        self._on_selected(folder_id, folder_name)
