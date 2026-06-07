"""Microsoft OneDrive storage provider."""
from __future__ import annotations

import logging
import mimetypes
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from ..core.onedrive_auth import OneDriveAuth
from .base import CloudStorageClient

log = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 4

# OneDrive/Microsoft 365 types that cannot be downloaded as binary files.
# Most Office formats (docx, xlsx, pptx) ARE downloadable — only notebook
# types need special handling.
ONEDRIVE_STUB_TYPES = {
    "application/msonenote": (
        ".onenote",
        "https://onedrive.live.com/edit.aspx?resid={id}",
    ),
}

# Package types (item["package"]["type"]) that signal a non-downloadable item
ONEDRIVE_STUB_PACKAGES = {
    "oneNote": (
        ".onenote",
        "https://onedrive.live.com/edit.aspx?resid={id}",
    ),
}

ONEDRIVE_STUB_EXTENSIONS = frozenset(
    ext
    for ext, _ in (
        list(ONEDRIVE_STUB_TYPES.values())
        + list(ONEDRIVE_STUB_PACKAGES.values())
    )
)


def write_onedrive_link(
    dest_path: Path, item_id: str, name: str, url_template: str
) -> None:
    """Write a OneDrive link stub file (small JSON, same shape as gdoc stubs)."""
    import json as _json
    url = url_template.format(id=item_id)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(
        _json.dumps({"url": url, "item_id": item_id, "name": name}, indent=2)
    )


class OneDriveClient(CloudStorageClient):
    """CloudStorageClient implementation backed by the Microsoft Graph API.

    *folder_id* / *parent_id* map to OneDrive item IDs.  The special value
    ``"root"`` resolves to the user's OneDrive root.

    Change tracking uses the Graph delta API, which returns an opaque
    ``@odata.deltaLink`` token stored between syncs.
    """

    def __init__(self, auth: OneDriveAuth) -> None:
        self._auth = auth
        # Serialises token refresh so concurrent callers don't race.
        self._token_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Internal request helpers                                            #
    # ------------------------------------------------------------------ #

    def _auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self._auth.access_token}"}

    def _refresh(self) -> None:
        with self._token_lock:
            self._auth.refresh_if_needed()

    def _request(
        self,
        method: str,
        url: str,
        stream: bool = False,
        **kwargs,
    ) -> requests.Response:
        """Single entry-point for all Graph API calls with retry/backoff."""
        self._refresh()
        headers = {**self._auth_header(), **kwargs.pop("headers", {})}
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.request(
                    method,
                    url,
                    headers=headers,
                    stream=stream,
                    timeout=60,
                    **kwargs,
                )
            except requests.ConnectionError as exc:
                if attempt < _MAX_RETRIES - 1:
                    log.warning("Network error (attempt %d): %s", attempt, exc)
                    time.sleep(2 ** attempt)
                    self._refresh()
                    headers = {**self._auth_header(), **kwargs.get("headers", {})}
                    continue
                raise

            if resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                return resp

            if attempt < _MAX_RETRIES - 1:
                retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                log.warning(
                    "HTTP %d from Graph API — retrying in %ds (attempt %d)",
                    resp.status_code,
                    retry_after,
                    attempt,
                )
                time.sleep(retry_after)
                self._refresh()
                headers = {**self._auth_header(), **kwargs.get("headers", {})}
            else:
                resp.raise_for_status()

        # Should be unreachable, but satisfies type checker
        raise RuntimeError("Exhausted retries")

    def _get(self, path: str, **kwargs) -> dict:
        resp = self._request("GET", f"{_GRAPH}{path}", **kwargs)
        return resp.json()

    def _post(self, path: str, **kwargs) -> dict:
        resp = self._request("POST", f"{_GRAPH}{path}", **kwargs)
        return resp.json()

    def _patch(self, path: str, **kwargs) -> dict:
        resp = self._request("PATCH", f"{_GRAPH}{path}", **kwargs)
        return resp.json()

    def _put_bytes(self, path: str, data: bytes, content_type: str) -> dict:
        resp = self._request(
            "PUT",
            f"{_GRAPH}{path}",
            headers={"Content-Type": content_type},
            data=data,
        )
        return resp.json()

    def _delete(self, path: str) -> None:
        resp = self._request("DELETE", f"{_GRAPH}{path}")
        # 204 No Content is the expected success response; already checked
        # by _request for non-retry statuses.
        if resp.status_code not in (200, 204):
            resp.raise_for_status()

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    def get_user_email(self) -> str:
        try:
            data = self._get("/me?$select=mail,userPrincipalName")
            return data.get("mail") or data.get("userPrincipalName", "")
        except Exception:
            return self._auth.user_email

    # ------------------------------------------------------------------ #
    # Folders                                                              #
    # ------------------------------------------------------------------ #

    def get_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return the OneDrive item ID for *name* under *parent_id*."""
        base = _item_base(parent_id)
        try:
            data = self._get(
                f"{base}/children"
                f"?$filter=name eq '{name}' and folder ne null"
                f"&$select=id,name"
            )
            items = data.get("value", [])
            if items:
                return items[0]["id"]
        except Exception:
            pass

        result = self._post(
            f"{base}/children",
            json={
                "name": name,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "rename",
            },
        )
        return result["id"]

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    def _fetch_all_items(self, folder_id: str) -> List[dict]:
        """Fetch every item under *folder_id* in one paginated delta sweep."""
        base = _item_base(folder_id)
        url: Optional[str] = (
            f"{_GRAPH}{base}/delta"
            "?$select=id,name,file,folder,parentReference"
            ",lastModifiedDateTime,size"
        )
        items: List[dict] = []
        while url:
            self._refresh()
            resp = requests.get(url, headers=self._auth_header(), timeout=60)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            if "@odata.deltaLink" in data:
                break
        return items

    def list_files_recursive(
        self, folder_id: str, prefix: str = ""
    ) -> List[Tuple[str, Dict]]:
        """Return ``(relative_path, file_meta)`` pairs via a flat delta sweep."""
        all_items = self._fetch_all_items(folder_id)

        root_id = None
        for item in all_items:
            if (
                item.get("name") == "root"
                or not item.get("parentReference", {}).get("id")
            ):
                root_id = item["id"]
                break
        if root_id is None:
            base = _item_base(folder_id).lstrip("/")
            root_id = self._get(f"/{base}?$select=id")["id"]

        children: Dict[str, List[dict]] = {}
        for item in all_items:
            pid = item.get("parentReference", {}).get("id")
            if pid:
                children.setdefault(pid, []).append(item)

        results: List[Tuple[str, Dict]] = []

        def _walk(node_id: str, cur: str) -> None:
            for item in children.get(node_id, []):
                name = item["name"]
                iid = item["id"]
                pkg_type = item.get("package", {}).get("type", "")

                if pkg_type in ONEDRIVE_STUB_PACKAGES:
                    ext, tmpl = ONEDRIVE_STUB_PACKAGES[pkg_type]
                    stub = name + ext
                    rel = f"{cur}/{stub}" if cur else stub
                    results.append((rel, {
                        **_item_to_meta(item),
                        "name": stub,
                        "is_onedrive_stub": True,
                        "stub_url_template": tmpl,
                    }))
                elif "folder" in item:
                    rel = f"{cur}/{name}" if cur else name
                    _walk(iid, rel)
                elif "file" in item:
                    mime = item.get("file", {}).get("mimeType", "")
                    if mime in ONEDRIVE_STUB_TYPES:
                        ext, tmpl = ONEDRIVE_STUB_TYPES[mime]
                        stub = name + ext
                        rel = f"{cur}/{stub}" if cur else stub
                        results.append((rel, {
                            **_item_to_meta(item),
                            "name": stub,
                            "is_onedrive_stub": True,
                            "stub_url_template": tmpl,
                        }))
                    else:
                        rel = f"{cur}/{name}" if cur else name
                        results.append((rel, _item_to_meta(item)))

        _walk(root_id, prefix)
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
        if local_path.suffix in ONEDRIVE_STUB_EXTENSIONS:
            raise ValueError(
                f"Cannot upload OneDrive link stub: {local_path.name}"
            )
        content_type = (
            mimetypes.guess_type(str(local_path))[0]
            or "application/octet-stream"
        )
        data = local_path.read_bytes()

        if len(data) <= 4 * 1024 * 1024:
            if existing_id:
                path = f"/me/drive/items/{existing_id}/content"
            else:
                base = _item_base(parent_id)
                path = f"{base}:/{local_path.name}:/content"
            result = self._put_bytes(path, data, content_type)
        else:
            result = self._upload_large(
                local_path, parent_id, existing_id, data, content_type
            )

        return _item_to_meta(result)

    def _upload_large(
        self,
        local_path: Path,
        parent_id: str,
        existing_id: Optional[str],
        data: bytes,
        content_type: str,
    ) -> dict:
        self._refresh()

        if existing_id:
            session_path = (
                f"{_GRAPH}/me/drive/items/{existing_id}/createUploadSession"
            )
        else:
            base = _item_base(parent_id)
            session_path = (
                f"{_GRAPH}{base}:/{local_path.name}:/createUploadSession"
            )

        session_resp = requests.post(
            session_path,
            headers=self._auth_header(),
            json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            timeout=30,
        )
        session_resp.raise_for_status()
        upload_url = session_resp.json()["uploadUrl"]

        # 10 MB chunks — must be a multiple of 320 KiB
        chunk_size = 10 * 1024 * 1024
        total = len(data)
        offset = 0
        result: dict = {}

        while offset < total:
            end = min(offset + chunk_size, total)
            chunk = data[offset:end]
            resp = requests.put(
                upload_url,
                headers={
                    "Content-Range": f"bytes {offset}-{end - 1}/{total}",
                    "Content-Type": content_type,
                },
                data=chunk,
                timeout=120,
            )
            resp.raise_for_status()
            if resp.status_code in (200, 201):
                result = resp.json()
            offset = end

        return result

    def download_file(self, file_id: str, dest_path: Path) -> None:
        resp = self._request(
            "GET",
            f"{_GRAPH}/me/drive/items/{file_id}/content",
            allow_redirects=True,
            stream=True,
        )
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)

    def trash_file(self, file_id: str) -> None:
        self._delete(f"/me/drive/items/{file_id}")

    # ------------------------------------------------------------------ #
    # Change tracking (Graph delta)                                        #
    # ------------------------------------------------------------------ #

    def get_start_page_token(self) -> str:
        """Return a delta link token representing the current drive state."""
        data = self._get("/me/drive/root/delta?$select=id&token=latest")
        return data.get("@odata.deltaLink", "")

    def get_changes(
        self, page_token: str
    ) -> Tuple[List[Dict], str]:
        """Poll the Graph delta feed since *page_token* (a deltaLink URL)."""
        changes: List[Dict] = []
        url: Optional[str] = page_token

        while url:
            self._refresh()
            resp = requests.get(
                url, headers=self._auth_header(), timeout=60
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("value", []):
                removed = "deleted" in item
                file_meta = (
                    _item_to_meta(item)
                    if not removed and "file" in item
                    else None
                )
                changes.append({
                    "fileId": item["id"],
                    "removed": removed,
                    "file": file_meta,
                })

            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")

            if next_link:
                url = next_link
            else:
                return changes, delta_link or page_token

        return changes, page_token


# ------------------------------------------------------------------ #
# Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _item_base(folder_id: str) -> str:
    if folder_id == "root":
        return "/me/drive/root"
    return f"/me/drive/items/{folder_id}"


def _item_to_meta(item: dict) -> dict:
    file_info = item.get("file", {})
    hashes = file_info.get("hashes", {})
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "modifiedTime": item.get("lastModifiedDateTime", ""),
        "md5Checksum": (
            hashes.get("quickXorHash") or hashes.get("md5Hash", "")
        ),
        "size": item.get("size", 0),
    }
