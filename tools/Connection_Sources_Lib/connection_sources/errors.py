"""Typed errors carrying the source, a remediation and a CLI exit code."""

from __future__ import annotations

from typing import Any


class ConnectionSourceError(Exception):
    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        server: str | None = None,
        remediation: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.server = server
        self.remediation = remediation
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": self.message,
            "source": self.source,
            "server": self.server,
            "remediation": self.remediation,
            "retryable": self.retryable,
        }


class SourcesConfigError(ConnectionSourceError, ValueError):
    exit_code = 2


class ReportError(ConnectionSourceError, ValueError):
    exit_code = 2


class CredentialError(ConnectionSourceError):
    exit_code = 3


class ServerLaunchError(ConnectionSourceError):
    exit_code = 4


class ToolNotFoundError(ConnectionSourceError):
    exit_code = 4


class ToolCallError(ConnectionSourceError):
    exit_code = 5


class TransportError(ConnectionSourceError):
    """The endpoint could not be reached: DNS, TLS, proxy, timeout."""

    exit_code = 4


class ApiError(ConnectionSourceError):
    """The endpoint answered, and the answer was a refusal."""

    exit_code = 5

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "status": self.status}


class NotFoundError(ApiError):
    exit_code = 5


class PermissionDeniedError(ApiError):
    """Authenticated fine; the account is not allowed to do this."""

    exit_code = 3


class WriteBlockedError(ConnectionSourceError):
    """A mutating call was refused before it left the process."""

    exit_code = 2
