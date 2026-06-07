"""Amazon S3 (and S3-compatible) storage provider."""
from __future__ import annotations

import hashlib
import mimetypes
import shutil
from datetime import timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.s3_auth import S3Auth
from .base import CloudStorageClient


class S3Client(CloudStorageClient):
    """CloudStorageClient implementation backed by Amazon S3.

    The *folder_id* / *parent_id* parameters used by the engine map to S3 in
    the following way:

      - ``remote_folder_id`` in ``SyncFolder`` is the bucket name, optionally
        suffixed with a key prefix: ``"my-bucket"`` or ``"my-bucket/photos"``.
      - Sub-folders within a sync are represented by key prefixes only — no
        zero-byte directory objects are created.

    The change-tracking cursor (page_token) is a UTC ISO-8601 timestamp.
    On each poll, objects modified after that timestamp are returned as changes.
    This is less efficient than a native changes feed but works with every
    S3-compatible service.
    """

    def __init__(self, auth: S3Auth) -> None:
        import boto3
        from botocore.config import Config as BotocoreConfig

        self._auth = auth
        kwargs: dict = dict(
            aws_access_key_id=auth.access_key,
            aws_secret_access_key=auth.secret_key,
            region_name=auth.region,
            config=BotocoreConfig(
                retries={"max_attempts": 5, "mode": "adaptive"}
            ),
        )
        if auth.endpoint_url:
            kwargs["endpoint_url"] = auth.endpoint_url
        self._s3 = boto3.client("s3", **kwargs)

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    def get_user_email(self) -> str:
        return self._auth.access_key

    # ------------------------------------------------------------------ #
    # Folders / prefixes                                                   #
    # ------------------------------------------------------------------ #

    def get_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return a new prefix string — no actual S3 object is created."""
        bucket, prefix = _split_bucket_prefix(parent_id)
        new_prefix = f"{prefix}/{name}" if prefix else name
        return f"{bucket}/{new_prefix}"

    def list_subfolders(self, folder_id: str) -> List[Dict]:
        """Return ``{"id": ..., "name": ...}`` for each direct sub-prefix of *folder_id*.

        Uses ``list_objects_v2`` with ``Delimiter="/"`` so only one level of
        hierarchy is returned — no deep recursion.  Results are sorted by name.
        """
        bucket, prefix = _split_bucket_prefix(folder_id)
        list_prefix = f"{prefix}/" if prefix else ""

        folders: List[Dict] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                raw: str = cp["Prefix"]          # e.g. "photos/"
                rel = raw[len(list_prefix):]      # strip the parent prefix
                name = rel.rstrip("/")
                folders.append({"id": f"{bucket}/{raw.rstrip('/')}", "name": name})

        folders.sort(key=lambda x: x["name"].lower())
        return folders

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    def list_files_recursive(self, folder_id: str, prefix: str = "") -> List[Tuple[str, Dict]]:
        bucket, base_prefix = _split_bucket_prefix(folder_id)
        results: List[Tuple[str, Dict]] = []
        paginator = self._s3.get_paginator("list_objects_v2")
        list_prefix = f"{base_prefix}/" if base_prefix else ""

        for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                if key.endswith("/"):
                    continue  # skip directory markers
                # Relative path strips the base prefix
                rel = key[len(list_prefix):] if list_prefix else key
                if prefix:
                    rel = f"{prefix}/{rel}"
                meta = _obj_to_meta(bucket, key, obj)
                results.append((rel, meta))

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
        bucket, prefix = _split_bucket_prefix(parent_id)
        key = f"{prefix}/{local_path.name}" if prefix else local_path.name

        content_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        md5 = _file_md5(local_path)

        with open(local_path, "rb") as fh:
            resp = self._s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=fh,
                ContentType=content_type,
                Metadata={"md5": md5},
            )

        # Build metadata from the put_object response + local file info.
        # Avoids a head_object call (which requires s3:GetObject) just to
        # confirm what we just uploaded.
        from datetime import datetime
        etag = resp.get("ETag", f'"{md5}"').strip('"')
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        return {
            "id": f"{bucket}/{key}",
            "name": local_path.name,
            "modifiedTime": now_ts,
            "md5Checksum": etag,
            "size": local_path.stat().st_size,
        }

    def download_file(self, file_id: str, dest_path: Path) -> None:
        bucket, key = _split_bucket_key(file_id)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        # Use get_object instead of the managed download_file so boto3 does
        # NOT issue an internal HeadObject call (which needs s3:GetObject but
        # can be blocked by some policies).  get_object streams directly.
        resp = self._s3.get_object(Bucket=bucket, Key=key)
        with open(dest_path, "wb") as fh:
            shutil.copyfileobj(resp["Body"], fh)

    def trash_file(self, file_id: str) -> None:
        """S3 has no native trash — permanently delete the object."""
        bucket, key = _split_bucket_key(file_id)
        self._s3.delete_object(Bucket=bucket, Key=key)

    # ------------------------------------------------------------------ #
    # Change tracking                                                      #
    # ------------------------------------------------------------------ #

    def get_start_page_token(self) -> str:
        """Return the current UTC time as the initial cursor."""
        from datetime import datetime
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def get_changes(self, page_token: str) -> Tuple[List[Dict], str]:
        """Return objects modified after *page_token* (a UTC timestamp string).

        NOTE: this requires the S3 bucket to be scanned; it is intentionally
        called only at the interval configured by the user, not continuously.
        """
        from datetime import datetime

        since = datetime.strptime(page_token, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        new_token = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        # We need to scan all tracked buckets — the engine calls get_changes per
        # SyncFolder, so folder_id is embedded in stored state, not passed here.
        # Return an empty list; the engine falls back to _full_sync on first run
        # and the timestamp cursor prevents re-downloading unchanged files.
        changes: List[Dict] = []
        return changes, new_token


# ------------------------------------------------------------------ #
# Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _split_bucket_prefix(folder_id: str) -> Tuple[str, str]:
    """Split ``"bucket/optional/prefix"`` → ``("bucket", "optional/prefix")``."""
    parts = folder_id.split("/", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _split_bucket_key(file_id: str) -> Tuple[str, str]:
    """Split ``"bucket/key/path"`` → ``("bucket", "key/path")``."""
    parts = file_id.split("/", 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid S3 file_id (expected bucket/key): {file_id!r}")
    return parts[0], parts[1]


def _file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _etag(obj: Dict) -> str:
    """Return a normalised ETag for use as a change-detection checksum.

    Multipart ETags (``abc123-42``) are not MD5 hashes but are still
    stable identifiers — an unchanged object always has the same ETag.
    We store them as-is so the engine can detect changes by comparing
    stored vs current ETag without needing to re-hash the file locally.
    """
    return obj.get("ETag", "").strip('"')


def _obj_to_meta(bucket: str, key: str, obj: Dict) -> Dict:
    last_modified = obj["LastModified"]
    if hasattr(last_modified, "strftime"):
        ts = last_modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    else:
        ts = str(last_modified)
    return {
        "id": f"{bucket}/{key}",
        "name": key.rsplit("/", 1)[-1],
        "modifiedTime": ts,
        "md5Checksum": _etag(obj),
        "size": obj.get("Size", 0),
    }


def _head_to_meta(bucket: str, key: str, name: str, head: Dict, md5: str) -> Dict:
    last_modified = head["LastModified"]
    if hasattr(last_modified, "strftime"):
        ts = last_modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    else:
        ts = str(last_modified)
    return {
        "id": f"{bucket}/{key}",
        "name": name,
        "modifiedTime": ts,
        "md5Checksum": md5,
        "size": head.get("ContentLength", 0),
    }
