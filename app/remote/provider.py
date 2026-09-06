from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable
from urllib import error, request
from urllib.parse import urlparse

from app.remote.auth import AuthenticationError, RemoteAccount


logger = logging.getLogger(__name__)


class RemoteProviderError(RuntimeError):
    """A safe remote-provider failure."""


@dataclass(frozen=True)
class RemoteRepository:
    name: str
    full_name: str
    clone_url: str
    private: bool


class RemoteProvider:
    def create_repository(self, name: str, private: bool = True) -> RemoteRepository:
        raise NotImplementedError


class GitHubProvider(RemoteProvider):
    api_base = "https://api.github.com"

    def __init__(self, account: RemoteAccount, token_loader: Callable[[RemoteAccount], str | None]) -> None:
        self.account = account
        self._token_loader = token_loader

    def create_repository(self, name: str, private: bool = True) -> RemoteRepository:
        clean_name = name.strip()
        if not clean_name:
            raise RemoteProviderError("Enter a repository name.")
        token = self._token_loader(self.account)
        if not token:
            raise AuthenticationError("Authenticate with GitHub before creating a repository.")
        logger.info("GitHub repository API request has an authenticated token in memory: token_supplied=%s", bool(token))
        payload = self._request_json(
            "/user/repos",
            method="POST",
            token=token,
            body={"name": clean_name, "private": private},
        )
        try:
            return RemoteRepository(
                name=payload["name"],
                full_name=payload["full_name"],
                clone_url=payload["clone_url"],
                private=bool(payload["private"]),
            )
        except (KeyError, TypeError) as error:
            raise RemoteProviderError("GitHub returned an unexpected repository response.") from error

    def _request_json(
        self,
        path: str,
        method: str,
        token: str,
        body: dict[str, object] | None = None,
    ) -> dict[str, object]:
        encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
        target_url = f"{self.api_base}{path}"
        target = urlparse(target_url)
        request_constructed = False
        urlopen_entered = False
        response_received = False
        try:
            http_request = request.Request(
                target_url,
                data=encoded_body,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            request_constructed = True
            urlopen_entered = True
            with request.urlopen(http_request, timeout=20) as response:
                response_received = True
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as response_error:
            response_payload = self._error_payload(response_error)
            error_code, description = self._error_details(response_payload)
            logger.info(
                "GitHub repository API HTTP failure: status=%s error=%s error_description=%s",
                response_error.code,
                error_code,
                description,
            )
            raise RemoteProviderError(self._api_error(response_error.code, response_payload)) from response_error
        except Exception as request_error:
            logger.info(
                "GitHub repository API non-HTTP failure: exception_class=%s exception_message=%s hostname=%s path=%s request_constructed=%s urlopen_entered=%s response_received=%s",
                type(request_error).__name__,
                self._safe_exception_message(request_error, token),
                target.hostname,
                target.path,
                request_constructed,
                urlopen_entered,
                response_received,
            )
            raise RemoteProviderError("GitHub request failed. Check connectivity and authentication.") from request_error
        if not isinstance(result, dict):
            raise RemoteProviderError("GitHub returned an unexpected response.")
        return result

    @staticmethod
    def _error_payload(response_error: error.HTTPError) -> object:
        try:
            return json.loads(response_error.read().decode("utf-8"))
        except Exception:
            return None

    @staticmethod
    def _api_error(status: int, payload: object) -> str:
        message = f"GitHub API request failed (HTTP {status})."
        details = [value for value in GitHubProvider._error_details(payload) if value]
        return f"{message} {' — '.join(details)}" if details else message

    @staticmethod
    def _error_details(payload: object) -> tuple[str | None, str | None]:
        if not isinstance(payload, dict):
            return None, None
        error_code = payload.get("error")
        description = payload.get("error_description")
        return (
            error_code if isinstance(error_code, str) and error_code else None,
            description if isinstance(description, str) and description else None,
        )

    @staticmethod
    def _safe_exception_message(exception: Exception, token: str) -> str:
        message = str(exception)
        return message.replace(token, "<redacted>") if token else message
