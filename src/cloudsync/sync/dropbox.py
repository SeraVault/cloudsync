"""Dropbox storage provider using the official Dropbox SDK."""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.dropbox_auth import DropboxAuth
from .base import CloudStorageClient


class DropboxClient(CloudStorageClient):
    """CloudStorageClient implementation backed by the Dropbox API v2.

    *folder_id* / *parent_id* map to Dropbox path strings:
      - ``""`` or ``"/"`` → the user's Dropbox root
      - ``"/Photos/Vacation"`` → a specific subfolder

    Change tracking uses the Dropbox list_folder cursor — a native delta
    feed that returns only changed entries since the last poll, making
    incremental syncs very efficient.
    """

    def __init__(self, auth: DropboxAuth) -> None:
        self._auth = auth

    def _dbx(self):
        return self._auth.get_client()

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    def get_user_email(self) -> str:
        if self._auth.user_email:
            return self._auth.user_email
        try:
            account = self._dbx().users_get_current_account()
            return account.email
        except Exception:
            return self._auth.account_id or "Dropbox"

    # ------------------------------------------------------------------ #
    # Folders                                                              #
    # ------------------------------------------------------------------ #

    def get_or_create_folder(self, name: str, parent_id: str) -> str:
        path = _join(parent_id, name)
        import dropbox.exceptions
        try:
            self._dbx().files_create_folder_v2(path)
        except dropbox.exceptions.ApiError as e:
            # Ignore "already exists" errors
            if not str(e).startswith("ApiError"):
                raise
            if "path/conflict/folder" not in str(e) and "path/conflict/file" not in str(e):
                raise
        return path

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    def list_subfolders(self, path: str) -> List[Dict]:
        """Return ``{"id": ..., "name": ...}`` for each direct subfolder of *path*."""
        import dropbox.files as dbf
        norm_path = "" if path in ("", "/", "root") else path
        folders: List[Dict] = []
        res = self._dbx().files_list_folder(norm_path, recursive=False, include_deleted=False)
        while True:
            for entry in res.entries:
                if isinstance(entry, dbf.FolderMetadata):
                    folders.append({"id": entry.path_display, "name": entry.name})
            if not res.has_more:
                break
            res = self._dbx().files_list_folder_continue(res.cursor)
        folders.sort(key=lambda x: x["name"].lower())
        return folders

    def list_files_recursive(self, folder_id: str, prefix: str = "") -> List[Tuple[str, Dict]]:
        """Return all files under *folder_id* in one paginated sweep."""
        path = "" if folder_id in ("", "/", "root") else folder_id
        results: List[Tuple[str, Dict]] = []

        dbx = self._dbx()
        res = dbx.files_list_folder(path, recursive=True, include_deleted=False)

        while True:
            for entry in res.entries:
                import dropbox.files as dbf
                if isinstance(entry, dbf.FileMetadata):
                    rel = entry.path_display.lstrip("/")
                    if path:
                        # Strip the base path prefix to get relative path
                        base = path.lstrip("/")
                        if rel.startswith(base + "/"):
                            rel = rel[len(base) + 1:]
                    if prefix:
                        rel = f"{prefix}/{rel}"
                    results.append((rel, _entry_to_meta(entry)))

            if not res.has_more:
                break
            res = dbx.files_list_folder_continue(res.cursor)

        return results

    # ------------------------------------------------------------------ #
    # Upload / Download / Delete                                           #
    # ------------------------------------------------------------------ #

    def upload_file(
        self,
        local_path: Path,
        parent_id: str,
        existing_id: Optional[str] = None,
    ) -> Dict:
        if not local_path.exists():
            raise FileNotFoundError(f"File vanished before upload: {local_path}")

        dest_path = _join(parent_id, local_path.name)
        import dropbox.files as dbf

        data = local_path.read_bytes()
        # Use upload session for files > 150 MB
        if len(data) > 150 * 1024 * 1024:
            meta = self._upload_large(dest_path, data)
        else:
            meta = self._dbx().files_upload(
                data,
                dest_path,
                mode=dbf.WriteMode.overwrite,
                mute=True,
            )
        return _entry_to_meta(meta)

    def _upload_large(self, dest_path: str, data: bytes) -> object:
        import dropbox.files as dbf
        CHUNK = 150 * 1024 * 1024
        dbx = self._dbx()

        session = dbx.files_upload_session_start(data[:CHUNK])
        cursor = dbf.UploadSessionCursor(session.session_id, offset=CHUNK)
        offset = CHUNK

        while offset < len(data):
            chunk = data[offset: offset + CHUNK]
            end = offset + len(chunk)
            if end >= len(data):
                commit = dbf.CommitInfo(path=dest_path, mode=dbf.WriteMode.overwrite)
                meta = dbx.files_upload_session_finish(chunk, cursor, commit)
                return meta
            dbx.files_upload_session_append_v2(chunk, cursor)
            cursor = dbf.UploadSessionCursor(cursor.session_id, offset=end)
            offset = end

        # Empty file edge case
        commit = dbf.CommitInfo(path=dest_path, mode=dbf.WriteMode.overwrite)
        return dbx.files_upload_session_finish(b"", cursor, commit)

    def download_file(self, file_id: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        _, response = self._dbx().files_download(file_id)
        if response is None:
            raise RuntimeError(f"Dropbox download returned no response for {file_id}")
        try:
            with open(dest_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=65536):
                    fh.write(chunk)
        finally:
            response.close()

    def trash_file(self, file_id: str) -> None:
        self._dbx().files_delete_v2(file_id)

    # ------------------------------------------------------------------ #
    # Change tracking (Dropbox list_folder cursor)                         #
    # ------------------------------------------------------------------ #

    def get_start_page_token(self) -> str:
        """Return the current list_folder cursor for the entire Dropbox."""
        result = self._dbx().files_list_folder_get_latest_cursor(
            "", recursive=True, include_deleted=True
        )
        return result.cursor

    def get_changes(self, page_token: str) -> Tuple[List[Dict], str]:
        """Return changes since *page_token* using the Dropbox delta feed."""
        import dropbox.files as dbf

        changes: List[Dict] = []
        cursor = page_token

        while True:
            result = self._dbx().files_list_folder_continue(cursor)

            for entry in result.entries:
                removed = isinstance(entry, dbf.DeletedMetadata)
                file_meta = None if removed else _entry_to_meta(entry)
                changes.append({
                    "fileId": entry.path_lower,
                    "removed": removed,
                    "file": file_meta,
                })

            cursor = result.cursor
            if not result.has_more:
                break

        return changes, cursor


# ------------------------------------------------------------------ #
# Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _join(parent: str, name: str) -> str:
    """Build a Dropbox path from parent + name."""
    if not parent or parent in ("/", "root", ""):
        return f"/{name}"
    return f"{parent.rstrip('/')}/{name}"


def _entry_to_meta(entry) -> Dict:
    import dropbox.files as dbf
    if isinstance(entry, dbf.FileMetadata):
        ts = entry.server_modified.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        return {
            "id": entry.path_lower,
            "name": entry.name,
            "modifiedTime": ts,
            "md5Checksum": entry.content_hash or "",
            "size": entry.size,
        }
    # Folder or deleted — return minimal meta
    return {
        "id": entry.path_lower,
        "name": entry.name,
        "modifiedTime": "",
        "md5Checksum": "",
        "size": 0,
    }
