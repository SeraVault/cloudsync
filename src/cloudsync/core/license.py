"""Stripe subscription validation.

Subscription status is checked against a thin backend that proxies Stripe.
The app never holds a Stripe secret key.

Validation endpoint (POST)
---------------------------
  POST https://us-central1-cloudsync-seravault.cloudfunctions.net/validateSubscription  # noqa: E501
  Content-Type: application/json
  Body: {"email": "<user email>"}

  200 response:
    {"active": true,  "renews_at": "2026-07-01"}
    {"active": false, "reason": "no_subscription"}

The app stores the email and cached status in
~/.config/cloudsync/license.json. Re-validation happens at most once per
week; on a network failure the cached status is used so the app keeps
working offline.

Free tier
---------
Without an active subscription the user is limited to 1 sync folder and
1 connected provider. A verified active subscription removes all limits.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_VALIDATE_URL = (
    "https://us-central1-cloudsync-seravault.cloudfunctions.net"
    "/validateSubscription"
)

# Stripe payment link — swap this one constant for production.
SUBSCRIBE_URL = (
    "https://buy.stripe.com/7sY8wPbq04hUfhh20Mf3a00"
)

_REVALIDATE_AFTER_DAYS = 7
_FREE_LIMIT = 1


# ------------------------------------------------------------------ #
# Data model                                                          #
# ------------------------------------------------------------------ #

@dataclass
class LicenseInfo:
    email: str = ""
    # "active" | "inactive" | ""
    status: str = ""
    # ISO-8601 date when the subscription renews, or ""
    renews_at: str = ""
    # ISO-8601 timestamp of the last successful remote validation
    last_validated: str = ""

    @property
    def is_signed_in(self) -> bool:
        return bool(self.email)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def needs_revalidation(self) -> bool:
        if not self.last_validated:
            return True
        try:
            lv = datetime.fromisoformat(self.last_validated)
        except ValueError:
            return True
        if lv.tzinfo is None:
            lv = lv.replace(tzinfo=timezone.utc)
        cutoff = timedelta(days=_REVALIDATE_AFTER_DAYS)
        return datetime.now(timezone.utc) - lv > cutoff

    def is_fully_licensed(self) -> bool:
        return self.is_signed_in and self.is_active

    def folder_limit(self) -> int | None:
        """Max sync folders allowed. None = unlimited."""
        return None if self.is_fully_licensed() else _FREE_LIMIT

    def provider_limit(self) -> int | None:
        """Max connected providers allowed. None = unlimited."""
        return None if self.is_fully_licensed() else _FREE_LIMIT

    def status_summary(self) -> tuple[str, str]:
        """Return (title, subtitle) for display in the UI."""
        if self.is_fully_licensed():
            sub = f"Renews: {self.renews_at}" if self.renews_at else ""
            return "Subscription active", sub
        if self.is_signed_in:
            return (
                f"Subscription {self.status or 'inactive'}",
                "Use the Subscribe button below to renew",
            )
        return (
            "Free tier",
            "Limited to 1 provider and 1 sync folder. "
            "Subscribe to unlock all features.",
        )


# ------------------------------------------------------------------ #
# Storage                                                             #
# ------------------------------------------------------------------ #

def _license_file() -> Path:
    from . import config as cfg  # noqa: PLC0415
    return cfg.CONFIG_DIR / "license.json"


def load() -> LicenseInfo:
    p = _license_file()
    if not p.exists():
        return LicenseInfo()
    try:
        with open(p) as f:
            data = json.load(f)
        known = set(LicenseInfo.__dataclass_fields__)
        return LicenseInfo(**{k: v for k, v in data.items() if k in known})
    except Exception:
        return LicenseInfo()


def _save(info: LicenseInfo) -> None:
    p = _license_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=p.parent, prefix=".license-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(info), f, indent=2)
        os.replace(tmp, p)
        p.chmod(0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ------------------------------------------------------------------ #
# Backend API                                                         #
# ------------------------------------------------------------------ #

def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ------------------------------------------------------------------ #
# Public API                                                          #
# ------------------------------------------------------------------ #

def sign_in(email: str) -> tuple[bool, str]:
    """Validate an email against the Stripe backend and store the result.

    Returns (success, user-visible message).
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Please enter a valid email address."
    try:
        result = _post_json(_VALIDATE_URL, {"email": email})
        info = load()
        info.email = email
        active = bool(result.get("active"))
        info.status = "active" if active else "inactive"
        info.renews_at = result.get("renews_at") or ""
        info.last_validated = datetime.now(timezone.utc).isoformat()
        _save(info)
        if active:
            return True, "Subscription verified. Thank you!"
        reason = result.get("reason", "")
        if reason == "no_subscription":
            return False, "No active subscription found for this email."
        return (
            False,
            "Subscription is inactive. Use the Subscribe button below.",
        )
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read())
            return False, body.get("error") or f"HTTP {exc.code}"
        except Exception:
            return False, f"HTTP error {exc.code}"
    except Exception as exc:
        log.warning("License sign-in error: %s", exc)
        return (
            False,
            "Could not reach the license server. "
            "Check your internet connection.",
        )


def validate(force: bool = False) -> tuple[bool, str]:
    """Re-validate the stored email against the backend.

    Skips the network call if validated recently (unless *force* is True).
    Falls back to cached status on network failure.
    Returns (is_active, user-visible message).
    """
    info = load()
    if not info.is_signed_in:
        return False, "No account connected."

    if not force and not info.needs_revalidation:
        if info.is_active:
            return True, "Subscription is active."
        return False, f"Subscription is {info.status or 'inactive'}."

    try:
        result = _post_json(_VALIDATE_URL, {"email": info.email})
        active = bool(result.get("active"))
        info.status = "active" if active else "inactive"
        info.renews_at = result.get("renews_at") or ""
        info.last_validated = datetime.now(timezone.utc).isoformat()
        _save(info)
        if active:
            return True, "Subscription is active."
        return False, f"Subscription is {info.status or 'inactive'}."
    except Exception as exc:
        log.warning("License validation error: %s", exc)
        return (
            info.is_active,
            "Could not reach the license server (using cached status).",
        )


def sign_out() -> None:
    """Remove the stored account, reverting to free tier."""
    _save(LicenseInfo())
