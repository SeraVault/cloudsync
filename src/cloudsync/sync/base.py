"""Abstract base class for cloud storage providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class CloudStorageClient(ABC):
    """Interface every cloud storage provider must implement.

    The sync engine depends only on this contract — not on any concrete client.
    """

    # ------------------------------------------------------------------ #
    # Identity                                                             #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def get_user_email(self) -> str:
        """Return a display identifier for the authenticated account."""

    # ------------------------------------------------------------------ #
    # Folders / prefixes                                                   #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def get_or_create_folder(self, name: str, parent_id: str) -> str:
        """Return the provider ID for *name* under *parent_id*, creating it if absent.

        For flat-namespace providers (e.g. S3) this builds a key prefix rather
        than a real folder object.
        """

    # ------------------------------------------------------------------ #
    # Listing                                                              #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def list_files_recursive(self, folder_id: str, prefix: str = "") -> List[Tuple[str, Dict]]:
        """Return ``(relative_path, file_meta)`` pairs under *folder_id*.

        *file_meta* must contain at minimum:
          - ``id``            — provider-specific object identifier
          - ``name``          — file basename
          - ``modifiedTime``  — ISO-8601 UTC timestamp string
          - ``md5Checksum``   — hex MD5 (or equivalent checksum)
          - ``size``          — byte count as int or str
        """

    # ------------------------------------------------------------------ #
    # Upload / Download / Delete                                           #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def upload_file(
        self,
        local_path: Path,
        parent_id: str,
        existing_id: Optional[str] = None,
    ) -> Dict:
        """Upload *local_path* under *parent_id*.

        If *existing_id* is provided the upload should replace that object.
        Returns file_meta in the same shape as :meth:`list_files_recursive`.
        """

    @abstractmethod
    def download_file(self, file_id: str, dest_path: Path) -> None:
        """Download the object identified by *file_id* to *dest_path*."""

    @abstractmethod
    def trash_file(self, file_id: str) -> None:
        """Soft-delete the object.  Implementations that lack a trash should
        permanently delete instead."""

    # ------------------------------------------------------------------ #
    # Change tracking                                                      #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def get_start_page_token(self) -> str:
        """Return an opaque cursor representing the current remote state.

        Stored after each sync; passed back to :meth:`get_changes` next time.
        """

    @abstractmethod
    def get_changes(self, page_token: str) -> Tuple[List[Dict], str]:
        """Return ``(changes, new_page_token)`` since *page_token*.

        Each change dict must contain:
          - ``fileId``  — provider ID of the affected object
          - ``removed`` — ``True`` if the object was deleted/trashed
          - ``file``    — file_meta dict (may be absent when ``removed`` is True)
        """
