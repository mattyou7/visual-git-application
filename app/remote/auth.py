from __future__ import annotations

import subprocess
import sys
import base64
import hashlib
import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from typing import Callable
from urllib import error, parse, request
from urllib.parse import urlparse


class AuthenticationError(RuntimeError):
    """A safe, user-facing authentication failure."""


@dataclass(frozen=True)
class RemoteAccount:
    provider: str
    host: str
    account: str


@dataclass(frozen=True)
class GitHubAuthResult:
    account: RemoteAccount
    token: str


class CredentialStore:
    def save(self, account: RemoteAccount, token: str) -> None:
        raise NotImplementedError

    def load(self, account: RemoteAccount) -> str | None:
        raise NotImplementedError

    def delete(self, account: RemoteAccount) -> None:
        raise NotImplementedError


class MacOSKeychainCredentialStore(CredentialStore):
    service_prefix = "Visual Git Workspace"

    def save(self, account: RemoteAccount, token: str) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise AuthenticationError("Authentication token cannot be empty.")
        self._run(
            "add-generic-password",
            "-U",
            "-s",
            self._service_name(account),
            "-a",
            account.account,
            "-w",
            clean_token,
        )

    def load(self, account: RemoteAccount) -> str | None:
        result = self._run("find-generic-password", "-s", self._service_name(account), "-a", account.account, check=False)
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise AuthenticationError("Could not read the saved authentication credential.")
        return result.stdout.strip() or None

    def delete(self, account: RemoteAccount) -> None:
        result = self._run("delete-generic-password", "-s", self._service_name(account), "-a", account.account, check=False)
        if result.returncode not in {0, 44}:
            raise AuthenticationError("Could not remove the saved authentication credential.")

    def _service_name(self, account: RemoteAccount) -> str:
        return f"{self.service_prefix}:{account.provider}:{account.host}"

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if sys.platform != "darwin":
            raise AuthenticationError("Secure Keychain authentication is only available on macOS.")
        try:
            result = subprocess.run(
                ["security", *arguments],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as error:
            raise AuthenticationError("Could not access the macOS Keychain.") from error
        if check and result.returncode != 0:
            raise AuthenticationError("Could not save the authentication credential securely.")
        return result


def account_from_remote_url(provider: str, remote_url: str, account: str) -> RemoteAccount:
    parsed = urlparse(remote_url if "://" in remote_url else f"ssh://{remote_url}")
    host = parsed.hostname
    if not host:
        raise AuthenticationError("Remote URL does not contain a valid host.")
    clean_account = account.strip()
    if not clean_account:
        raise AuthenticationError("Authentication account cannot be empty.")
    return RemoteAccount(provider=provider.strip() or "unknown", host=host, account=clean_account)


class GitHubAuthenticator:
    authorization_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    api_endpoint = "https://api.github.com/user"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        credential_store: CredentialStore,
        browser_opener: Callable[[str], bool] = webbrowser.open,
        urlopen: Callable[..., object] = request.urlopen,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.credential_store = credential_store
        self.browser_opener = browser_opener
        self.urlopen = urlopen

    def connect(self, timeout: int = 180) -> GitHubAuthResult:
        if not self.client_id or not self.client_secret:
            raise AuthenticationError("GitHub authentication is not configured for this application.")
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        callback = _OAuthCallbackServer(state)
        callback.start()
        redirect_uri = f"http://127.0.0.1:{callback.port}/callback"
        query = parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "repo",
        })
        try:
            if not self.browser_opener(f"{self.authorization_endpoint}?{query}"):
                raise AuthenticationError("Could not open GitHub authorization in the browser.")
            code = callback.wait(timeout)
            token = self._exchange_code(code, redirect_uri, verifier)
            user = self._github_user(token)
            login = user.get("login") if isinstance(user, dict) else None
            if not isinstance(login, str) or not login:
                raise AuthenticationError("GitHub did not return an account name.")
            account = RemoteAccount("GitHub", "github.com", login)
            self.credential_store.save(account, token)
            return GitHubAuthResult(account, token)
        finally:
            callback.close()

    def disconnect(self, account: RemoteAccount) -> None:
        self.credential_store.delete(account)

    def _exchange_code(self, code: str, redirect_uri: str, verifier: str) -> str:
        payload = parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }).encode()
        http_request = request.Request(
            self.token_endpoint,
            data=payload,
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self.urlopen(http_request, timeout=20) as response:
                data = json.loads(response.read().decode())
        except error.HTTPError as response_error:
            raise AuthenticationError(self._token_exchange_error(self._error_payload(response_error))) from response_error
        except Exception as exchange_error:
            raise AuthenticationError("GitHub token exchange failed.") from exchange_error
        if isinstance(data, dict) and isinstance(data.get("error"), str) and data["error"]:
            raise AuthenticationError(self._token_exchange_error(data))
        token = data.get("access_token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise AuthenticationError(self._token_exchange_error(data))
        return token

    @staticmethod
    def _error_payload(response_error: error.HTTPError) -> object:
        try:
            return json.loads(response_error.read().decode())
        except Exception:
            return None

    @staticmethod
    def _token_exchange_error(payload: object) -> str:
        if not isinstance(payload, dict):
            return "GitHub token exchange failed."
        error_code = payload.get("error")
        description = payload.get("error_description")
        details = [value for value in (error_code, description) if isinstance(value, str) and value]
        if not details:
            return "GitHub token exchange failed."
        return f"GitHub token exchange failed: {' — '.join(details)}"

    def _github_user(self, token: str) -> dict[str, object]:
        http_request = request.Request(
            self.api_endpoint,
            headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
        )
        try:
            with self.urlopen(http_request, timeout=20) as response:
                data = json.loads(response.read().decode())
        except Exception as error:
            raise AuthenticationError("GitHub account lookup failed.") from error
        if not isinstance(data, dict):
            raise AuthenticationError("GitHub returned an unexpected account response.")
        return data


class _OAuthCallbackServer:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None
        self.event = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = parse.urlparse(self.path)
                values = parse.parse_qs(parsed.query)
                if values.get("state", [None])[0] != owner.expected_state:
                    owner.error = "GitHub authorization state did not match."
                elif values.get("error", [None])[0]:
                    owner.error = "GitHub authorization was denied."
                else:
                    owner.code = values.get("code", [None])[0]
                    if not owner.code:
                        owner.error = "GitHub did not return an authorization code."
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization received. You can close this window.")
                owner.event.set()

            def log_message(self, *_args: object) -> None:
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.handle_request, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def wait(self, timeout: int) -> str:
        if not self.event.wait(timeout):
            raise AuthenticationError("GitHub authorization timed out.")
        if self.error:
            raise AuthenticationError(self.error)
        if not self.code:
            raise AuthenticationError("GitHub authorization did not return a code.")
        return self.code

    def close(self) -> None:
        self.server.server_close()
