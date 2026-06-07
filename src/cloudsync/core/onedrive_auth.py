"""Microsoft OneDrive OAuth2 authentication via MSAL."""
from __future__ import annotations

from typing import Optional

from .config import CONFIG_DIR

_TOKEN_FILE = CONFIG_DIR / "onedrive_token.json"

_SCOPES = ["https://graph.microsoft.com/Files.ReadWrite"]
_REDIRECT_URI = "http://localhost"

_CREDS_FILE = CONFIG_DIR / "onedrive_credentials.json"

# Built-in client ID for personal Microsoft accounts (@outlook.com etc.)
# Uses the consumers authority — no app registration needed.
_PERSONAL_CLIENT_ID = "ab9b8c07-8f02-4f72-87fa-80105867a763"
_PERSONAL_AUTHORITY = "https://login.microsoftonline.com/consumers"

# Work/school accounts require a user-supplied client ID registered in
# their organisation's Entra ID tenant.
_WORK_AUTHORITY = "https://login.microsoftonline.com/organizations"


class OneDriveAuth:
    """Manages Microsoft OAuth2 tokens for OneDrive access.

    Two account types are supported:

    * **Personal** (@outlook.com, @hotmail.com, @live.com) — uses a built-in
      client ID with the ``consumers`` authority.  No registration needed.

    * **Work/school** (Microsoft 365) — requires a user-supplied Azure App
      client ID registered in their organisation's Entra ID tenant.

    The account type and optional client ID are stored in
    ``~/.config/cloudsync/onedrive_credentials.json``.
    Tokens are persisted in ``~/.config/cloudsync/onedrive_token.json``.
    """

    def __init__(self) -> None:
        self._account_type: str = "personal"  # "personal" | "work"
        self._client_id: str = ""
        self._app = None
        self._account: Optional[dict] = None
        self._token: Optional[str] = None
        self._pending_flow: Optional[dict] = None
        self._load_creds()
        if self._effective_client_id:
            self._init_msal()
            self._load_existing()

    @property
    def _effective_client_id(self) -> str:
        if self._account_type == "personal":
            return _PERSONAL_CLIENT_ID
        return self._client_id

    @property
    def _effective_authority(self) -> str:
        if self._account_type == "personal":
            return _PERSONAL_AUTHORITY
        return _WORK_AUTHORITY

    def _init_msal(self) -> None:
        import msal
        self._app = msal.PublicClientApplication(
            self._effective_client_id,
            authority=self._effective_authority,
            token_cache=self._load_cache(),
        )

    # ------------------------------------------------------------------ #
    # Client ID management                                                 #
    # ------------------------------------------------------------------ #

    @property
    def has_client_id(self) -> bool:
        """Personal accounts always have a built-in client ID."""
        return self._account_type == "personal" or bool(self._client_id)

    def save_personal(self) -> None:
        """Configure for a personal Microsoft account and reinitialise MSAL."""
        self._account_type = "personal"
        self._client_id = ""
        self._persist_creds()
        self._init_msal()

    def save_work(self, client_id: str) -> None:
        """Configure for a work/school account with a user-supplied client ID."""  # noqa: E501
        self._account_type = "work"
        self._client_id = client_id.strip()
        self._persist_creds()
        self._init_msal()

    # Legacy single-argument form kept for EditAccountDialog compatibility
    def save_client_id(self, client_id: str) -> None:
        self.save_work(client_id)

    def _persist_creds(self) -> None:
        import json
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data: dict = {"account_type": self._account_type}
        if self._client_id:
            data["client_id"] = self._client_id
        _CREDS_FILE.write_text(json.dumps(data, indent=2))
        _CREDS_FILE.chmod(0o600)

    def _load_creds(self) -> None:
        if not _CREDS_FILE.exists():
            return
        try:
            import json
            data = json.loads(_CREDS_FILE.read_text())
            self._account_type = data.get("account_type", "personal")
            self._client_id = data.get("client_id", "")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def is_authenticated(self) -> bool:
        return bool(self._token)

    @property
    def access_token(self) -> str:
        return self._token or ""

    @property
    def user_email(self) -> str:
        if self._account:
            return self._account.get("username", "")
        return ""

    # ---- Embedded webview flow (preferred) ----------------------------- #

    def build_auth_url(self) -> str:
        """Return the Microsoft login URL for use in an embedded webview.

        The redirect URI is ``http://localhost``.  Call :meth:`exchange_code`
        once the webview navigates to that URI with a ``code`` query parameter.
        """
        if not self._app:
            raise RuntimeError(
                "OneDrive not configured. "
                "Complete the setup wizard first."
            )
        flow = self._app.initiate_auth_code_flow(
            _SCOPES, redirect_uri=_REDIRECT_URI
        )
        self._pending_flow = flow
        return flow["auth_uri"]

    def exchange_code(self, auth_response_url: str) -> bool:
        """Complete the auth-code flow from the webview redirect URL.

        *auth_response_url* is the full ``http://localhost?code=...`` URL the
        webview navigated to.  Must be called after :meth:`build_auth_url`.
        Returns ``True`` on success; raises on failure.
        """
        if not hasattr(self, "_pending_flow"):
            raise RuntimeError("Call build_auth_url() before exchange_code().")

        # MSAL expects a dict of query params, not the full URL
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(auth_response_url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        result = self._app.acquire_token_by_auth_code_flow(
            self._pending_flow, params
        )
        del self._pending_flow
        return self._handle_result(result)

    # ---- External browser fallback ------------------------------------ #

    def authenticate_external(self) -> bool:
        """Open the system browser and spin a localhost redirect server.

        The HTTP server runs in a background thread so this method never
        blocks the UI. Raises TimeoutError if the browser doesn't redirect
        back within 5 minutes.
        """
        import threading
        import webbrowser
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.parse import parse_qs, urlparse

        result_holder: dict = {}
        done = threading.Event()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = {
                    k: v[0]
                    for k, v in parse_qs(parsed.query).items()
                }
                result_holder["params"] = params
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"<h1>Signed in - you may close this tab.</h1>"
                )
                done.set()

            def log_message(self, *_):
                pass

        server = HTTPServer(("localhost", 0), _Handler)
        port = server.server_address[1]
        flow = self._app.initiate_auth_code_flow(
            _SCOPES, redirect_uri=f"http://localhost:{port}"
        )
        webbrowser.open(flow["auth_uri"])

        def _serve():
            server.handle_request()
            server.server_close()

        threading.Thread(target=_serve, daemon=True).start()

        if not done.wait(timeout=300):
            server.server_close()
            raise TimeoutError(
                "No response from Microsoft login within 5 minutes."
            )

        result = self._app.acquire_token_by_auth_code_flow(
            flow, result_holder["params"]
        )
        return self._handle_result(result)

    def refresh_if_needed(self) -> bool:
        """Refresh the token silently if an account is cached."""
        accounts = self._app.get_accounts()
        if not accounts:
            return False
        result = self._app.acquire_token_silent(_SCOPES, account=accounts[0])
        if result and "access_token" in result:
            self._token = result["access_token"]
            self._account = accounts[0]
            self._persist_cache()
            return True
        return False

    def sign_out(self) -> None:
        for account in self._app.get_accounts():
            self._app.remove_account(account)
        self._token = None
        self._account = None
        if _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _handle_result(self, result: dict) -> bool:
        if "access_token" not in result:
            error = (
                result.get("error_description")
                or result.get("error", "unknown error")
            )
            raise ValueError(f"Microsoft sign-in failed: {error}")
        self._token = result["access_token"]
        accounts = self._app.get_accounts()
        self._account = accounts[0] if accounts else None
        self._persist_cache()
        return True

    def _load_cache(self):
        import msal
        cache = msal.SerializableTokenCache()
        if _TOKEN_FILE.exists():
            try:
                cache.deserialize(_TOKEN_FILE.read_text())
            except Exception:
                pass
        return cache

    def _load_existing(self) -> None:
        """Try a silent token refresh from a previously persisted cache."""
        self.refresh_if_needed()

    def _persist_cache(self) -> None:
        cache = self._app.token_cache
        if cache.has_state_changed:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_text(cache.serialize())
            _TOKEN_FILE.chmod(0o600)
