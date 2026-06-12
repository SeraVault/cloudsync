"""Two-way sync engine with SQLite state tracking."""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..core.config import Config, DATA_DIR, SyncFolder
from .base import CloudStorageClient
from .gdrive import GDOC_STUB_EXTENSIONS, GOOGLE_NATIVE_MIMES, write_gdoc_link
from .onedrive import ONEDRIVE_STUB_EXTENSIONS, write_onedrive_link
from .watcher import LocalWatcher

log = logging.getLogger(__name__)

_ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"
_ISO_FMT_SHORT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_drive_time(ts: str) -> float:
    for fmt in (_ISO_FMT, _ISO_FMT_SHORT):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


class SyncResult:
    def __init__(self) -> None:
        self.uploaded = 0
        self.downloaded = 0
        self.errors: List[str] = []


class SyncEngine:
    """Orchestrates two-way sync between local folders and Google Drive.

    Thread-safety: :meth:`run_sync` is designed to be called from a background
    thread.  UI callbacks are posted via the optional *on_status* callable which
    should be safe to call from any thread (e.g. use ``GLib.idle_add`` when
    wrapping it for GTK).
    """

    def __init__(
        self,
        config: Config,
        drive: CloudStorageClient,
        provider_id: str = "gdrive",
        on_status: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_detail: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._drive = drive
        self._provider_id = provider_id
        self._on_status = on_status
        self._on_progress = on_progress  # callback(done, total)
        self._on_detail = on_detail      # callback(per-file message)
        self._on_error = on_error        # callback(error_message) — fires on any sync error
        self._watcher = LocalWatcher()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._db: Optional[sqlite3.Connection] = None
        self._running = False
        self._timer_thread: Optional[threading.Thread] = None

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(DATA_DIR / "state.db"), check_same_thread=False)
        self._init_db()

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the background watcher and periodic sync timer."""
        self._running = True
        self._refresh_watchers()
        self._watcher.start()
        self._timer_thread = threading.Thread(target=self._timer_loop, daemon=True)
        self._timer_thread.start()
        self._watcher_thread = threading.Thread(target=self._watcher_loop, daemon=True)
        self._watcher_thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        self._watcher.stop()
        if self._db:
            self._db.close()
            self._db = None

    def reload_config(self, config: Config) -> None:
        self._config = config
        self._refresh_watchers()

    def swap_client(self, client: CloudStorageClient) -> None:
        """Replace the API client without restarting the engine or watcher.

        Acquires the sync lock so no sync is in progress when the swap happens.
        """
        with self._lock:
            self._drive = client

    # ------------------------------------------------------------------ #
    # Public sync                                                          #
    # ------------------------------------------------------------------ #

    def run_sync(self) -> SyncResult:
        """Sync all enabled folders for this provider.  Safe to call from any thread."""
        active = [
            f for f in self._config.sync_folders
            if f.enabled and f.provider == self._provider_id
        ]
        return self._run_folders(active)

    def run_folders(self, folders: List[SyncFolder],
                     on_status: Optional[Callable[[str], None]] = None,
                     on_progress: Optional[Callable[[int, int], None]] = None,
                     on_detail: Optional[Callable[[str], None]] = None) -> SyncResult:
        """Sync only the specified enabled folders for this provider.

        Optional *on_status*, *on_progress*, and *on_detail* override the
        engine-level callbacks for this call only — useful for per-folder
        UI updates without affecting other rows.
        """
        active = [
            folder for folder in folders
            if folder.enabled and folder.provider == self._provider_id
        ]
        return self._run_folders(active, on_status=on_status,
                                 on_progress=on_progress, on_detail=on_detail)

    def _run_folders(self, folders: list,
                     on_status: Optional[Callable[[str], None]] = None,
                     on_progress: Optional[Callable[[int, int], None]] = None,
                     on_detail: Optional[Callable[[str], None]] = None) -> SyncResult:
        """Sync a specific list of folders.  Acquires the lock."""
        _status   = on_status   or self._status
        _progress = on_progress or self._progress
        _detail   = on_detail   or self._detail
        result = SyncResult()
        with self._lock:
            try:
                _status("Syncing…")
                _progress(0, len(folders))
                for idx, folder in enumerate(folders):
                    self._sync_folder(folder, result)
                    _progress(idx + 1, len(folders))
                _progress(0, 0)
                _detail("")
                _status("Idle")
            except Exception as exc:
                msg = str(exc)
                log.exception("Sync error: %s", msg)
                result.errors.append(msg)
                _progress(0, 0)
                _detail("")
                _status(f"Error: {msg}")
                self._error(msg)
        return result

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _timer_loop(self) -> None:
        # Track when each folder is next due.  Keyed by local_path.
        next_due: dict = {}
        TICK = 5  # seconds between due-date checks

        while self._running:
            now = time.monotonic()
            folders_due = []
            for folder in self._config.sync_folders:
                if not (folder.enabled and folder.provider == self._provider_id):
                    continue
                interval = folder.effective_interval(
                    self._config.sync_interval_seconds
                )
                due = next_due.get(folder.local_path, 0)
                if now >= due:
                    folders_due.append(folder)
                    next_due[folder.local_path] = now + interval

            if folders_due and self._running:
                try:
                    self._run_folders(folders_due)
                except Exception as exc:
                    log.exception("Timer sync error: %s", exc)

            self._stop_event.wait(TICK)

    def _watcher_loop(self) -> None:
        """Upload local changes as watchdog events arrive.

        Only handles local→remote direction.  Remote→local polling is left
        entirely to the timer loop so we never trigger redundant Drive scans.

        A short debounce window coalesces rapid saves (editor temp files, etc.)
        into a single upload per file.  Events are additionally filtered against
        the stored mtime so that read-only opens (IN_OPEN) which watchdog can
        emit as spurious modified events don't cause unnecessary uploads.
        """
        DEBOUNCE = 2.0  # seconds
        pending: dict = {}  # path → event_type

        while self._running:
            # Wait for the first event, then drain anything that arrives within
            # the debounce window before acting.
            try:
                event_type, path = self._watcher.queue.get(timeout=DEBOUNCE)
                pending[path] = event_type
            except queue.Empty:
                pass
            except Exception as exc:
                log.exception("Watcher queue read error: %s", exc)

            while True:
                try:
                    event_type, path = self._watcher.queue.get_nowait()
                    pending[path] = event_type
                except queue.Empty:
                    break
                except Exception as exc:
                    log.exception("Watcher queue drain error: %s", exc)
                    break

            if not pending:
                continue

            batch = dict(pending)
            pending.clear()

            with self._lock:
                result = SyncResult()
                for path, event_type in batch.items():
                    folder = self._find_folder_for(path)
                    if not folder:
                        continue
                    # Skip events for files whose mtime hasn't changed —
                    # these are read-opens mis-reported as modifications.
                    if event_type != "deleted" and path.exists():
                        state = self._get_file_state(
                            folder.local_path,
                            path.relative_to(
                                Path(folder.local_path).expanduser()
                            ).as_posix(),
                        )
                        if state and abs(path.stat().st_mtime - state["local_mtime"]) < 1:
                            continue
                    try:
                        self._status("Syncing…")
                        self._handle_local_change(event_type, path, folder, result)
                    except Exception as exc:
                        log.exception("Watcher upload error %s: %s", path, exc)
                        result.errors.append(str(exc))

                if result.errors:
                    self._status(f"Error: {result.errors[0]}")
                    self._error(result.errors[0])
                elif result.uploaded:
                    self._status("Idle")
                    self._detail("")
                else:
                    self._status("Idle")

    def _refresh_watchers(self) -> None:
        for folder in self._config.sync_folders:
            if folder.provider != self._provider_id:
                continue
            p = Path(folder.local_path).expanduser()
            if p.exists() and folder.enabled:
                self._watcher.add_path(p)

    def _sync_folder(self, folder: SyncFolder, result: SyncResult) -> None:
        local_root = Path(folder.local_path).expanduser()
        if local_root.is_symlink():
            log.warning(
                "Sync folder %s is a symlink — skipping to prevent "
                "accidental upload of sensitive data.",
                local_root,
            )
            return
        local_root.mkdir(parents=True, exist_ok=True)

        # Check for remote changes since last sync.
        # Key is scoped to provider so multiple engines don't share tokens.
        token_key = f"page_token:{self._provider_id}:{folder.local_path}"
        page_token = self._get_state(token_key)

        # If the gdoc-stubs feature hasn't run for this folder yet, force a
        # full sync.  We track this with a dedicated KV flag so the check is
        # O(1) and doesn't scan the filesystem or file table on every sync.
        stubs_flag_key = f"gdoc_stubs_done:{folder.local_path}"
        if page_token and self._provider_id == "gdrive":
            if not self._get_state(stubs_flag_key):
                log.info("gdoc stubs not yet created for %s — forcing full sync",
                         folder.local_path)
                self._del_state(token_key)
                page_token = None

        if page_token:
            changes, new_token = self._drive.get_changes(page_token)
            total = len(changes)
            self._progress(0, total)
            for idx, change in enumerate(changes):
                self._handle_drive_change(change, folder, local_root, result)
                self._progress(idx + 1, total)
            self._set_state(token_key, new_token)
            self._upload_local_changes(folder, local_root, result)
        else:
            # First sync (or forced re-reconcile): full scan
            self._full_sync(folder, local_root, result)
            token = self._drive.get_start_page_token()
            self._set_state(token_key, token)
            # Mark that gdoc stubs have been created for this folder so the
            # migration check above never forces a redundant full sync again.
            if self._provider_id == "gdrive":
                self._set_state(stubs_flag_key, "1")

    def _upload_local_changes(self, folder: SyncFolder, local_root: Path, result: SyncResult) -> None:
        """Upload any local files that are new or newer than the DB records.

        Runs after every incremental sync to catch files the watcher missed
        (e.g. files added while the app wasn't running, or on filesystems
        where inotify is unavailable).  Only hits the API for files whose
        mtime has advanced — everything else is an O(1) DB lookup.
        """
        from .gdrive import GDOC_STUB_EXTENSIONS
        from .onedrive import ONEDRIVE_STUB_EXTENSIONS
        skip_exts = GDOC_STUB_EXTENSIONS | ONEDRIVE_STUB_EXTENSIONS

        for local_path in local_root.rglob("*"):
            if not local_path.is_file() or local_path.is_symlink():
                continue
            if local_path.suffix in skip_exts:
                continue
            rel = local_path.relative_to(local_root).as_posix()
            state = self._get_file_state(folder.local_path, rel)
            try:
                mtime = local_path.stat().st_mtime
            except OSError:
                continue
            if not state:
                self._upload(local_path, rel, folder, local_root, result)
            elif mtime > state["local_mtime"] + 1:
                self._upload(local_path, rel, folder, local_root, result, state["drive_id"])

    def _full_sync(self, folder: SyncFolder, local_root: Path, result: SyncResult) -> None:
        """Reconcile local and Drive contents on first sync."""
        self._status(f"Scanning {folder.remote_folder_name}…")
        drive_files = dict(
            self._drive.list_files_recursive(folder.remote_folder_id)
        )

        local_files = [
            p for p in local_root.rglob("*")
            if p.is_file() and not p.is_symlink()
        ]
        drive_only = {
            rel: meta for rel, meta in drive_files.items()
            if not (local_root / rel).exists()
        }
        total = len(local_files) + len(drive_only)
        done = 0
        self._progress(done, total)

        for local_path in local_files:
            rel = local_path.relative_to(local_root).as_posix()
            state = self._get_file_state(folder.local_path, rel)

            if rel in drive_files:
                drive_meta = drive_files[rel]
                drive_mtime = _parse_drive_time(drive_meta.get("modifiedTime", ""))
                local_mtime = local_path.stat().st_mtime

                if state:
                    local_changed = local_mtime > state["local_mtime"] + 1
                    remote_changed = drive_mtime > _parse_drive_time(state["drive_modified"]) + 1
                    if local_changed and remote_changed:
                        self._resolve_conflict(local_path, drive_meta, folder, local_root, result)
                    elif local_changed:
                        self._upload(local_path, rel, folder, local_root, result, drive_meta["id"])
                    elif remote_changed:
                        self._download(drive_meta, local_path, folder, rel, result)
                else:
                    # No prior state — pick the newer side.  Equal mtimes
                    # means already in sync; do nothing.
                    if local_mtime > drive_mtime + 1:
                        self._upload(local_path, rel, folder, local_root, result, drive_meta["id"])
                    elif drive_mtime > local_mtime + 1:
                        self._download(drive_meta, local_path, folder, rel, result)
            else:
                self._upload(local_path, rel, folder, local_root, result)

            done += 1
            self._progress(done, total)

        for rel, drive_meta in drive_only.items():
            local_path = local_root / rel
            self._download(drive_meta, local_path, folder, rel, result)
            done += 1
            self._progress(done, total)

    def _handle_drive_change(
        self, change: Dict, folder: SyncFolder, local_root: Path, result: SyncResult
    ) -> None:
        file_meta = change.get("file")
        if not file_meta:
            return

        mime = file_meta.get("mimeType", "")
        drive_id = change["fileId"]

        # For Google Workspace native types, reconstruct the stub meta so we
        # can create/update the local link file.  Types we can't stub are skipped.
        from .gdrive import GDOC_STUBS
        if mime in GDOC_STUBS:
            ext, _ = GDOC_STUBS[mime]
            stub_name = file_meta["name"] + ext
            file_meta = {**file_meta, "name": stub_name, "is_gdoc_stub": True}
        elif mime in GOOGLE_NATIVE_MIMES:
            return  # no useful stub (shortcut, folder, etc.)

        # Look up existing state by drive_id
        state = self._get_file_state_by_drive_id(drive_id)

        if change.get("removed") or file_meta.get("trashed"):
            if state:
                rel = state["rel_path"]
                local_path = local_root / rel
                if local_path.exists():
                    local_mtime = local_path.stat().st_mtime
                    if abs(local_mtime - state["local_mtime"]) < 2:
                        local_path.unlink(missing_ok=True)
                        self._del_file_state(folder.local_path, rel)
            return

        # Determine the relative path — use stored state if we have it,
        # otherwise derive it from the file name (new file not yet tracked).
        if state:
            rel = state["rel_path"]
        else:
            rel = file_meta["name"]

        local_path = local_root / rel

        if local_path.exists() and state:
            local_mtime = local_path.stat().st_mtime
            local_changed = local_mtime > state["local_mtime"] + 1
            if local_changed and not file_meta.get("is_gdoc_stub"):
                self._resolve_conflict(local_path, file_meta, folder, local_root, result)
            else:
                self._download(file_meta, local_path, folder, rel, result)
        else:
            self._download(file_meta, local_path, folder, rel, result)

    def _handle_local_change(
        self,
        event_type: str,
        path: Path,
        folder: SyncFolder,
        result: SyncResult,
    ) -> None:
        local_root = Path(folder.local_path).expanduser()
        rel = path.relative_to(local_root).as_posix()
        state = self._get_file_state(folder.local_path, rel)

        # Stub files are read-only local links — never push changes to the cloud.
        if path.suffix in GDOC_STUB_EXTENSIONS | ONEDRIVE_STUB_EXTENSIONS:
            return

        if event_type == "deleted":
            if state and state["drive_id"]:
                self._drive.trash_file(state["drive_id"])
                self._del_file_state(folder.local_path, rel)
        else:
            if not path.exists():
                # File was created and immediately deleted (temp file, editor
                # swap file, etc.) — nothing to upload.
                return
            # Only upload if the file is genuinely newer than our last recorded
            # state, guarding against read-open events on some filesystems.
            current_mtime = path.stat().st_mtime
            if state and current_mtime <= state["local_mtime"] + 1:
                return
            drive_id = state["drive_id"] if state else None
            self._upload(path, rel, folder, local_root, result, drive_id)

    # ------------------------------------------------------------------ #
    # Upload / Download helpers                                            #
    # ------------------------------------------------------------------ #

    def _upload(
        self,
        local_path: Path,
        rel: str,
        folder: SyncFolder,
        local_root: Path,
        result: SyncResult,
        existing_drive_id: Optional[str] = None,
    ) -> None:
        # Never upload link stubs back to the cloud.
        if local_path.suffix in GDOC_STUB_EXTENSIONS | ONEDRIVE_STUB_EXTENSIONS:
            return

        # Ensure Drive folder hierarchy exists
        parts = rel.split("/")
        parent_id = folder.remote_folder_id
        for part in parts[:-1]:
            parent_id = self._drive.get_or_create_folder(part, parent_id)

        self._detail(f"↑ {local_path.name}")
        meta = self._drive.upload_file(local_path, parent_id, existing_drive_id)
        stat = local_path.stat()
        self._save_file_state(
            folder.local_path, rel, meta["id"], stat.st_mtime,
            stat.st_size, meta.get("modifiedTime", ""), meta.get("md5Checksum", "")
        )
        result.uploaded += 1

    def _download(
        self, drive_meta: Dict, local_path: Path, folder: SyncFolder, rel: str, result: SyncResult
    ) -> None:
        self._detail(f"↓ {drive_meta['name']}")
        if drive_meta.get("is_gdoc_stub"):
            write_gdoc_link(local_path, drive_meta["id"], drive_meta["mimeType"])
        elif drive_meta.get("is_onedrive_stub"):
            write_onedrive_link(
                local_path, drive_meta["id"], drive_meta["name"],
                drive_meta["stub_url_template"],
            )
        else:
            try:
                self._drive.download_file(drive_meta["id"], local_path)
            except Exception as exc:
                msg = str(exc)
                # AccessDenied on download means the key lacks s3:GetObject.
                # Skip the file and surface a clear warning rather than
                # aborting the entire sync run.
                if "AccessDenied" in msg or "Access Denied" in msg or "403" in msg:
                    log.warning(
                        "Download skipped for %s — Access Denied. "
                        "Add s3:GetObject to your IAM user policy.",
                        rel,
                    )
                    self._error(
                        f"Cannot download '{rel}': Access Denied. "
                        "Add s3:GetObject to your AWS IAM user policy."
                    )
                    result.errors.append(msg)
                    return
                raise
        stat = local_path.stat()
        self._save_file_state(
            folder.local_path, rel, drive_meta["id"], stat.st_mtime,
            stat.st_size, drive_meta.get("modifiedTime", ""), drive_meta.get("md5Checksum", "")
        )
        result.downloaded += 1

    def _resolve_conflict(
        self,
        local_path: Path,
        drive_meta: Dict,
        folder: SyncFolder,
        local_root: Path,
        result: SyncResult,
    ) -> None:
        strategy = folder.conflict_resolution or self._config.conflict_resolution
        rel = local_path.relative_to(local_root).as_posix()
        log.warning("Conflict on %s — strategy: %s", rel, strategy)

        if strategy == "local_wins":
            state = self._get_file_state(folder.local_path, rel)
            self._upload(local_path, rel, folder, local_root, result, state["drive_id"] if state else None)

        elif strategy == "remote_wins":
            self._download(drive_meta, local_path, folder, rel, result)

        else:  # keep_both
            suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
            conflict_path = local_path.with_suffix(f".conflict_{suffix}{local_path.suffix}")
            local_path.rename(conflict_path)
            # Upload renamed local version, then download Drive version
            conflict_rel = conflict_path.relative_to(local_root).as_posix()
            self._upload(conflict_path, conflict_rel, folder, local_root, result)
            self._download(drive_meta, local_path, folder, rel, result)

    # ------------------------------------------------------------------ #
    # Utilities                                                            #
    # ------------------------------------------------------------------ #

    def _find_folder_for(self, path: Path) -> Optional[SyncFolder]:
        for folder in self._config.sync_folders:
            root = Path(folder.local_path).expanduser()
            try:
                path.relative_to(root)
                return folder
            except ValueError:
                continue
        return None

    def _status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)

    def _progress(self, done: int, total: int) -> None:
        if self._on_progress:
            self._on_progress(done, total)

    def _detail(self, msg: str) -> None:
        if self._on_detail:
            self._on_detail(msg)

    def _error(self, msg: str) -> None:
        if self._on_error:
            self._on_error(msg)

    # ------------------------------------------------------------------ #
    # SQLite state                                                         #
    # ------------------------------------------------------------------ #

    def _init_db(self) -> None:
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    folder_root TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    drive_id TEXT,
                    local_mtime REAL,
                    local_size INTEGER,
                    drive_modified TEXT,
                    drive_md5 TEXT,
                    PRIMARY KEY (folder_root, rel_path)
                )
            """)
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

    def _get_file_state(self, folder_root: str, rel: str) -> Optional[Dict]:
        cur = self._db.execute(
            "SELECT * FROM files WHERE folder_root=? AND rel_path=?", (folder_root, rel)
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _get_file_state_by_drive_id(self, drive_id: str) -> Optional[Dict]:
        cur = self._db.execute("SELECT * FROM files WHERE drive_id=?", (drive_id,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def _save_file_state(
        self,
        folder_root: str,
        rel: str,
        drive_id: str,
        local_mtime: float,
        local_size: int,
        drive_modified: str,
        drive_md5: str,
    ) -> None:
        with self._db:
            self._db.execute("""
                INSERT INTO files (folder_root, rel_path, drive_id, local_mtime, local_size, drive_modified, drive_md5)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder_root, rel_path) DO UPDATE SET
                    drive_id=excluded.drive_id,
                    local_mtime=excluded.local_mtime,
                    local_size=excluded.local_size,
                    drive_modified=excluded.drive_modified,
                    drive_md5=excluded.drive_md5
            """, (folder_root, rel, drive_id, local_mtime, local_size, drive_modified, drive_md5))

    def _del_file_state(self, folder_root: str, rel: str) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM files WHERE folder_root=? AND rel_path=?", (folder_root, rel)
            )

    def _get_state(self, key: str) -> Optional[str]:
        cur = self._db.execute("SELECT value FROM kv WHERE key=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    def _set_state(self, key: str, value: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def _del_state(self, key: str) -> None:
        with self._db:
            self._db.execute("DELETE FROM kv WHERE key=?", (key,))
