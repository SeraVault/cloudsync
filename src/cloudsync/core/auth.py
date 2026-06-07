"""Google OAuth2 authentication helper."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import TOKEN_FILE

SCOPES = ["https://www.googleapis.com/auth/drive"]

# ── App credentials ─────────────────────────────────────────────────────────
# Desktop OAuth credentials are intentionally embedded here. Per Google's own
# guidance (https://developers.google.com/identity/protocols/oauth2/native-app),  # noqa: E501
# the "client_secret" for installed/desktop apps cannot be kept confidential —
# it is not a server secret. The loopback redirect (localhost) ensures only the
# local machine receives the auth code. Rotate via Google Cloud Console if
# ever revoked.
#
# To override at runtime (e.g. for forks/white-labels), set:
#   CLOUDSYNC_GOOGLE_CLIENT_ID and CLOUDSYNC_GOOGLE_CLIENT_SECRET env vars,
# or place a client_secret.json next to this file.
# ────────────────────────────────────────────────────────────────────────────

_BUNDLED_CREDENTIALS_FILE = Path(__file__).parent / "client_secret.json"


def _load_client_config() -> dict:
    # 1. Env vars (highest priority — CI, forks, white-label builds)
    env_id = os.environ.get("CLOUDSYNC_GOOGLE_CLIENT_ID")
    env_secret = os.environ.get("CLOUDSYNC_GOOGLE_CLIENT_SECRET")
    if env_id and env_secret:
        return {
            "installed": {
                "client_id": env_id,
                "client_secret": env_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        }

    # 2. Bundled credentials file (standard Google client_secret.json format)
    if _BUNDLED_CREDENTIALS_FILE.exists():
        try:
            return json.loads(_BUNDLED_CREDENTIALS_FILE.read_text())
        except Exception:
            pass

    raise RuntimeError(
        "No Google OAuth credentials found. "
        "Place a client_secret.json next to auth.py or set "
        "CLOUDSYNC_GOOGLE_CLIENT_ID / CLOUDSYNC_GOOGLE_CLIENT_SECRET."
    )


# Loopback redirect used by the embedded webview flow
_REDIRECT_URI = "http://localhost"


class GoogleAuth:
    """Manages Google OAuth2 credentials.

    Tokens are persisted in ``~/.config/cloudsync/token.json``.
    No credentials file is needed — OAuth client credentials are bundled above.

    Two auth paths are available:

    * :meth:`build_auth_url` + :meth:`exchange_code` — used by the in-app
      WebKit dialog (preferred, no external browser).
    * :meth:`authenticate_external` — fallback that opens the system browser
      and spins up a localhost redirect server.
    """

    def __init__(self) -> None:
        self._creds: Optional[Credentials] = None
        self._flow: Optional[InstalledAppFlow] = None
        self._load_existing()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_authenticated(self) -> bool:
        return self._creds is not None and self._creds.valid

    @property
    def credentials(self) -> Optional[Credentials]:
        return self._creds

    @property
    def user_email(self) -> str:
        """Best-effort email from the token; empty string if unavailable."""
        if (self._creds and hasattr(self._creds, "id_token")
                and self._creds.id_token):
            return self._creds.id_token.get("email", "")
        return ""

    # ---- Embedded webview flow (preferred) ----------------------------- #

    def build_auth_url(self) -> str:
        """Prepare the OAuth2 flow and return the Google login URL.

        The caller should display this URL in a WebKit webview, then call
        :meth:`exchange_code` once the redirect carries an auth code.
        """
        self._flow = InstalledAppFlow.from_client_config(
            _load_client_config(), SCOPES, redirect_uri=_REDIRECT_URI
        )
        auth_url, _ = self._flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="select_account",
        )
        return auth_url

    def exchange_code(self, code: str) -> bool:
        """Exchange an auth code (from the webview redirect) for tokens.

        Must be called after :meth:`build_auth_url`.  Returns ``True`` on
        success.  This call hits the network and should run in a background
        thread.
        """
        if self._flow is None:
            raise RuntimeError("Call build_auth_url() before exchange_code().")
        self._flow.fetch_token(code=code)
        self._creds = self._flow.credentials
        self._flow = None
        self._save()
        return True

    # ---- External browser fallback ------------------------------------ #

    def authenticate_external(self) -> bool:
        """Open the system browser and spin a localhost redirect server.

        Use this only when WebKit is unavailable.
        """
        flow = InstalledAppFlow.from_client_config(_load_client_config(), SCOPES)
        self._creds = flow.run_local_server(port=0, open_browser=True)
        self._save()
        return True

    def refresh_if_needed(self) -> bool:
        """Refresh an expired token.  Returns True if credentials are valid."""
        if self._creds is None:
            return False
        if self._creds.valid:
            return True
        if self._creds.expired and self._creds.refresh_token:
            try:
                self._creds.refresh(Request())
                self._save()
                return True
            except Exception:
                self._creds = None
                if TOKEN_FILE.exists():
                    TOKEN_FILE.unlink()
        return False

    def sign_out(self) -> None:
        self._creds = None
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _load_existing(self) -> None:
        if TOKEN_FILE.exists():
            try:
                self._creds = Credentials.from_authorized_user_file(
                    str(TOKEN_FILE), SCOPES
                )
                self.refresh_if_needed()
            except Exception:
                self._creds = None

    def _save(self) -> None:
        if self._creds:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(self._creds.to_json())
