"""HTTP transport, credential handling and error mapping shared by every ALM client."""

from __future__ import annotations

import base64
import os
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping

import httpx

from ..env import mask
from ..errors import (
    ApiError,
    CredentialError,
    NotFoundError,
    PermissionDeniedError,
    TransportError,
)
from ..models import AlmRecord, Identity

DEFAULT_TIMEOUT = 30.0
MAX_PAGE = 100


def basic_auth(user: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode("ascii")


def _ssl_context() -> "ssl.SSLContext | bool":
    """The TLS trust store every client verifies its connections against.

    Prefers the operating system's own trust store (via `truststore`, when
    installed) over the bundled `certifi` list httpx falls back to: a corporate
    TLS-inspecting proxy is trusted by the OS -- pushed there by policy -- but
    never by `certifi`, so every ALM call behind one otherwise fails with a
    certificate error that has nothing to do with the ALM server. `ALM_CA_BUNDLE`
    overrides with an explicit PEM file for a machine where neither default is
    right; `truststore` missing just means the ordinary `certifi` default.
    """
    bundle = os.environ.get("ALM_CA_BUNDLE")
    if bundle:
        return ssl.create_default_context(cafile=bundle)
    try:
        import truststore  # noqa: PLC0415 - optional, OS-specific
    except ImportError:
        return True
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


@dataclass(frozen=True, slots=True)
class SuspectCredential:
    """A credential that is present but looks like it belongs to another system."""

    key: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class ClientSpec:
    """What a client needs from the environment, and where that comes from."""

    name: str
    required_env: tuple[str, ...]
    optional_env: tuple[str, ...] = ()
    scope_keys: tuple[str, ...] = ()
    token_hint: str = ""

    def missing(self, env: Mapping[str, str]) -> list[str]:
        return [key for key in self.required_env if not env.get(key)]


class AlmClient(ABC):
    """A read/write connection to one ALM system.

    Subclasses own their URLs and payload shapes; everything about sending a
    request, mapping a failure onto a typed error and redacting secrets from the
    message lives here so no system can quietly grow its own error semantics.

    `scope` carries the source's configured scope, set by operations.open_client.
    """

    spec: ClassVar[ClientSpec]

    def __init__(
        self,
        *,
        base_url: str,
        auth: str,
        auth_header: str = "Authorization",
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._env = dict(env or {})
        self.scope: dict[str, Any] = {}
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={auth_header: auth, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            verify=_ssl_context() if transport is None else True,
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> "AlmClient":
        absent = cls.spec.missing(env)
        if absent:
            raise CredentialError(
                f"{cls.spec.name}: missing credential(s): {', '.join(absent)}",
                server=cls.spec.name,
                remediation=cls.spec.token_hint
                or f"add a Vault reference for {absent} to this project",
            )
        return cls._build(env, timeout=timeout, transport=transport)

    @classmethod
    @abstractmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "AlmClient": ...

    @classmethod
    def suspect_credentials(cls, env: Mapping[str, str]) -> list[SuspectCredential]:
        """Credentials that are present but structurally the wrong kind.

        Checked offline, so a token pasted into the wrong slot is caught before it
        costs a call and a confusing 401.
        """
        return []

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AlmClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | str | None = None,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
        raw: bool = False,
        follow_redirects: bool | None = None,
    ) -> Any:
        # Per-call headers, for the one case the constructor cannot cover: a token
        # obtained by an earlier call on the same client, as Xray Cloud's
        # authenticate-then-query flow requires.
        sent = dict(headers or {})
        if content_type:
            sent["Content-Type"] = content_type
        request_kwargs: dict[str, Any] = {
            "params": params,
            "headers": sent or None,
        }
        # `content` is a raw body (Jenkins' config.xml uploads); `json` is the
        # existing serialize-as-JSON path (optionally with `content_type` overriding
        # the header, as Azure DevOps's JSON-Patch calls do). Mutually exclusive --
        # httpx itself rejects passing both -- so only one is ever put in the kwargs.
        if content is not None:
            request_kwargs["content"] = content
        else:
            request_kwargs["json"] = json
        if follow_redirects is not None:
            request_kwargs["follow_redirects"] = follow_redirects
        try:
            response = self._client.request(method, path, **request_kwargs)
        except httpx.TimeoutException as exc:
            raise TransportError(
                f"{self.spec.name}: request timed out after {self._client.timeout.read}s",
                server=self.spec.name,
                remediation="raise --timeout, or check network and proxy access",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise TransportError(
                mask(f"{self.spec.name}: cannot reach {self.base_url}: {exc}", self._env),
                server=self.spec.name,
                remediation="check the base URL, DNS and any corporate proxy",
                retryable=True,
            ) from exc
        response = self._checked(response)
        if raw:
            return response
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def _checked(self, response: httpx.Response) -> httpx.Response:
        """The response itself when it succeeded; the typed error otherwise."""
        if response.is_success:
            return response
        detail = mask(self._detail(response), self._env)
        status = response.status_code

        if status in (301, 302, 303, 307, 308):
            raise CredentialError(
                f"{self.spec.name}: redirected to sign-in ({status}) — the credential "
                "was not accepted",
                server=self.spec.name,
                remediation=self.spec.token_hint or "regenerate the token",
            )
        if status == 401:
            raise CredentialError(
                f"{self.spec.name}: 401 unauthorized" + (f" — {detail}" if detail else ""),
                server=self.spec.name,
                remediation=self.spec.token_hint
                or "check the token is valid and unexpired",
            )
        if status == 403:
            raise PermissionDeniedError(
                f"{self.spec.name}: 403 forbidden" + (f" — {detail}" if detail else ""),
                server=self.spec.name,
                status=status,
                remediation=(
                    "the credential is valid but this account lacks the rights for "
                    "this action; grant the permission in the project's permission "
                    "scheme, or widen the token's scope"
                ),
            )
        if status == 404:
            raise NotFoundError(
                f"{self.spec.name}: not found" + (f" — {detail}" if detail else ""),
                server=self.spec.name,
                status=status,
                remediation="check the id, project key and base URL",
            )
        if status == 429:
            raise ApiError(
                f"{self.spec.name}: rate limited",
                server=self.spec.name,
                status=status,
                remediation="retry after a pause",
                retryable=True,
            )
        raise ApiError(
            f"{self.spec.name}: {status}" + (f" — {detail}" if detail else ""),
            server=self.spec.name,
            status=status,
            remediation="check the query and the field names against the API docs",
            retryable=status >= 500,
        )

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text.strip()[:300]

        if isinstance(body, dict):
            for key in ("errorMessages", "message", "error", "value"):
                value = body.get(key)
                if isinstance(value, list) and value:
                    return "; ".join(str(v) for v in value)[:300]
                if isinstance(value, str) and value:
                    return value[:300]
                if isinstance(value, dict) and value.get("message"):
                    return str(value["message"])[:300]
            errors = body.get("errors")
            if isinstance(errors, dict) and errors:
                return "; ".join(f"{k}: {v}" for k, v in errors.items())[:300]
        return str(body)[:300]

    @abstractmethod
    def ping(self) -> Identity:
        """Cheapest call that proves the credential works. Never mutates."""

    @abstractmethod
    def search(self, scope: Mapping[str, Any], *, limit: int = MAX_PAGE) -> list[AlmRecord]: ...

    @abstractmethod
    def get(self, ident: str) -> AlmRecord: ...

    @abstractmethod
    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord: ...

    @abstractmethod
    def update(self, ident: str, **fields: Any) -> AlmRecord: ...

    @abstractmethod
    def transition(self, ident: str, status: str) -> AlmRecord: ...

    @abstractmethod
    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        """Remove one item, reporting what was removed and whether it is recoverable."""
