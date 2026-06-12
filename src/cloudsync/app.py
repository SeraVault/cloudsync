"""Main Adw.Application — wires together auth, sync engine, and UI."""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Adw, Gdk, GLib, Gio, Gtk  # noqa: E402

from . import APP_ID  # noqa: E402
from .core.activity_log import ActivityLog  # noqa: E402
from .core.auth import GoogleAuth  # noqa: E402
from .core.config import Config, ProviderAccount, SyncFolder  # noqa: E402
from .core import license as lic  # noqa: E402
from .core.dropbox_auth import DropboxAuth  # noqa: E402
from .core.onedrive_auth import OneDriveAuth  # noqa: E402
from .core.s3_auth import S3Auth  # noqa: E402
from .notifications import Notifier  # noqa: E402
from .sync.base import CloudStorageClient  # noqa: E402
from .sync.gdrive import DriveClient  # noqa: E402
from .sync.engine import SyncEngine  # noqa: E402
from .ui.tray import TrayIcon  # noqa: E402
from .ui.window import MainWindow  # noqa: E402

log = logging.getLogger(__name__)

# Type alias: provider_id -> (client, engine)
_ProviderEntry = Tuple[CloudStorageClient, SyncEngine]


class CloudSyncApp(Adw.Application):
    def __init__(self, background: bool = False):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._background = background

        self.config = Config.load()

        # One auth object per provider type — instantiated once and reused.
        self.auth = GoogleAuth()
        self.s3_auth = S3Auth()
        self.onedrive_auth = OneDriveAuth()
        self.dropbox_auth = DropboxAuth()

        # Registry: provider_id -> (client, engine)
        self._providers: Dict[str, _ProviderEntry] = {}

        self._notifier: Optional[Notifier] = None
        self._window: Optional[MainWindow] = None
        self._tray: Optional[TrayIcon] = None
        self._activity_log = ActivityLog()
        # kept for back-compat; holds the first connected provider's email
        self.account_email: str = ""
        self._bg_threads: list = []

        self._setup_actions()
        self.hold()

    # ------------------------------------------------------------------ #
    # Application lifecycle                                                #
    # ------------------------------------------------------------------ #

    def do_activate(self) -> None:
        # Register the local data/icons dir so the icon resolves when
        # running from the source tree (dev mode or plain `python -m`).
        display = Gdk.Display.get_default()
        if display:
            theme = Gtk.IconTheme.get_for_display(display)
            icons_dir = (
                Path(__file__).resolve().parent.parent.parent
                / "data" / "icons"
            )
            if icons_dir.is_dir():
                theme.add_search_path(str(icons_dir))

        if self._window is None:
            self._window = MainWindow(self)
            self._notifier = Notifier(self)
            self._window.set_icon_name("com.seravault.cloudsync")
            self._window.connect("close-request", self._on_window_close)

        if self._tray is None:
            self._tray = TrayIcon(self)
            # Tray activation is asynchronous (XSI registers via a bus
            # callback; on GNOME the SNI watcher may appear minutes later
            # when the AppIndicator extension loads) — track it.
            self._tray.on_active_changed = self._on_tray_active_changed
            self._tray.start()

        self._window.set_hide_on_close(self._tray.is_active())

        if not self._background:
            self._window.present()

        if not self.config.connected_providers:
            self._show_add_provider_wizard()
        elif not self._providers:
            t = threading.Thread(
                target=self._init_all_providers_async, daemon=True
            )
            self._bg_threads.append(t)
            t.start()

        t = threading.Thread(
            target=self._revalidate_license_async, daemon=True
        )
        self._bg_threads.append(t)
        t.start()

    def _on_tray_active_changed(self, active: bool) -> None:
        if self._window:
            self._window.set_hide_on_close(active)

    def _on_window_close(self, window) -> bool:
        if self._tray and self._tray.is_active():
            window.hide()
            return True
        self.quit()
        return True

    def _on_setup_complete(self) -> None:
        if self._window:
            self._window.refresh()
        if self._providers:
            self.trigger_sync()

    def do_shutdown(self) -> None:
        if self._tray:
            self._tray.stop()
        for _, engine in self._providers.values():
            engine.stop()
        live = [t for t in self._bg_threads if t.is_alive()]
        for t in live:
            t.join(timeout=3)
        Adw.Application.do_shutdown(self)

    # ------------------------------------------------------------------ #
    # Actions                                                              #
    # ------------------------------------------------------------------ #

    def _setup_actions(self) -> None:
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])

        sync_action = Gio.SimpleAction.new("sync-now", None)
        sync_action.connect("activate", lambda *_: self.trigger_sync())
        self.add_action(sync_action)
        self.set_accels_for_action("app.sync-now", ["<Ctrl>r"])

    # ------------------------------------------------------------------ #
    # Provider registry                                                    #
    # ------------------------------------------------------------------ #

    def _build_client(self, provider: str) -> CloudStorageClient:
        if provider == "s3":
            from .sync.s3 import S3Client
            return S3Client(self.s3_auth)
        if provider == "dropbox":
            from .sync.dropbox import DropboxClient
            return DropboxClient(self.dropbox_auth)
        if provider == "onedrive":
            from .sync.onedrive import OneDriveClient
            return OneDriveClient(self.onedrive_auth)
        return DriveClient(self.auth)

    def init_provider(self, provider: str) -> str:
        """Start the engine for *provider* for the first time.

        If an engine already exists for this provider, swaps only the API
        client (no new watcher, no inotify churn). Returns the account's
        display name / email.
        """
        client = self._build_client(provider)
        display_name = client.get_user_email()

        if provider in self._providers:
            _, engine = self._providers[provider]
            engine.swap_client(client)
            self._providers[provider] = (client, engine)
        else:
            def _on_status(msg: str, _p: str = provider):
                if self._window:
                    GLib.idle_add(
                        self._window.set_provider_status, _p, msg
                    )

            def _on_detail(msg: str, _p: str = provider):
                if self._window:
                    GLib.idle_add(
                        self._window.set_provider_detail, _p, msg
                    )

            def _on_progress(done: int, total: int, _p: str = provider):
                if self._window:
                    GLib.idle_add(
                        self._window.set_provider_progress, _p, done, total
                    )

            def _on_error(msg: str, _p: str = provider):
                self._record_activity("error", msg, _p)
                if self._notifier and self.config.notifications_enabled:
                    short = msg.split("\n")[0][:200]
                    GLib.idle_add(self._notifier.sync_error, short)

            engine = SyncEngine(
                self.config, client, provider_id=provider,
                on_status=_on_status, on_detail=_on_detail,
                on_progress=_on_progress, on_error=_on_error,
            )
            engine.start()
            self._providers[provider] = (client, engine)

        if not self.account_email:
            self.account_email = display_name

        return display_name

    def _init_all_providers_async(self) -> None:
        for account in self.config.connected_providers:
            try:
                name = self.init_provider(account.provider)
                account.display_name = name
            except Exception as exc:
                log.error(
                    "Could not init provider %s: %s", account.provider, exc
                )
                self._record_activity(
                    "error",
                    f"Could not initialize provider: {exc}",
                    account.provider,
                )
        if self._window:
            GLib.idle_add(self._window.refresh)

    def _init_provider_async(self, provider: str) -> None:
        try:
            name = self.init_provider(provider)
            for acct in self.config.connected_providers:
                if acct.provider == provider:
                    acct.display_name = name
        except Exception as exc:
            log.error("Could not init provider %s: %s", provider, exc)
            self._record_activity(
                "error", f"Could not initialize provider: {exc}", provider
            )
        if self._window:
            GLib.idle_add(self._window.refresh)

    def _revalidate_license_async(self) -> None:
        """Background thread: revalidate subscription weekly (offline-safe)."""
        import time
        time.sleep(5)
        try:
            info = lic.load()
            if info.is_signed_in and info.needs_revalidation:
                ok, _ = lic.validate()
                if not ok and self._window:
                    title, subtitle = lic.load().status_summary()
                    GLib.idle_add(
                        self._window.show_toast,
                        f"Subscription: {title} — {subtitle}",
                    )
        except Exception as exc:
            log.debug("License revalidation error: %s", exc)

    def connected_provider_ids(self) -> list:
        return [a.provider for a in self.config.connected_providers]

    def get_client(self, provider: str):
        """Return the active CloudStorageClient for *provider*, or None."""
        entry = self._providers.get(provider)
        return entry[0] if entry else None

    # ------------------------------------------------------------------ #
    # Adding / removing providers                                          #
    # ------------------------------------------------------------------ #

    def _show_add_provider_wizard(self) -> None:
        from .ui.setup_wizard import SetupWizard
        wizard = SetupWizard(
            app=self,
            on_complete=self._on_setup_complete,
            parent=self._window,
        )
        wizard.present()

    def add_provider_account(self, provider: str, display_name: str) -> None:
        """Register a freshly authenticated provider and persist it.

        Safe to call from any thread.
        """
        # Update display name if provider already connected
        for acct in self.config.connected_providers:
            if acct.provider == provider:
                acct.display_name = display_name
                self.config.save()
                # Engine may not be running if init failed at startup (e.g.
                # credentials were missing). Start it now that auth succeeded.
                if provider not in self._providers:
                    t = threading.Thread(
                        target=self._init_provider_async,
                        args=(provider,),
                        daemon=True,
                    )
                    t.start()
                    self._bg_threads.append(t)
                elif self._window:
                    GLib.idle_add(self._window.refresh)
                return

        self.config.connected_providers.append(
            ProviderAccount(provider=provider, display_name=display_name)
        )
        self.config.save()
        for _, engine in self._providers.values():
            engine.reload_config(self.config)
        if self._window:
            GLib.idle_add(self._window.refresh)

    def remove_provider_account(self, provider: str) -> None:
        """Disconnect a provider, stop its engine, and remove its folders."""
        if provider in self._providers:
            _, engine = self._providers.pop(provider)
            engine.stop()

        if provider == "s3":
            self.s3_auth.sign_out()
        elif provider == "dropbox":
            self.dropbox_auth.sign_out()
        elif provider == "onedrive":
            self.onedrive_auth.sign_out()
        else:
            self.auth.sign_out()

        self.config.connected_providers = [
            a for a in self.config.connected_providers
            if a.provider != provider
        ]
        self.config.sync_folders = [
            f for f in self.config.sync_folders if f.provider != provider
        ]
        self.save_config()
        if self._window:
            GLib.idle_add(self._window.refresh)

    # ------------------------------------------------------------------ #
    # Config helpers                                                       #
    # ------------------------------------------------------------------ #

    def save_config(self, config: Optional[Config] = None) -> None:
        if config is not None:
            self.config = config
        self.config.save()
        for _, engine in self._providers.values():
            engine.reload_config(self.config)

    def _show_upgrade_dialog(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Upgrade to CloudSync Pro",
            body=(
                "The free tier is limited to 1 cloud provider and "
                "1 sync folder. Subscribe to connect unlimited "
                "providers and folders."
            ),
        )
        dialog.add_response("cancel", "Not Now")
        dialog.add_response("upgrade", "View Plans")
        dialog.set_response_appearance(
            "upgrade", Adw.ResponseAppearance.SUGGESTED
        )
        dialog.connect("response", self._on_upgrade_dialog_response)
        if self._window:
            dialog.present(self._window)

    def _on_upgrade_dialog_response(self, dialog, response: str) -> None:
        if response == "upgrade":
            subprocess.Popen(
                ["xdg-open", lic.SUBSCRIBE_URL]
            )

    def add_sync_folder(
        self,
        local_path: str,
        provider: str = "gdrive",
        remote_folder_id: str = "root",
        remote_folder_name: str = "",
    ) -> None:
        if provider == "s3" and (
            not remote_folder_id or remote_folder_id == "root"
        ):
            for folder in self.config.sync_folders:
                if (
                    folder.provider == "s3"
                    and folder.remote_folder_id not in ("", "root")
                ):
                    remote_folder_id = folder.remote_folder_id
                    break
            else:
                raise ValueError(
                    "For S3, set Bucket or Bucket/Path first "
                    "(example: my-bucket or my-bucket/folder)."
                )

        if not remote_folder_name:
            if remote_folder_id in ("root", ""):
                if provider == "gdrive":
                    remote_folder_name = "My Drive"
                elif provider == "dropbox":
                    remote_folder_name = "Dropbox"
                else:
                    remote_folder_name = "Cloud Root"
            else:
                remote_folder_name = remote_folder_id

        existing = [f.local_path for f in self.config.sync_folders]
        if local_path not in existing:
            self.config.sync_folders.append(SyncFolder(
                local_path=local_path,
                provider=provider,
                remote_folder_id=remote_folder_id,
                remote_folder_name=remote_folder_name,
            ))
            self.save_config()

    def remove_sync_folder(self, folder: SyncFolder) -> None:
        self.config.sync_folders = [
            f for f in self.config.sync_folders
            if f.local_path != folder.local_path
        ]
        self.save_config()

    # ------------------------------------------------------------------ #
    # Sync trigger                                                         #
    # ------------------------------------------------------------------ #

    def trigger_sync(self) -> None:
        if not self._providers:
            log.warning("trigger_sync called but no providers are running")
            if self._window:
                self._window.show_toast(
                    "Not connected — please add a provider first."
                )
            return

        self._record_activity(
            "info",
            f"Started sync for {len(self._providers)} provider(s).",
        )

        if self._tray:
            GLib.idle_add(self._tray.set_status, True)

        providers = list(self._providers.items())
        results_lock = threading.Lock()
        total_up = [0]
        total_down = [0]
        all_errors: list = []
        remaining = [len(providers)]

        def _run_one(engine):
            result = engine.run_sync()
            with results_lock:
                total_up[0] += result.uploaded
                total_down[0] += result.downloaded
                all_errors.extend(result.errors)
                remaining[0] -= 1
                done = remaining[0] == 0
            if done:
                if self._tray:
                    GLib.idle_add(self._tray.set_status, False)
                if self._notifier and self.config.notifications_enabled:
                    if all_errors:
                        GLib.idle_add(
                            self._notifier.sync_error, all_errors[0]
                        )
                    else:
                        GLib.idle_add(
                            self._notifier.sync_finished,
                            total_up[0],
                            total_down[0],
                        )
                if self._window:
                    msg = f"Synced — ↑{total_up[0]} ↓{total_down[0]}"
                    GLib.idle_add(self._window.show_toast, msg)
                if all_errors:
                    self._record_activity(
                        "error",
                        f"Sync finished with {len(all_errors)} error(s).",
                    )
                else:
                    self._record_activity(
                        "info",
                        f"Sync finished: uploaded {total_up[0]}, "
                        f"downloaded {total_down[0]}.",
                    )

        for _provider_id, (_, engine) in providers:
            t = threading.Thread(target=_run_one, args=(engine,), daemon=True)
            self._bg_threads.append(t)
            t.start()

    def trigger_folder_sync(self, folder: "SyncFolder") -> None:
        """Trigger an immediate sync for a single folder."""
        entry = self._providers.get(folder.provider)
        if not entry:
            log.warning("trigger_folder_sync called but %s engine is not running", folder.provider)
            if self._window:
                self._window.show_toast("Provider not connected.")
            return

        _, engine = entry
        local_path = folder.local_path

        self._record_activity(
            "info",
            f"Started sync for {Path(local_path).name}.",
            folder.provider,
        )

        def _status(msg: str) -> None:
            if self._window:
                GLib.idle_add(
                    self._window.set_folder_status, local_path, msg
                )

        def _progress(done: int, total: int) -> None:
            if self._window:
                GLib.idle_add(
                    self._window.set_folder_progress, local_path, done, total
                )

        def _detail(msg: str) -> None:
            if self._window:
                GLib.idle_add(
                    self._window.set_folder_detail, local_path, msg
                )

        def _run():
            result = engine.run_folders(
                [folder],
                on_status=_status,
                on_progress=_progress,
                on_detail=_detail,
            )
            if self._window:
                if result.errors:
                    GLib.idle_add(
                        self._window.show_toast,
                        f"Sync error: {result.errors[0]}",
                    )
                else:
                    up = result.uploaded
                    down = result.downloaded
                    name = Path(local_path).name
                    GLib.idle_add(
                        self._window.show_toast,
                        f"{name} — ↑{up} ↓{down}",
                    )

        t = threading.Thread(target=_run, daemon=True)
        self._bg_threads.append(t)
        t.start()

    # ------------------------------------------------------------------ #
    # Legacy sign_out (kept so existing callers don't break)              #
    # ------------------------------------------------------------------ #

    def sign_out(self, provider: Optional[str] = None) -> None:
        if provider is None:
            for p in list(self._providers.keys()):
                self.remove_provider_account(p)
        else:
            self.remove_provider_account(provider)
        self.account_email = ""

    @property
    def activity_log(self) -> ActivityLog:
        return self._activity_log

    def clear_activity_log(self) -> None:
        self._activity_log.clear()
        if self._window:
            GLib.idle_add(self._window.refresh_activity_log)

    def _record_activity(
        self,
        level: str,
        message: str,
        provider: str = "",
    ) -> None:
        self._activity_log.append(level, message, provider)
        if self._window:
            GLib.idle_add(self._window.refresh_activity_log)
