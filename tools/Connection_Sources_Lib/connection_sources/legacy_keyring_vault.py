"""Read-only access to the retired OS-keyring SSH vault, for migration only.

Superseded by the HashiCorp Vault backend in `ssh_vault.py`; this module exists solely
so `ssh-key-migrate` can read keys that were vaulted under the old OS-keyring backend
before it is removed. It never writes -- `store_key`/`delete_key`/`import_from_path`
are deliberately not carried over, since nothing should be adding to a backend that is
being retired.

Windows resolves to Windows Credential Manager, Linux to SecretService (GNOME
Keyring/KWallet) -- both via `keyring.get_keyring()`. A headless Linux box with no
SecretService daemon resolves `keyring` to a no-op "fail" backend, so that case falls
back to `keyrings.cryptfile`, a local file encrypted with SSH_VAULT_PASSPHRASE.

Requires the `legacy-vault` optional dependency group (`keyring`, `keyrings.cryptfile`)
-- these are no longer core dependencies of this package.
"""

from __future__ import annotations

import base64
import json
import sys

from .errors import CredentialError

SERVICE = "talan-alm-ssh-vault"
_INDEX_USERNAME = "__aliases__"


def _reject_macos() -> None:
    if sys.platform == "darwin":
        raise CredentialError(
            "the legacy SSH vault is not supported on macOS",
            remediation="run the migration from Windows or Linux",
        )


def _is_fail_backend(backend: object) -> bool:
    from keyring.backends.fail import Keyring as FailKeyring

    return isinstance(backend, FailKeyring)


def _resolve_backend(vault_passphrase: str | None):
    _reject_macos()
    import os

    try:
        import keyring
    except ImportError as exc:
        raise CredentialError(
            "the keyring package is not installed",
            remediation="pip install 'connection-sources[legacy-vault]' to read the "
            "old OS-keyring vault for migration",
        ) from exc

    backend = keyring.get_keyring()
    if not _is_fail_backend(backend):
        return backend

    try:
        from keyrings.cryptfile.cryptfile import CryptFileKeyring
    except ImportError as exc:
        raise CredentialError(
            "no OS credential store is available and the keyrings.cryptfile "
            "fallback is not installed",
            remediation="pip install 'connection-sources[legacy-vault]'",
        ) from exc

    passphrase = vault_passphrase or os.environ.get("SSH_VAULT_PASSPHRASE")
    if not passphrase:
        raise CredentialError(
            "no OS credential store is available; the cryptfile vault fallback "
            "needs a passphrase",
            remediation="set SSH_VAULT_PASSPHRASE in the "
            "environment, then retry",
        )
    fallback = CryptFileKeyring()
    fallback.keyring_key = passphrase
    return fallback


def _read_index(backend) -> set[str]:
    raw = backend.get_password(SERVICE, _INDEX_USERNAME)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except json.JSONDecodeError:
        return set()


def backend_name(*, vault_passphrase: str | None = None) -> str:
    backend = _resolve_backend(vault_passphrase)
    return f"{type(backend).__module__}.{type(backend).__qualname__}"


def list_aliases(*, vault_passphrase: str | None = None) -> list[str]:
    backend = _resolve_backend(vault_passphrase)
    return sorted(_read_index(backend))


def get_key(alias: str, *, vault_passphrase: str | None = None) -> bytes:
    backend = _resolve_backend(vault_passphrase)
    encoded = backend.get_password(SERVICE, alias)
    if encoded is None:
        raise CredentialError(
            f"no SSH key vaulted under alias {alias!r} in the legacy OS-keyring vault"
        )
    return base64.b64decode(encoded)


def delete_key(alias: str, *, vault_passphrase: str | None = None) -> None:
    """Remove the alias from the legacy backend only. Used by ssh-key-legacy-purge."""
    backend = _resolve_backend(vault_passphrase)
    import keyring.errors

    try:
        backend.delete_password(SERVICE, alias)
    except keyring.errors.PasswordDeleteError as exc:
        raise CredentialError(
            f"no SSH key vaulted under alias {alias!r} in the legacy OS-keyring vault"
        ) from exc
    aliases = _read_index(backend)
    aliases.discard(alias)
    backend.set_password(SERVICE, _INDEX_USERNAME, json.dumps(sorted(aliases)))
