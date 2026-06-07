"""Google Drive API wrapper."""
from __future__ import annotations

import io
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from ..core.auth import GoogleAuth
from .base import CloudStorageClient

# Pass to every .execute() call so the client library retries 429/5xx
# with exponential backoff automatically.
_NUM_RETRIES = 5

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id,name,mimeType,modifiedTime,md5Checksum,size,parents,trashed"

# Maps Google Workspace MIME types to the local stub extension and the URL
# template used to open the file in a browser.
GDOC_STUBS = {
    "application/vnd.google-apps.document":     (".gdoc",    "https://docs.google.com/document/d/{id}/edit"),
    "application/vnd.google-apps.spreadsheet":  (".gsheet",  "https://docs.google.com/spreadsheets/d/{id}/edit"),
    "application/vnd.google-apps.presentation": (".gslides", "https://docs.google.com/presentation/d/{id}/edit"),
    "application/vnd.google-apps.drawing":      (".gdraw",   "https://docs.google.com/drawings/d/{id}/edit"),
    "application/vnd.google-apps.form":         (".gform",   "https://docs.google.com/forms/d/{id}/edit"),
    "application/vnd.google-apps.script":       (".gscript", "https://script.google.com/d/{id}/edit"),
    "application/vnd.google-apps.site":         (".gsite",   "https://sites.google.com/d/{id}/edit"),
    "application/vnd.google-apps.map":          (".gmap",    "https://www.google.com/maps/d/edit?mid={id}"),
}

# Extensions that are Google Workspace link stubs — never upload these back.
GDOC_STUB_EXTENSIONS = frozenset(ext for ext, _ in GDOC_STUBS.values())

# Mimes we cannot produce a useful stub for — silently skip.
GOOGLE_NATIVE_MIMES = frozenset(GDOC_STUBS) | {
    "application/vnd.google-apps.shortcut",
    "application/vnd.google-apps.folder",
}


def _escape_q(value: str) -> str:
    """Escape single quotes in a Drive API query string value."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveClient(CloudStorageClient):
    """Thin wrapper around the Drive v3 REST API."""

    def __init__(self, auth: GoogleAuth) -> None:
        self._auth = auth
        self._service = build("drive", "v3", credentials=auth.credentials)

    # ------------------------------------------------------------------ #
    # Folders                                                              #
    # ------------------------------------------------------------------ #

    def get_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return the Drive folder ID for *name* under *parent_id*, creating it if absent."""
        q = (
            f"name = '{_escape_q(name)}' and mimeType = '{FOLDER_MIME}' "
            f"and '{_escape_q(parent_id)}' in parents and trashed = false"
        )
        result = self._service.files().list(q=q, fields="files(id)").execute(num_retries=_NUM_RETRIES)
        files = result.get("files", [])
        if files:
            return files[0]["id"]

        meta = {
            "name": name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        }
        f = self._service.files().create(body=meta, fields="id").execute(num_retries=_NUM_RETRIES)
        return f["id"]

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    def list_subfolders(self, folder_id: str) -> List[Dict]:
        """Return ``{"id": ..., "name": ...}`` for each direct subfolder of *folder_id*."""
        q = (
            f"'{_escape_q(folder_id)}' in parents "
            f"and mimeType = '{FOLDER_MIME}' "
            f"and trashed = false"
        )
        resp = self._service.files().list(
            q=q,
            fields="files(id,name)",
            orderBy="name",
            pageSize=500,
        ).execute(num_retries=_NUM_RETRIES)
        return [{"id": f["id"], "name": f["name"]} for f in resp.get("files", [])]

    def list_files(self, folder_id: str) -> List[Dict]:
        """Return all non-trashed items directly under *folder_id*.

        Kept for compatibility with get_or_create_folder; prefer
        list_files_recursive for full-tree enumeration.
        """
        items: List[Dict] = []
        page_token: Optional[str] = None
        q = f"'{_escape_q(folder_id)}' in parents and trashed = false"
        while True:
            kwargs: Dict = dict(
                q=q,
                fields=f"nextPageToken, files({FILE_FIELDS})",
                pageSize=1000,
            )
            if page_token:
                kwargs["pageToken"] = page_token
            resp = self._service.files().list(**kwargs).execute(num_retries=_NUM_RETRIES)
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items

    def _resolve_id(self, folder_id: str) -> str:
        """Resolve ``"root"`` to its real Drive item ID."""
        if folder_id != "root":
            return folder_id
        resp = self._service.files().get(fileId="root", fields="id").execute(num_retries=_NUM_RETRIES)
        return resp["id"]

    def _fetch_all_files(self, root_id: str) -> List[Dict]:
        """Fetch every non-trashed file and folder on the drive in one
        paginated sweep — O(total_files / 1000) API calls instead of
        O(total_folders).  Returns raw items including folders.
        """
        items: List[Dict] = []
        page_token: Optional[str] = None
        while True:
            kwargs: Dict = dict(
                q="trashed = false",
                fields=f"nextPageToken, files({FILE_FIELDS})",
                pageSize=1000,
                spaces="drive",
            )
            if page_token:
                kwargs["pageToken"] = page_token
            resp = self._service.files().list(**kwargs).execute(num_retries=_NUM_RETRIES)
            items.extend(resp.get("files", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items

    def list_files_recursive(self, folder_id: str, prefix: str = "") -> List[Tuple[str, Dict]]:
        """Return ``(relative_path, file_meta)`` pairs for everything under
        *folder_id*, using a single flat API sweep rather than per-folder calls.

        Google Workspace native files (Docs, Sheets, etc.) are included with
        their stub extension appended to the name (e.g. ``Report.gdoc``).
        The meta dict carries ``"is_gdoc_stub": True`` so the engine writes a
        link file instead of downloading binary content.
        """
        # Resolve "root" alias to real ID so parent lookups work
        real_root = self._resolve_id(folder_id)

        all_items = self._fetch_all_files(real_root)

        # Build id→item and id→children maps from the flat list
        by_id: Dict[str, Dict] = {item["id"]: item for item in all_items}
        children: Dict[str, List[str]] = {}
        for item in all_items:
            for parent in item.get("parents", []):
                children.setdefault(parent, []).append(item["id"])

        # Walk the tree from the real root ID, building relative paths
        results: List[Tuple[str, Dict]] = []

        def _walk(node_id: str, current_prefix: str) -> None:
            for child_id in children.get(node_id, []):
                item = by_id.get(child_id)
                if not item:
                    continue
                mime = item["mimeType"]
                name = item["name"]
                if mime == FOLDER_MIME:
                    rel = f"{current_prefix}/{name}" if current_prefix else name
                    _walk(child_id, rel)
                elif mime in GDOC_STUBS:
                    ext, _ = GDOC_STUBS[mime]
                    stub_name = name + ext
                    rel = f"{current_prefix}/{stub_name}" if current_prefix else stub_name
                    results.append((rel, {**item, "name": stub_name, "is_gdoc_stub": True}))
                elif mime not in GOOGLE_NATIVE_MIMES:
                    rel = f"{current_prefix}/{name}" if current_prefix else name
                    results.append((rel, item))

        _walk(real_root, prefix)
        return results

    # ------------------------------------------------------------------ #
    # Upload / Download                                                    #
    # ------------------------------------------------------------------ #

    def upload_file(self, local_path: Path, parent_id: str, existing_id: Optional[str] = None) -> Dict:
        """Upload or update a file.  Returns the Drive file metadata."""
        if not local_path.exists():
            raise FileNotFoundError(f"File vanished before upload: {local_path}")

        mime = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        # Use resumable uploads only for files >5 MB; simple uploads are more
        # reliable for small files and avoid a separate session-initiation round-trip.
        resumable = local_path.stat().st_size > 5 * 1024 * 1024
        media = MediaFileUpload(str(local_path), mimetype=mime, resumable=resumable)
        if existing_id:
            file_meta = self._service.files().update(
                fileId=existing_id, media_body=media, fields=FILE_FIELDS
            ).execute(num_retries=_NUM_RETRIES)
        else:
            meta = {"name": local_path.name, "parents": [parent_id]}
            file_meta = self._service.files().create(
                body=meta, media_body=media, fields=FILE_FIELDS
            ).execute(num_retries=_NUM_RETRIES)
        return file_meta

    def download_file(self, file_id: str, dest_path: Path) -> None:
        """Download a Drive file to *dest_path*."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(fileId=file_id)
        with open(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

    def delete_file(self, file_id: str) -> None:
        """Permanently delete a Drive file."""
        self._service.files().delete(fileId=file_id).execute(num_retries=_NUM_RETRIES)

    def trash_file(self, file_id: str) -> None:
        """Move a Drive file to trash instead of permanent deletion."""
        self._service.files().update(fileId=file_id, body={"trashed": True}).execute(num_retries=_NUM_RETRIES)

    # ------------------------------------------------------------------ #
    # Changes API                                                          #
    # ------------------------------------------------------------------ #

    def get_start_page_token(self) -> str:
        """Return the current changes start page token."""
        resp = self._service.changes().getStartPageToken().execute(num_retries=_NUM_RETRIES)
        return resp["startPageToken"]

    def get_changes(self, page_token: str) -> Tuple[List[Dict], str]:
        """Return ``(changes_list, new_page_token)`` since *page_token*.

        Each change dict has ``fileId``, ``removed``, and optionally ``file``.
        """
        changes: List[Dict] = []
        while True:
            resp = (
                self._service.changes()
                .list(
                    pageToken=page_token,
                    fields=f"nextPageToken,newStartPageToken,changes(fileId,removed,file({FILE_FIELDS}))",
                    spaces="drive",
                    includeRemoved=True,
                )
                .execute(num_retries=_NUM_RETRIES)
            )
            changes.extend(resp.get("changes", []))
            if "nextPageToken" in resp:
                page_token = resp["nextPageToken"]
            else:
                page_token = resp.get("newStartPageToken", page_token)
                break
        return changes, page_token

    # ------------------------------------------------------------------ #
    # User info                                                            #
    # ------------------------------------------------------------------ #

    def get_user_email(self) -> str:
        about = self._service.about().get(fields="user").execute(num_retries=_NUM_RETRIES)
        return about.get("user", {}).get("emailAddress", "")


def write_gdoc_link(dest_path: Path, file_id: str, mime_type: str) -> None:
    """Write a Google Workspace link stub file to *dest_path*.

    The stub is a small JSON file (same format as the Windows/macOS Google
    Drive client) that records the document URL and ID.  On Linux, associating
    the mimetype ``application/x-gdoc`` with a browser or xdg-open handler
    lets the user open the document directly from a file manager.
    """
    import json as _json
    stub_info = GDOC_STUBS.get(mime_type)
    if stub_info is None:
        return
    _, url_template = stub_info
    url = url_template.format(id=file_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(
        _json.dumps({"url": url, "doc_id": file_id, "mime_type": mime_type}, indent=2)
    )
