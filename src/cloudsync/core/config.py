"""Configuration management — stored as JSON in ~/.config/cloudsync/."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "cloudsync"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "cloudsync"

CONFIG_DIR = DEFAULT_CONFIG_DIR
CONFIG_FILE = DEFAULT_CONFIG_FILE
DATA_DIR = DEFAULT_DATA_DIR
STATE_DB = DATA_DIR / "state.db"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"

CONFLICT_OPTIONS = ["keep_both", "local_wins", "remote_wins"]
CONFLICT_LABELS = ["Keep both copies", "Local copy wins", "Remote copy wins"]
INTERVAL_OPTIONS = [30, 60, 300, 900]  # seconds
INTERVAL_LABELS = ["30 seconds", "1 minute", "5 minutes", "15 minutes"]
PROVIDER_OPTIONS = ["gdrive", "s3", "dropbox"]


def set_config_file(path: str | Path) -> Path:
    """Point runtime config paths at a specific config file.

    When a non-default config file is used, sidecar credentials and state are
    stored next to it so headless deployments can remain self-contained.
    """
    global CONFIG_DIR, CONFIG_FILE, DATA_DIR, STATE_DB, CREDENTIALS_FILE, TOKEN_FILE

    config_path = Path(path).expanduser().resolve(strict=False)
    CONFIG_FILE = config_path
    CONFIG_DIR = config_path.parent

    if config_path == DEFAULT_CONFIG_FILE.resolve(strict=False):
        DATA_DIR = DEFAULT_DATA_DIR
    else:
        DATA_DIR = CONFIG_DIR / "data"

    STATE_DB = DATA_DIR / "state.db"
    CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
    TOKEN_FILE = CONFIG_DIR / "token.json"
    return CONFIG_FILE


def resolve_portal_path(path: str) -> str:
    """Resolve XDG document-portal FUSE paths to the real host path.

    GTK4 FileDialog in a flatpak sandbox returns paths under
    ``/run/user/<uid>/doc/<hash>/<name>``.  The real path is stored in the
    ``xattr::document-portal.host-path`` extended attribute on the FUSE entry.
    Falls back to the original path if the xattr is absent.
    """
    import re
    import os
    if not re.match(r'^/run/user/\d+/doc/', path):
        return path
    try:
        host = os.getxattr(path, "user.document-portal.host-path")
        if host:
            return host.decode() if isinstance(host, bytes) else host
    except (OSError, AttributeError):
        pass
    return path


@dataclass
class ProviderAccount:
    """A connected provider account (one entry per sign-in)."""
    provider: str          # "gdrive" | "s3" | "onedrive"
    display_name: str = "" # e.g. user@example.com or AWS access key


@dataclass
class SyncFolder:
    local_path: str
    remote_folder_id: str = "root"
    remote_folder_name: str = "Cloud Root"
    provider: str = "gdrive"
    enabled: bool = True
    # 0 means "use the global default from Config.sync_interval_seconds"
    sync_interval_seconds: int = 0
    # Empty string means "inherit the global default from Config.conflict_resolution"
    conflict_resolution: str = ""

    def effective_interval(self, global_default: int) -> int:
        return self.sync_interval_seconds if self.sync_interval_seconds > 0 else global_default


@dataclass
class Config:
    sync_folders: List[SyncFolder] = field(default_factory=list)
    # Ordered list of connected provider accounts.  The engine starts one
    # SyncEngine instance per unique provider that has at least one folder.
    connected_providers: List[ProviderAccount] = field(default_factory=list)
    sync_interval_seconds: int = 60
    notifications_enabled: bool = True
    start_on_login: bool = False
    conflict_resolution: str = "keep_both"

    # ------------------------------------------------------------------ #
    # Convenience                                                          #
    # ------------------------------------------------------------------ #

    def providers_in_use(self) -> List[str]:
        """Return unique provider IDs that have at least one sync folder."""
        seen: List[str] = []
        for sf in self.sync_folders:
            if sf.provider not in seen:
                seen.append(sf.provider)
        return seen

    # ------------------------------------------------------------------ #
    # Persistence                                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
            _sf_fields = {
                f.name for f in SyncFolder.__dataclass_fields__.values()
            }
            folders = [
                SyncFolder(**{k: v for k, v in sf.items() if k in _sf_fields})
                for sf in data.pop("sync_folders", [])
            ]
            accounts = [
                ProviderAccount(**pa)
                for pa in data.pop("connected_providers", [])
            ]
            # Migrate legacy single-provider configs that stored a top-level
            # "provider" key but no connected_providers list.
            legacy_provider = data.pop("provider", None)
            if legacy_provider and not accounts:
                accounts = [ProviderAccount(provider=legacy_provider)]
            return cls(
                sync_folders=folders, connected_providers=accounts, **data
            )
        except Exception:
            backup = CONFIG_FILE.with_suffix(".json.bak")
            try:
                CONFIG_FILE.replace(backup)
                log.error(
                    "Config corrupted — backed up to %s and starting fresh.",
                    backup,
                )
            except OSError:
                log.error(
                    "Config corrupted and could not be backed up; "
                    "starting fresh."
                )
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(asdict(self), f, indent=2)
            os.replace(tmp, CONFIG_FILE)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
