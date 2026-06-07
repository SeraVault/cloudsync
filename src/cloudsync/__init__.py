from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

_PROD_APP_ID = "com.seravault.cloudsync"
_DEV_APP_ID = "com.seravault.cloudsync.dev"


def _running_from_source_tree() -> bool:
    package_dir = Path(__file__).resolve().parent
    return package_dir.name == "cloudsync" and package_dir.parent.name == "src"


APP_ID = os.environ.get("CLOUDSYNC_APP_ID", _PROD_APP_ID)

if "CLOUDSYNC_APP_ID" not in os.environ:
    if os.environ.get("FLATPAK_ID") == _PROD_APP_ID:
        APP_ID = _PROD_APP_ID
    elif _running_from_source_tree():
        APP_ID = _DEV_APP_ID
