"""An alias-addressed vault for SSH private keys, backed by HashiCorp Vault (KV v2).

Every key is stored by an alias the caller chooses, under
`{VAULT_KV_MOUNT}/talan-alm-ssh-vault/{alias}` -- this module never reads a path
under `~/.ssh` or `%USERPROFILE%\\.ssh`, never scans for candidate identity files, and
never writes back into that directory. `import_from_path` is the only function that
touches a private key file on disk, and it always takes an explicit path named by the
caller; it refuses (unless `force=True`) to import a file that looks like the OS-default
identity, so a company key sitting at its default name and location cannot be vaulted
by accident.

Connection is configured entirely through environment variables, read directly with
`os.environ.get(...)` the same way this module's predecessor read `SSH_VAULT_PASSPHRASE`
-- never routed through `env.load_project_env`'s `_known_keys()` registry, since
`gitlab_git`/this module are explicitly not an `AlmClient`:

  VAULT_ADDR          Vault server URL. Default: http://127.0.0.1:8200 (the local dev
                       Vault started by docker/docker-compose.vault.yml).
  VAULT_TOKEN          Token auth (the dev-mode default; see ../README.md).
  VAULT_ROLE_ID        AppRole auth, for a real (non-dev) Vault deployment -- used only
  VAULT_SECRET_ID      when VAULT_AUTH_METHOD=approle. Built now, even though the local
                       dev Vault only exercises token auth, so pointing this module at a
                       real Vault server later needs no code change, only config.
  VAULT_AUTH_METHOD    "token" (default) or "approle".
  VAULT_NAMESPACE      Optional Vault Enterprise namespace.
  VAULT_KV_MOUNT        KV v2 mount point. Default: "secret" (dev-mode Vault's built-in
                       mount).

Every function's `vault_token` keyword is an explicit override of `VAULT_TOKEN` for a
single call -- the same optional-override role the old OS-keyring backend's
`vault_passphrase` kwarg played for `SSH_VAULT_PASSPHRASE`.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from .errors import CredentialError

VAULT_PATH_PREFIX = "talan-alm-ssh-vault"

def _vault_env(key: str, default: str | None = None) -> str | None:
    """Resolve one VAULT_* setting from the process environment, else `default`.
    Re-read on every call rather than cached, matching `_resolve_client`'s own
    "no cached state" design.
    """
    return os.environ.get(key) or default

DEFAULT_SSH_DIR = Path.home() / ".ssh"
_DEFAULT_IDENTITY_NAMES = frozenset({"id_rsa", "id_ecdsa", "id_ed25519", "id_dsa"})


def _resolve_client(vault_token: str | None):
    """A `hvac.Client` authenticated against the configured Vault server.

    Never caches or reuses a client across calls -- each call is short-lived and this
    module has no long-running process state to keep it in, so re-authenticating per
    call is simpler than invalidation logic and costs one extra round trip at most.
    """
    try:
        import hvac
    except ImportError as exc:
        raise CredentialError(
            "the hvac package (HashiCorp Vault client) is not installed",
            remediation="pip install hvac, or reinstall Connection_Sources_Lib",
        ) from exc

    addr = _vault_env("VAULT_ADDR", "http://127.0.0.1:8200")
    namespace = _vault_env("VAULT_NAMESPACE") or None
    auth_method = (_vault_env("VAULT_AUTH_METHOD") or "token").lower()

    client = hvac.Client(url=addr, namespace=namespace)

    if auth_method == "approle":
        role_id = _vault_env("VAULT_ROLE_ID")
        secret_id = _vault_env("VAULT_SECRET_ID")
        if not role_id or not secret_id:
            raise CredentialError(
                "VAULT_AUTH_METHOD=approle needs VAULT_ROLE_ID and VAULT_SECRET_ID",
                remediation="set both in the environment, then retry",
            )
        try:
            client.auth.approle.login(role_id=role_id, secret_id=secret_id)
        except Exception as exc:  # hvac raises its own exception hierarchy
            raise CredentialError(
                f"Vault AppRole login failed: {exc}",
                remediation="check VAULT_ROLE_ID/VAULT_SECRET_ID and that the AppRole "
                "auth method is enabled on this Vault server",
            ) from exc
    elif auth_method == "token":
        token = vault_token or _vault_env("VAULT_TOKEN")
        if not token:
            raise CredentialError(
                "no Vault token available",
                remediation="set VAULT_TOKEN in the environment "
                "(the dev Vault's root token is printed by "
                "docker/docker-compose.vault.yml -- see ../README.md), then retry",
            )
        client.token = token
    else:
        raise CredentialError(
            f"unknown VAULT_AUTH_METHOD: {auth_method!r}",
            remediation="use 'token' or 'approle'",
        )

    try:
        reachable = client.is_authenticated()
    except Exception as exc:
        raise CredentialError(
            f"could not reach Vault at {addr}: {exc}",
            remediation="check VAULT_ADDR and that the Vault server is running "
            "(docker compose -f docker/docker-compose.vault.yml up -d)",
        ) from exc
    if not reachable:
        raise CredentialError(
            f"Vault at {addr} rejected this token/AppRole",
            remediation="check VAULT_TOKEN / VAULT_ROLE_ID+VAULT_SECRET_ID are current "
            "and not expired",
        )
    return client


def _kv_mount(vault_token: str | None = None) -> str:
    return _vault_env("VAULT_KV_MOUNT", "secret")


def backend_name(*, vault_token: str | None = None) -> str:
    addr = _vault_env("VAULT_ADDR", "http://127.0.0.1:8200")
    auth_method = (_vault_env("VAULT_AUTH_METHOD") or "token").lower()
    return f"hvac.Client(addr={addr!r}, auth={auth_method!r}, mount={_kv_mount()!r})"


def _check_alias(alias: str) -> None:
    if not alias or any(c in alias for c in "/\\"):
        raise CredentialError(
            f"invalid SSH key alias: {alias!r}",
            remediation="use a short name with no path separators, e.g. personal-push",
        )


def _read_secret_at_path(path: str, client) -> dict:
    """Raw secret dict at a full KV v2 path under the configured mount, or `{}` if
    nothing is vaulted there yet. The path is the caller's choice -- `_read_secret_data`
    below always prepends `VAULT_PATH_PREFIX` for the SSH-key vault; other in-package
    consumers reading a different path under the same mount root (e.g.
    `connection_sources.env`'s Jira-credentials Vault tier) call this directly instead
    of duplicating the read/InvalidPath handling.
    """
    try:
        response = client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=_kv_mount(),
            raise_on_deleted_version=True,
        )
    except Exception as exc:
        import hvac.exceptions

        if isinstance(exc, hvac.exceptions.InvalidPath):
            return {}
        raise CredentialError(f"could not read {path!r} from Vault: {exc}") from exc
    return dict(response["data"]["data"])


def _read_secret_data(alias: str, client) -> dict:
    """Raw secret dict for `alias`, or `{}` if nothing is vaulted yet there."""
    return _read_secret_at_path(f"{VAULT_PATH_PREFIX}/{alias}", client)


def store_key(alias: str, private_key_bytes: bytes, *, vault_token: str | None = None) -> None:
    _check_alias(alias)
    client = _resolve_client(vault_token)
    encoded = base64.b64encode(private_key_bytes).decode("ascii")
    # Preserve any passphrase already vaulted alongside this alias -- re-importing
    # the key (e.g. --force after a mistake) shouldn't silently wipe it.
    existing = _read_secret_data(alias, client)
    existing["private_key_b64"] = encoded
    try:
        client.secrets.kv.v2.create_or_update_secret(
            path=f"{VAULT_PATH_PREFIX}/{alias}",
            secret=existing,
            mount_point=_kv_mount(),
        )
    except Exception as exc:
        raise CredentialError(
            f"could not write alias {alias!r} to Vault: {exc}",
            remediation="check the Vault token has write access under "
            f"{_kv_mount()}/{VAULT_PATH_PREFIX}/",
        ) from exc


def get_key(alias: str, *, vault_token: str | None = None) -> bytes:
    _check_alias(alias)
    client = _resolve_client(vault_token)
    try:
        response = client.secrets.kv.v2.read_secret_version(
            path=f"{VAULT_PATH_PREFIX}/{alias}",
            mount_point=_kv_mount(),
            raise_on_deleted_version=True,
        )
    except Exception as exc:
        import hvac.exceptions

        if isinstance(exc, hvac.exceptions.InvalidPath):
            raise CredentialError(
                f"no SSH key vaulted under alias {alias!r}",
                remediation="import it first: alm-conn ssh-key-import --alias "
                f"{alias} --path <keyfile>",
            ) from exc
        raise CredentialError(f"could not read alias {alias!r} from Vault: {exc}") from exc
    encoded = response["data"]["data"].get("private_key_b64")
    if encoded is None:
        raise CredentialError(
            f"the Vault entry for alias {alias!r} is missing its private_key_b64 field",
            remediation="re-import the key: alm-conn ssh-key-import --alias "
            f"{alias} --path <keyfile> --force",
        )
    return base64.b64decode(encoded)


def delete_key(alias: str, *, vault_token: str | None = None) -> None:
    _check_alias(alias)
    client = _resolve_client(vault_token)
    try:
        client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=f"{VAULT_PATH_PREFIX}/{alias}",
            mount_point=_kv_mount(),
        )
    except Exception as exc:
        raise CredentialError(
            f"could not delete alias {alias!r} from Vault: {exc}",
            remediation="check ssh-key-list",
        ) from exc


def list_aliases(*, vault_token: str | None = None) -> list[str]:
    client = _resolve_client(vault_token)
    try:
        response = client.secrets.kv.v2.list_secrets(
            path=VAULT_PATH_PREFIX,
            mount_point=_kv_mount(),
        )
    except Exception as exc:
        import hvac.exceptions

        if isinstance(exc, hvac.exceptions.InvalidPath):
            # Nothing vaulted yet under this prefix -- not an error.
            return []
        raise CredentialError(f"could not list vaulted aliases from Vault: {exc}") from exc
    return sorted(response["data"]["keys"])


def store_passphrase(alias: str, passphrase: str, *, vault_token: str | None = None) -> None:
    """Vault an SSH key's passphrase alongside the key itself, same secret, same path.

    Only an alias that has had a passphrase explicitly stored this way is eligible
    for unattended auto-fetch (`get_passphrase` returning `None` is the normal,
    expected state for every other alias -- callers must fall back to prompting a
    human, never treat `None` as an error). Stored in reversible form deliberately --
    the caller needs the literal passphrase string to hand to `ssh-add`, so hashing it
    would make it useless for this purpose; Vault's own storage encryption is the
    at-rest protection here, not a hash.
    """
    _check_alias(alias)
    client = _resolve_client(vault_token)
    existing = _read_secret_data(alias, client)
    existing["passphrase"] = passphrase
    try:
        client.secrets.kv.v2.create_or_update_secret(
            path=f"{VAULT_PATH_PREFIX}/{alias}",
            secret=existing,
            mount_point=_kv_mount(),
        )
    except Exception as exc:
        raise CredentialError(
            f"could not write passphrase for alias {alias!r} to Vault: {exc}",
            remediation="check the Vault token has write access under "
            f"{_kv_mount()}/{VAULT_PATH_PREFIX}/",
        ) from exc


def get_passphrase(alias: str, *, vault_token: str | None = None) -> str | None:
    """Return the vaulted passphrase for `alias`, or `None` if none was ever stored.

    `None` is not an error -- most aliases will never have a stored passphrase, and
    the caller (`usine.py approve-push`) falls back to prompting a human for it.
    """
    _check_alias(alias)
    client = _resolve_client(vault_token)
    return _read_secret_data(alias, client).get("passphrase")


def delete_passphrase(alias: str, *, vault_token: str | None = None) -> None:
    _check_alias(alias)
    client = _resolve_client(vault_token)
    existing = _read_secret_data(alias, client)
    if "passphrase" not in existing:
        return
    del existing["passphrase"]
    try:
        client.secrets.kv.v2.create_or_update_secret(
            path=f"{VAULT_PATH_PREFIX}/{alias}",
            secret=existing,
            mount_point=_kv_mount(),
        )
    except Exception as exc:
        raise CredentialError(
            f"could not delete passphrase for alias {alias!r} from Vault: {exc}",
            remediation="check ssh-key-list for the alias name",
        ) from exc


def import_from_path(
    alias: str,
    path: str,
    *,
    force: bool = False,
    vault_token: str | None = None,
) -> dict[str, object]:
    """Read a private key from a path the caller names explicitly and vault it.

    Refuses a file that both sits directly in the OS-default SSH directory and
    carries a well-known default identity filename (id_rsa, id_ed25519, ...) --
    that combination is almost always the machine's existing company/default key,
    which this vault must never import. --force overrides for the rare case where
    that really is the personal key meant for this alias.
    """
    key_path = Path(path)
    if not key_path.is_file():
        raise CredentialError(
            f"no such file: {key_path}",
            remediation="pass an explicit --path to an existing private key file",
        )
    resolved = key_path.resolve()
    default_dir = DEFAULT_SSH_DIR.resolve()
    looks_like_default = (
        resolved.parent == default_dir and resolved.name in _DEFAULT_IDENTITY_NAMES
    )
    if looks_like_default and not force:
        raise CredentialError(
            f"{resolved} sits in the OS-default SSH directory ({default_dir}) under "
            "a default identity filename -- refusing to vault what looks like your "
            "existing default/company key",
            remediation=(
                "create or keep your personal test key in a separate folder (e.g. "
                f"{default_dir.parent}\\.ssh-personal on Windows, ~/.ssh-personal on "
                "Linux) and point --path at it; if this genuinely is a personal-"
                "account key you intend to vault, re-run with --force"
            ),
        )
    key_bytes = resolved.read_bytes()
    store_key(alias, key_bytes, vault_token=vault_token)
    return {"alias": alias, "imported_from": str(resolved), "bytes": len(key_bytes)}
