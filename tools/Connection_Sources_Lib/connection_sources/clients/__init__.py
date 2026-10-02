"""Client registry: a server name from sources.json to a live ALM client."""

from __future__ import annotations

from typing import Mapping, Type

import httpx

from ..errors import SourcesConfigError
from .azure import AzureDevOpsClient
from .base import DEFAULT_TIMEOUT, AlmClient, ClientSpec, SuspectCredential, basic_auth
from .confluence import ConfluenceClient
from .gitlab import GitLabClient
from .jenkins import JenkinsClient
from .jira import JiraClient

REGISTRY: dict[str, Type[AlmClient]] = {
    "jira": JiraClient,
    "azuredevops": AzureDevOpsClient,
    "gitlab": GitLabClient,
    # Read-only (see the module docstring): every write method raises.
    "confluence": ConfluenceClient,
    "jenkins": JenkinsClient,
}

__all__ = [
    "REGISTRY",
    "AlmClient",
    "AzureDevOpsClient",
    "ClientSpec",
    "ConfluenceClient",
    "GitLabClient",
    "JenkinsClient",
    "JiraClient",
    "SuspectCredential",
    "basic_auth",
    "client_for",
    "get_client_class",
]


def get_client_class(server: str) -> Type[AlmClient]:
    try:
        return REGISTRY[server]
    except KeyError:
        raise SourcesConfigError(
            f"no direct client for server {server!r}",
            server=server,
            remediation=f"supported: {', '.join(sorted(REGISTRY))}",
        ) from None


def client_for(
    server: str,
    env: Mapping[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> AlmClient:
    """Build a ready client for one server, or raise a typed error saying why not."""
    return get_client_class(server).from_env(env, timeout=timeout, transport=transport)
