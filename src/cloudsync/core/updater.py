"""Update checker — fetches the latest release version and compares to the
installed version.  Updates are only surfaced to the user when they have an
active subscription.

Configure the version URL by setting CLOUDSYNC_VERSION_URL in the environment
or by editing VERSION_URL below.  The URL must return a plain-text semver
string (e.g. ``1.2.3``), optionally wrapped in a JSON object as
``{"version": "1.2.3"}``.

Example hosting options:
  - A raw file in your GitHub releases: the release asset URL
  - A tiny Cloudflare Worker / Lambda that checks the DB and returns the
    latest version for active subscribers
  - A static file on your own domain
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from .. import __version__
from . import license as lic

log = logging.getLogger(__name__)

# Replace with the URL of your hosted version file before shipping.
# Override at runtime with the CLOUDSYNC_VERSION_URL environment variable.
_DEFAULT_VERSION_URL = "https://seravault.com/cloudsync/latest-version"


def _version_url() -> str:
    import os
    return os.environ.get("CLOUDSYNC_VERSION_URL", _DEFAULT_VERSION_URL)


# --------------------------------------------------------------------------- #
# Version comparison                                                            #
# --------------------------------------------------------------------------- #

def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except (ValueError, AttributeError):
        return (0,)


def _fetch_latest() -> Optional[str]:
    """Return the latest version string from the version URL, or None on error."""
    try:
        req = urllib.request.Request(_version_url())
        req.add_header("Accept", "application/json, text/plain")
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read().decode().strip()
        # Accept either plain "1.2.3" or {"version": "1.2.3"}
        try:
            data = json.loads(raw)
            return str(data.get("version", raw))
        except (json.JSONDecodeError, AttributeError):
            return raw
    except Exception as exc:
        log.debug("Update check failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #

def check() -> Optional[str]:
    """Check for an available update.

    Returns the latest version string if:
      - the user has an active subscription, AND
      - the latest version is newer than the installed version.

    Returns ``None`` if no update is available, the subscription is inactive,
    or the check could not be performed (network error, URL not configured).
    """
    active, _ = lic.validate()
    if not active:
        log.debug("Update check skipped — subscription not active.")
        return None

    latest = _fetch_latest()
    if not latest:
        return None

    if _parse_version(latest) > _parse_version(__version__):
        log.info("Update available: %s → %s", __version__, latest)
        return latest

    return None
