"""ALM connection sources: Jira, Azure DevOps, GitLab.

Every system is reached over its own REST API through `clients`. There is one
transport and one code path, so what the CLI reports and what the library returns
cannot differ.

Reusable entry points, for the orchestrator or any other factory code:

    from connection_sources import resolve, check, read, create, update, transition

    resolve(project)                      what this project connects to, offline
    preflight(project)                    config/credential validation, offline
    identify(project, "jira")             who the credential authenticates as
    check(project)                        probe every enabled source, live
    read(project, "jira")                 records for a source's configured scope
    export_project(project, source="jira") writes every item to disk as JSON
    create(project, "jira", summary="...", project="<PROJECT-KEY>")
    update(project, "jira", "<ISSUE-KEY>", summary="...")
    transition(project, "jira", "<ISSUE-KEY>", "<STATUS>")
    delete(project, "jira", "<ISSUE-KEY>")

Reads return `AlmRecord`s in one shape whatever system produced them. Writes are
refused when sources.json sets safety.read_only.
"""

from .api import (
    check,
    client,
    create,
    delete,
    identify,
    preflight,
    read,
    resolve,
    source_for,
    transition,
    update,
    writes_allowed,
)
from .clients import (
    REGISTRY,
    AlmClient,
    AzureDevOpsClient,
    ClientSpec,
    GitLabClient,
    JiraClient,
    SuspectCredential,
    client_for,
)
from .config import SourcesConfigLoader
from .env import load_project_env, mask, redacted_view
from .export import export_project
from .errors import (
    ApiError,
    ConnectionSourceError,
    CredentialError,
    NotFoundError,
    PermissionDeniedError,
    ReportError,
    SourcesConfigError,
    TransportError,
    WriteBlockedError,
)
from .health import check_project, doctor, load_project, sources_path
from .models import (
    AlmRecord,
    ConnectionReport,
    ConnectionResult,
    Identity,
    ProjectSourcesConfig,
    ResolvedProject,
    ResolvedSource,
    SourceConfig,
)
from .operations import probe
from .report import ConnectionReportBuilder

__all__ = [
    "REGISTRY",
    "AlmClient",
    "AlmRecord",
    "ApiError",
    "AzureDevOpsClient",
    "ClientSpec",
    "ConnectionReport",
    "ConnectionReportBuilder",
    "ConnectionResult",
    "ConnectionSourceError",
    "CredentialError",
    "GitLabClient",
    "Identity",
    "JiraClient",
    "NotFoundError",
    "PermissionDeniedError",
    "ProjectSourcesConfig",
    "ReportError",
    "ResolvedProject",
    "ResolvedSource",
    "SourceConfig",
    "SourcesConfigError",
    "SourcesConfigLoader",
    "SuspectCredential",
    "TransportError",
    "WriteBlockedError",
    "check",
    "check_project",
    "client",
    "client_for",
    "create",
    "delete",
    "doctor",
    "export_project",
    "identify",
    "load_project",
    "load_project_env",
    "mask",
    "preflight",
    "probe",
    "read",
    "redacted_view",
    "resolve",
    "source_for",
    "sources_path",
    "transition",
    "update",
    "writes_allowed",
]
