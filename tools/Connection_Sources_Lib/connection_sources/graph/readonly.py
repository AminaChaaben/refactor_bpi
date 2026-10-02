"""A client that can only read.

`AlmClient` exists to read *and* write an ALM system, and every write it exposes is
one the CLI deliberately gates behind `safety.read_only` and `writes_allowed`. The
graph needs none of that: it reads a test management system and writes only to Neo4j.

Subclassing and refusing the writes — rather than writing a second HTTP client — keeps
one implementation of the thing that actually matters here, which is the mapping from
an HTTP status onto a typed error carrying a remediation. A second transport would
mean a second, quietly diverging set of error semantics, and the first sign of that
divergence is somebody staring at a bare 403 with no idea which credential it belongs
to.

The refusals are `WriteBlockedError` because that is already what this codebase means
by "this call was stopped before it left the process".
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from ..clients.base import DEFAULT_TIMEOUT, AlmClient, ClientSpec
from ..errors import WriteBlockedError
from ..models import AlmRecord, Identity

__all__ = ["ReadOnlyClient"]


class ReadOnlyClient(AlmClient):
    """Transport, auth and error mapping — with every mutating method refused."""

    name: str = "readonly"
    required_env: tuple[str, ...] = ()
    spec = ClientSpec(name="readonly", required_env=())

    def __init__(
        self,
        *,
        base_url: str,
        env: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        auth: str = "",
    ) -> None:
        super().__init__(
            base_url=base_url,
            auth=auth,
            env=env,
            timeout=timeout,
            transport=transport,
        )
        # `spec` is a ClassVar shared by every instance, so a subclass that only set
        # `name` would report the wrong system in its errors. Binding a per-instance
        # spec here is what makes "xray: 401 unauthorized" say xray.
        self.spec = ClientSpec(name=self.name, required_env=tuple(self.required_env))

    # -- reads ------------------------------------------------------------

    def raw_get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def raw_post(
        self, path: str, json: Any = None, *, headers: Mapping[str, str] | None = None
    ) -> Any:
        return self.request("POST", path, json=json, headers=headers)

    # -- the contract, refused --------------------------------------------

    @classmethod
    def _build(
        cls,
        env: Mapping[str, str],
        *,
        timeout: float,
        transport: httpx.BaseTransport | None,
    ) -> "ReadOnlyClient":
        return cls.from_env(env, timeout=timeout, transport=transport)  # type: ignore[return-value]

    def ping(self) -> Identity:  # pragma: no cover - every subclass overrides this
        raise NotImplementedError

    def search(self, scope: Mapping[str, Any], *, limit: int = 100) -> list[AlmRecord]:
        return []

    def get(self, ident: str) -> AlmRecord:
        raise self._refuse("get")

    def create(self, scope: Mapping[str, Any], **fields: Any) -> AlmRecord:
        raise self._refuse("create")

    def update(self, ident: str, **fields: Any) -> AlmRecord:
        raise self._refuse("update")

    def transition(self, ident: str, status: str) -> AlmRecord:
        raise self._refuse("transition")

    def delete(self, ident: str, *, permanent: bool = False) -> dict[str, Any]:
        raise self._refuse("delete")

    def _refuse(self, action: str) -> WriteBlockedError:
        return WriteBlockedError(
            f"{self.name}: {action} is not available — this client only reads",
            server=self.name,
            remediation="use the Jira client for writes; the graph never mutates a tracker",
        )
