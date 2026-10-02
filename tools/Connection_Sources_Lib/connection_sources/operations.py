"""Turn a configured source into a live read or write.

Every source reaches its system through a REST client in `clients`. There is no
second transport: what the CLI does and what the library does are the same calls.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .clients import REGISTRY as CLIENT_REGISTRY, client_for
from .clients.base import DEFAULT_TIMEOUT, AlmClient
from .env import mask
from .errors import ConnectionSourceError, SourcesConfigError, WriteBlockedError
from .models import AlmRecord, ConnectionResult, SourceConfig

__all__ = [
    "DEFAULT_TIMEOUT",
    "create",
    "delete",
    "has_direct_client",
    "open_client",
    "probe",
    "read",
    "transition",
    "update",
]


def has_direct_client(server: str) -> bool:
    return server in CLIENT_REGISTRY


def open_client(
    source: SourceConfig,
    env: Mapping[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> AlmClient:
    client = client_for(source.server, env, timeout=timeout)
    client.scope = dict(source.scope)
    return client


def probe(
    source: SourceConfig,
    env: Mapping[str, str],
    *,
    limit: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
) -> ConnectionResult:
    """Check one source and return the outcome as data, never raising.

    Credentials are proven separately from scope, so a bad token and an empty
    query never look alike.
    """
    try:
        with open_client(source, env, timeout=timeout) as client:
            identity = client.ping()
            records = client.search(source.scope, limit=limit)
            return ConnectionResult(
                source=source.name,
                server=source.server,
                ok=True,
                item_count=len(records),
                details={
                    "account": identity.account,
                    "base_url": identity.base_url,
                    "scope_type": source.scope_type,
                },
            )
    except ConnectionSourceError as exc:
        return ConnectionResult(
            source=source.name,
            server=source.server,
            ok=False,
            error=mask(exc.message, env),
            details={
                "remediation": exc.remediation,
                "kind": type(exc).__name__,
                "retryable": exc.retryable,
            },
        )
    except Exception as exc:
        return ConnectionResult(
            source=source.name,
            server=source.server,
            ok=False,
            error=mask(f"unexpected failure: {exc}", env),
            details={"kind": type(exc).__name__},
        )


_EXTRA_FIELD_SERVERS = frozenset({"jira", "azuredevops"})


def _check_extra_fields(source: SourceConfig, extra_fields: Iterable[str] | None) -> None:
    if extra_fields and source.server not in _EXTRA_FIELD_SERVERS:
        raise SourcesConfigError(
            f"extra_fields is only supported for jira and azuredevops, not {source.server!r}",
            server=source.server,
            remediation="drop --extra-fields for this source",
        )


def read(
    source: SourceConfig,
    env: Mapping[str, str],
    *,
    limit: int = 100,
    timeout: float = DEFAULT_TIMEOUT,
    extra_fields: Iterable[str] | None = None,
) -> list[AlmRecord]:
    _check_extra_fields(source, extra_fields)
    with open_client(source, env, timeout=timeout) as client:
        if extra_fields:
            return client.search(source.scope, limit=limit, extra_fields=extra_fields)
        return client.search(source.scope, limit=limit)


def get(
    source: SourceConfig,
    env: Mapping[str, str],
    ident: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    extra_fields: Iterable[str] | None = None,
) -> AlmRecord:
    _check_extra_fields(source, extra_fields)
    with open_client(source, env, timeout=timeout) as client:
        if extra_fields:
            return client.get(ident, extra_fields=extra_fields)
        return client.get(ident)


def _guard(writes_allowed: bool, source: SourceConfig) -> None:
    if not writes_allowed:
        raise WriteBlockedError(
            "writes are disabled for this project (safety.read_only is true)",
            source=source.name,
            remediation="set safety.read_only to false in sources.json, deliberately",
        )


def create(
    source: SourceConfig,
    env: Mapping[str, str],
    *,
    writes_allowed: bool,
    timeout: float = DEFAULT_TIMEOUT,
    **fields: Any,
) -> AlmRecord:
    _guard(writes_allowed, source)
    with open_client(source, env, timeout=timeout) as client:
        return client.create(source.scope, **fields)


def update(
    source: SourceConfig,
    env: Mapping[str, str],
    ident: str,
    *,
    writes_allowed: bool,
    timeout: float = DEFAULT_TIMEOUT,
    **fields: Any,
) -> AlmRecord:
    _guard(writes_allowed, source)
    with open_client(source, env, timeout=timeout) as client:
        return client.update(ident, **fields)


def transition(
    source: SourceConfig,
    env: Mapping[str, str],
    ident: str,
    status: str,
    *,
    writes_allowed: bool,
    timeout: float = DEFAULT_TIMEOUT,
) -> AlmRecord:
    _guard(writes_allowed, source)
    with open_client(source, env, timeout=timeout) as client:
        return client.transition(ident, status)


def delete(
    source: SourceConfig,
    env: Mapping[str, str],
    ident: str,
    *,
    writes_allowed: bool,
    permanent: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    _guard(writes_allowed, source)
    with open_client(source, env, timeout=timeout) as client:
        return client.delete(ident, permanent=permanent)
