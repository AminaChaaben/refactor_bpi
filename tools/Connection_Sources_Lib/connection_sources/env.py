"""Per-project credential loading and secret redaction.

The broker fetch below (~40 lines: `_fetch_broker_env`/`load_broker_env`) is a
deliberate, separate re-implementation of the same `GET /internal/sandboxes/
{id}/credentials` call that `sandbox-image/credentials.py` (pod side) and
`Talan_Library/talan_credentials.py` (AI_Test_Lib side) also make.
Connection_Sources_Lib is its own uv project and is never on Talan_Library's
sys.path (it ships/runs independently), so it cannot import either of those
-- hence its own copy rather than a shared import. All three now follow the
same last-known-good-on-failure availability policy; keep them consistent by
hand if that policy ever changes.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping

from .errors import CredentialError

VAULT_ADDR_VAR = "VAULT_ADDR"
VAULT_TOKEN_VAR = "VAULT_TOKEN"

# env var -> KV v2 secret path, at the configured mount's root (NOT under
# ssh_vault.py's talan-alm-ssh-vault/ prefix, which is SSH-key-specific). Same
# paths and same field-per-secret shape Orchestrateur_Lib/scheduler/unified_tick.py's
# own `_ensure_jira_env` already reads, so one dev-Vault entry serves both consumers.
_JIRA_VAULT_SECRETS = {
    "JIRA_USERNAME": "Jira_username",
    "JIRA_API_TOKEN": "Jira_token",
}

BROKER_URL_VAR = "TALAN_CREDENTIALS_URL"
BROKER_TOKEN_VAR = "TALAN_CREDENTIALS_TOKEN"
BROKER_TIMEOUT_VAR = "TALAN_CREDENTIALS_TIMEOUT_S"
_BROKER_DEFAULT_TIMEOUT_S = 20.0

# Availability policy (matches sandbox-image/credentials.py, the pod-side
# reference, and Talan_Library/talan_credentials.py): serve the last
# successfully resolved set on a refresh failure -- a network blip must not
# take a project's credentials away mid-run. Raise CredentialError only when
# nothing has EVER been fetched successfully, i.e. there is nothing to fall
# back to. In-memory only, one process's lifetime.
_broker_lock = threading.Lock()
_broker_cached: dict[str, str] | None = None

_SECRET_HINTS = ("TOKEN", "SECRET", "PAT", "PASSWORD", "PASSPHRASE", "KEY")

_NEVER_SECRET = frozenset(
    {
        "JIRA_URL",
        "JIRA_BASE_URL",
        "JIRA_USERNAME",
        "JIRA_EMAIL",
        "GITLAB_URL",
        "AZURE_DEVOPS_ORG",
        "AZURE_DEVOPS_EMAIL",
        "ENABLED_TOOLS",
        "JIRA_PROJECTS_FILTER",
        "READ_ONLY_MODE",
    }
)

REDACTED = "***"
_MIN_MASKABLE = 6


def broker_configured() -> bool:
    return bool(os.environ.get(BROKER_URL_VAR) and os.environ.get(BROKER_TOKEN_VAR))


def _fetch_broker_env(url: str, token: str, timeout: float) -> dict[str, str]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    resolved = body.get("env") if isinstance(body, dict) else None
    if not isinstance(resolved, dict):
        return {}
    return {str(k): str(v) for k, v in resolved.items() if v}


def load_broker_env() -> dict[str, str]:
    global _broker_cached

    url = os.environ.get(BROKER_URL_VAR)
    token = os.environ.get(BROKER_TOKEN_VAR)
    if not url or not token:
        # TEMP DEBUG (2026-09-17, remove once vault credential flow is verified):
        # alm-conn silently falls back to an empty env otherwise, which reads
        # exactly like "Jira not configured" from the outside. stderr, not
        # stdout -- every caller's stdout is a JSON result a consumer parses
        # (graph-load's report, graph-query's rows, code-graph's status/index).
        print(
            f"[connection_sources][vault_debug] broker not configured: "
            f"{BROKER_URL_VAR}={'set' if url else 'MISSING'} "
            f"{BROKER_TOKEN_VAR}={'set' if token else 'MISSING'}",
            file=sys.stderr,
        )
        return {}
    try:
        timeout = float(os.environ.get(BROKER_TIMEOUT_VAR) or _BROKER_DEFAULT_TIMEOUT_S)
    except ValueError:
        timeout = _BROKER_DEFAULT_TIMEOUT_S

    with _broker_lock:
        try:
            resolved = _fetch_broker_env(url, token, timeout)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            if _broker_cached is not None:
                return dict(_broker_cached)
            raise CredentialError(
                f"the credential broker at {url} could not be reached: {exc}",
                remediation=(
                    "check that the backend is reachable from this sandbox and that "
                    f"{BROKER_TOKEN_VAR} is this sandbox's current token"
                ),
            ) from exc
        _broker_cached = resolved
        print(  # stderr, not stdout -- see the note on the other TEMP DEBUG print above.
            f"[connection_sources][vault_debug] resolved {len(resolved)} credentials from "
            f"the broker: {', '.join(sorted(resolved))}",
            file=sys.stderr,
        )
        return dict(resolved)


def jira_vault_configured() -> bool:
    """Whether the Jira Vault tier should even attempt Vault.

    Deliberately checks only `VAULT_ADDR`/`VAULT_TOKEN` -- mirroring the broker
    tier's own two-var no-op guard -- not `ssh_vault.py`'s AppRole variables
    (`VAULT_ROLE_ID`/`VAULT_SECRET_ID`). An AppRole-only setup (no `VAULT_TOKEN`)
    does not trigger this tier; that is this tier's own scope decision, independent
    of `ssh_vault.py` supporting AppRole for its SSH-key vault use.
    """
    return bool(os.environ.get(VAULT_ADDR_VAR) and os.environ.get(VAULT_TOKEN_VAR))


def _read_jira_vault_secret(secret_path: str, client) -> str | None:
    """One KV v2 secret's own-named field at the mount root, via ssh_vault's shared
    `_read_secret_at_path` helper (hvac-based) rather than a separate urllib call.
    Raises whatever `_read_secret_at_path` raises on a real Vault error; the caller
    decides how to log/swallow it.
    """
    from . import ssh_vault

    data = ssh_vault._read_secret_at_path(secret_path, client)
    return data.get(secret_path) or None


def load_jira_vault_env(resolved: Mapping[str, str]) -> dict[str, str]:
    """JIRA_USERNAME/JIRA_API_TOKEN from Vault, for whichever of the two are still
    missing from `resolved`. No-op (returns `{}`) when VAULT_ADDR/VAULT_TOKEN are
    unset, exactly like the broker tier is a no-op when its own two env vars are
    unset. Never overrides a key already present in `resolved`.

    One `hvac` authentication serves both possible reads (not one per secret). Never
    raises: an unreachable/misauthenticated Vault or a not-yet-vaulted secret just
    leaves the corresponding key unresolved -- the same discipline
    `_ensure_jira_env` applies for the scheduled tick -- but each skip is logged to
    stderr (never the secret value) so "Jira not using Vault" stays distinguishable
    from "Vault is misconfigured for Jira", the same reason `load_broker_env` above
    logs its own not-configured/failure cases.
    """
    if not jira_vault_configured():
        print(
            f"[connection_sources][vault_debug] jira vault tier skipped: "
            f"{VAULT_ADDR_VAR}={'set' if os.environ.get(VAULT_ADDR_VAR) else 'MISSING'} "
            f"{VAULT_TOKEN_VAR}={'set' if os.environ.get(VAULT_TOKEN_VAR) else 'MISSING'}",
            file=sys.stderr,
        )
        return {}

    missing = [key for key in _JIRA_VAULT_SECRETS if key not in resolved]
    if not missing:
        return {}

    from . import ssh_vault

    try:
        client = ssh_vault._resolve_client(None)
    except Exception as exc:
        print(
            f"[connection_sources][vault_debug] jira vault tier: could not "
            f"authenticate to Vault: {exc}",
            file=sys.stderr,
        )
        return {}

    fetched: dict[str, str] = {}
    for env_key in missing:
        secret_path = _JIRA_VAULT_SECRETS[env_key]
        try:
            value = _read_jira_vault_secret(secret_path, client)
        except Exception as exc:
            print(
                f"[connection_sources][vault_debug] jira vault tier: could not read "
                f"{secret_path!r}: {exc}",
                file=sys.stderr,
            )
            continue
        if value:
            fetched[env_key] = value
    return fetched


def load_project_env(project_path: str | Path) -> dict[str, str]:
    """This project's credentials: the process environment, then Vault.

    Three tiers, checked in the order a value actually becomes trustworthy. Real
    process environment variables come from whoever launched this process -- a
    sandbox pod, a CI job, or (for the shared, platform-level Neo4j) whatever
    loaded the Application-level `.env` docker-compose reads -- and always win.
    The credential broker is the sandbox's own resolved Vault secrets and is
    authoritative for every ALM connection when configured -- a broker that
    resolves nothing is treated as a hard failure rather than silently falling
    through, because a misconfigured broker must not read as "no credentials
    needed" -- so a broker that is configured but resolves nothing raises before
    this Vault tier ever runs; it only ever fills gaps the broker left, not a
    broker failure. Last, a direct Vault read fills in JIRA_USERNAME/JIRA_API_TOKEN
    for the interactive flow when neither of the tiers above set them and Vault is
    configured -- the same two secrets the scheduled tick already reads, through
    this package's own hvac client rather than a broker round trip. There is
    deliberately no project-local `.env` fallback: every ALM credential is
    Vault-managed, never a file checked out beside the project (and this Vault tier
    never writes one either).
    """
    known = _known_keys()
    values: dict[str, str] = {}
    for key in known:
        os_value = os.environ.get(key)
        if os_value:
            values[key] = os_value

    if broker_configured():
        values.update(load_broker_env())
        if not values:
            raise CredentialError(
                f"the credential broker resolved nothing for {project_path}",
                remediation=(
                    "add this project's Vault references so the backend has a path "
                    "and key to resolve"
                ),
            )
        values.update(load_jira_vault_env(values))
        return values

    values.update(load_jira_vault_env(values))

    if not values:
        raise CredentialError(
            f"no credentials found for {project_path}: no matching environment "
            "variables are set and no credential broker is configured",
            remediation=(
                f"set {BROKER_URL_VAR} and {BROKER_TOKEN_VAR}, or export the "
                "credentials this project needs directly (e.g. the platform's "
                "own NEO4J_* values)"
            ),
        )
    return values


#: Credentials the graph package needs that sit outside the ALM client registry:
#: the Neo4j driver is not an `AlmClient`, and Xray Cloud's OAuth client id/secret
#: is a separate credential from the Jira token it rides alongside. Both still
#: resolve through this same two-tier lookup -- without this, a project's Neo4j
#: password and Xray Cloud keys could never reach `graph.load_graph_config` no
#: matter where they were set.
_GRAPH_EXTRA_KEYS = (
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "XRAY_CLIENT_ID",
    "XRAY_CLIENT_SECRET",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
)


def _known_keys() -> list[str]:
    from .clients import REGISTRY

    keys: set[str] = set(_GRAPH_EXTRA_KEYS)
    for client_cls in REGISTRY.values():
        keys.update(client_cls.spec.required_env)
        keys.update(client_cls.spec.optional_env)
    return sorted(keys)


def is_secret(key: str) -> bool:
    if key in _NEVER_SECRET:
        return False
    return any(hint in key.upper() for hint in _SECRET_HINTS)


def secret_values(env: Mapping[str, str]) -> list[str]:
    return [v for k, v in env.items() if v and is_secret(k) and len(v) >= _MIN_MASKABLE]


def mask(text: str, env: Mapping[str, str]) -> str:
    if not text:
        return text
    masked = text
    for value in sorted(secret_values(env), key=len, reverse=True):
        masked = masked.replace(value, REDACTED)
    return masked


def redacted_view(env: Mapping[str, str]) -> dict[str, str]:
    return {k: (REDACTED if is_secret(k) else v) for k, v in sorted(env.items())}
