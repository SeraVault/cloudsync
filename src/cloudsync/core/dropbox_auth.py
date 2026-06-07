"""Dropbox OAuth2 authentication using the official Dropbox SDK.

Uses the PKCE (Proof Key for Code Exchange) flow — no client secret needed.
The app key is the public identifier from a Dropbox App Console registration.

Register at https://www.dropbox.com/developers/apps:
  1. Create App → Scoped access → Full Dropbox (or App folder)
  2. OAuth 2 → Redirect URIs → add http://localhost
  3. Copy the App key (not the secret — PKCE doesn't need it)
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import parse_qs, urlparse

from .config import CONFIG_DIR

_TOKEN_FILE = CONFIG_DIR / "dropbox_token.json"
_REDIRECT_URI = "http://localhost"
_CSRF_SESSION_KEY = "dropbox-oauth-csrf"

# Public app key — users can override with their own via save_app_key().
# Register your own at https://www.dropbox.com/developers/apps for production.
_DEFAULT_APP_KEY = "wf73ec34p75mvem"


class DropboxAuth:
    """Manages Dropbox OAuth2 PKCE credentials.

    Tokens are persisted in ``~/.config/cloudsync/dropbox_token.json``.

    Two auth paths:
    * :meth:`build_auth_url` + :meth:`exchange_code` — embedded WebKit dialog.
    * :meth:`authenticate_external` — system browser + localhost redirect server.
    """

    def __init__(self) -> None:
        self._app_key: str = self._load_app_key() or _DEFAULT_APP_KEY
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._account_id: Optional[str] = None
        self._email: Optional[str] = None
        self._flow = None   # dropbox.DropboxOAuth2Flow
        self._oauth_session: dict[str, str] = {}
        self._load_existing()

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_authenticated(self) -> bool:
        return bool(self._access_token or self._refresh_token)

    @property
    def has_app_key(self) -> bool:
        return bool(self._app_key)

    @property
    def app_key(self) -> str:
        return self._app_key

    @property
    def access_token(self) -> str:
        return self._access_token or ""

    @property
    def user_email(self) -> str:
        return self._email or ""

    def save_app_key(self, app_key: str) -> None:
        """Persist the Dropbox app key."""
        self._app_key = app_key.strip()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        key_file = CONFIG_DIR / "dropbox_app_key.json"
        key_file.write_text(json.dumps({"app_key": self._app_key}, indent=2))
        key_file.chmod(0o600)

    def _load_app_key(self) -> str:
        key_file = CONFIG_DIR / "dropbox_app_key.json"
        if not key_file.exists():
            return ""
        try:
            return json.loads(key_file.read_text()).get("app_key", "")
        except Exception:
            return ""

    # ---- Embedded webview flow ----------------------------------------- #

    def build_auth_url(self) -> str:
        """Return the Dropbox authorisation URL for the embedded WebKit dialog."""
        if not self._app_key:
            raise RuntimeError(
                "No Dropbox app key configured. "
                "Enter your app key in the setup wizard first."
            )
        from dropbox import DropboxOAuth2Flow

        self._oauth_session = {}
        self._flow = DropboxOAuth2Flow(
            self._app_key,
            _REDIRECT_URI,
            self._oauth_session,
            _CSRF_SESSION_KEY,
            use_pkce=True,
            token_access_type="offline",
        )
        return self._flow.start()

    def exchange_code(self, auth_response: str) -> bool:
        """Exchange the redirect callback URL (or code) for Dropbox tokens."""
        if self._flow is None:
            raise RuntimeError("Call build_auth_url() before exchange_code().")

        query_params = self._to_query_params(auth_response)
        result = self._flow.finish(query_params)
        self._access_token = result.access_token
        self._refresh_token = getattr(result, "refresh_token", None)
        self._account_id = result.account_id
        self._flow = None
        self._oauth_session = {}
        self._fetch_email()
        self._save()
        return True

    # ---- External browser fallback ------------------------------------ #

    def start_external_auth(self) -> str:
        """Start the no-redirect flow and return the auth URL to open in a browser."""
        if not self._app_key:
            raise RuntimeError("No Dropbox app key configured.")
        from dropbox import DropboxOAuth2FlowNoRedirect
        self._flow = DropboxOAuth2FlowNoRedirect(
            self._app_key,
            use_pkce=True,
            token_access_type="offline",
        )
        return self._flow.start()

    def finish_external_auth(self, code: str) -> bool:
        """Complete the no-redirect flow with the code the user copied from Dropbox."""
        if self._flow is None:
            raise RuntimeError("Call start_external_auth() first.")
        result = self._flow.finish(code.strip())
        self._access_token = result.access_token
        self._refresh_token = getattr(result, "refresh_token", None)
        self._account_id = result.account_id
        self._flow = None
        self._fetch_email()
        self._save()
        return True

    def _to_query_params(self, auth_response: str) -> dict[str, str]:
        """Convert a redirect URL or plain code into Dropbox flow params."""
        text = auth_response.strip()

        if "://" in text:
            parsed = urlparse(text)
            params = {
                key: values[0]
                for key, values in parse_qs(parsed.query).items()
                if values
            }
            if params:
                return params

        fallback = {
            "code": text,
            "state": self._oauth_session.get(_CSRF_SESSION_KEY, ""),
        }
        return fallback

    def get_client(self):
        """Return an authenticated dropbox.Dropbox client, refreshing if needed."""
        import dropbox
        if self._refresh_token:
            dbx = dropbox.Dropbox(
                oauth2_refresh_token=self._refresh_token,
                app_key=self._app_key,
            )
        elif self._access_token:
            dbx = dropbox.Dropbox(self._access_token)
        else:
            raise RuntimeError("Not authenticated.")
        return dbx

    def sign_out(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._account_id = None
        self._email = None
        if _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _fetch_email(self) -> None:
        try:
            dbx = self.get_client()
            account = dbx.users_get_current_account()
            self._email = account.email
        except Exception:
            pass

    def _load_existing(self) -> None:
        if not _TOKEN_FILE.exists():
            return
        try:
            data = json.loads(_TOKEN_FILE.read_text())
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")
            self._account_id = data.get("account_id")
            self._email = data.get("email")
        except Exception:
            pass

    def _save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "account_id": self._account_id,
            "email": self._email,
        }
        _TOKEN_FILE.write_text(json.dumps(data, indent=2))
        _TOKEN_FILE.chmod(0o600)
