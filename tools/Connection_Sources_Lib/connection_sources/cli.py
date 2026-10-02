"""alm-conn: JSON to stdout, diagnostics to stderr, one exit code per failure class.

0 success | 1 usage | 2 config/report | 3 credentials | 4 transport | 5 API refused.
`pipeline-run --wait` and `pipeline-status --wait` also use 5 for a pipeline that
finished failed/canceled, and 4 when the wait itself timed out.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import getpass
import os
import sys
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from . import api
from . import ssh_vault
from .clients import REGISTRY as CLIENT_REGISTRY
from .clients import azure as azure_client
from .clients import gitlab_git
from .clients.base import DEFAULT_TIMEOUT
from .config import SourcesConfigLoader
from .env import load_project_env, redacted_view
from .errors import (
    ConnectionSourceError,
    CredentialError,
    ReportError,
    SourcesConfigError,
    WriteBlockedError,
)
from .export import export_project
from . import graph
from .graph import xray as xray_mod
from .health import check_project, doctor, load_project, sources_path, write_report
from .models import AlmRecord, SourceConfig
from . import operations
from .report import ConnectionReportBuilder
from .schedule import (
    BACKENDS as SCHEDULE_BACKENDS,
    install as schedule_install,
    remove as schedule_remove,
    run_now as schedule_run_now,
    set_enabled as schedule_set_enabled,
    status as schedule_status,
)
from .sync import SyncResult, init_state, sync_once, watch as sync_watch
from .sync.detail_fields import add_detail_field, list_detail_fields, remove_detail_field
from .sync.runner import DEFAULT_INTERVAL, load_detail_fields, load_sync_config
from .sync.runner import default_state_dir as sync_default_state_dir

CLIENTS_ROOT = Path(__file__).resolve().parent.parent / "clients"
FACTORY_PROJECTS_ROOT = (
    Path(__file__).resolve().parents[3] / "Talan_Factory" / "Talan_usine_config"
)


def _serialize(value: Any, *, include_raw: bool = False) -> Any:
    if isinstance(value, AlmRecord):
        return value.to_dict(include_raw=include_raw)
    if isinstance(value, list):
        return [_serialize(v, include_raw=include_raw) for v in value]
    return value


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _fail(exc: ConnectionSourceError) -> int:
    print(json.dumps(exc.to_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
    return exc.exit_code


def _parse_param_value(text: str) -> Any:
    """`--param key=value`'s value: null/true/false/int/float, else the raw string."""
    lowered = text.lower()
    if lowered in ("null", "none"):
        return None
    if lowered in ("true", "false"):
        return lowered == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _write_json(path: str, payload: Any) -> None:
    """Write a document to a path the caller named, creating its folder if needed.

    A `--out` pointing into a directory that does not exist yet is a typo often
    enough, but far more often it is somebody writing into a reports folder they have
    not created. Failing there costs the whole read that produced the payload.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


# The parts of the state document worth printing when the full one is too much to
# read: what was read, how big it is, and what is missing.
_STATE_SUMMARY = ("schema", "project", "generated_at", "sources", "sites", "xray",
                  "read", "totals", "gaps", "errors")


def _parse_fields(pairs: list[str]) -> dict[str, Any]:
    """Turn --field KEY=VALUE repeats into a dict, coercing obvious scalars.

    A value opening with [ or { is read as JSON, which is the only way to reach a
    field the API types as a list or an object, such as a Jira labels array. Text
    that merely looks like JSON and does not parse stays the string it was.

    Windows shells strip the embedded quotes a JSON list needs, so a bare
    `labels=[a,b]` — after the outer shell quoting has done its worst — is
    accepted as a comma-separated list as a fallback: `[`-opening values that do
    not parse as JSON are split on commas, trimmed and unquoted.

    A literal two-character `\\n` inside a plain-string value is unescaped to a
    real newline (`\\t` to a real tab), the same convention JSON string literals
    already use — a multi-paragraph value can be written as
    `--field description='AC1: ... \\n\\nAC2: ...'` with no actual line break
    anywhere in the command line. This exists because a real embedded newline in
    the command text has been confirmed to get an otherwise-perfect, pattern-
    matching command refused outright under a scoped agent's `--allowedTools`
    (its wildcard does not appear to span one) — typing `\\n` instead keeps the
    whole command on one physical line while still producing properly
    paragraphed text once it lands. JSON values are unaffected: `json.loads`
    already applies this exact unescaping to `\\n` inside a JSON string per the
    JSON spec, so nothing extra happens there.
    """
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        if not sep:
            raise SourcesConfigError(
                f"malformed --field {pair!r}", remediation="use --field KEY=VALUE"
            )
        if raw.lower() in {"true", "false"}:
            out[key] = raw.lower() == "true"
        elif raw.lstrip("-").isdigit():
            out[key] = int(raw)
        elif raw[:1] in {"[", "{"}:
            try:
                out[key] = json.loads(raw)
            except json.JSONDecodeError:
                if raw[:1] == "[" and raw[-1:] == "]":
                    items = [
                        item.strip().strip('"\'')
                        for item in raw[1:-1].split(",")
                        if item.strip()
                    ]
                    out[key] = items
                else:
                    out[key] = raw
        else:
            out[key] = raw.replace("\\n", "\n").replace("\\t", "\t")
    return out


def _normalize_project_key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _resolve_project(value: str) -> str:
    """A literal existing folder wins outright. Otherwise the value is matched,
    case- and punctuation-insensitive, against every project folder name under
    the two homes a client project may live in — `clients/<client>/<project>`
    inside this package, and the factory's `Talan_Factory/Talan_usine_config` —
    so `--project talan_usine_config` finds the folder without the caller spelling out
    the path. An exact match on one of those two roots' own name wins outright,
    even if some unrelated folder several levels deeper happens to share the same
    name -- a project's own root is never ambiguous with one of its own descendants.
    """
    literal = Path(value)
    if literal.is_dir():
        return str(literal.resolve())

    key = _normalize_project_key(value)

    root_matches = [
        root
        for root in (CLIENTS_ROOT, FACTORY_PROJECTS_ROOT)
        if root.is_dir() and _normalize_project_key(root.name) == key
    ]
    if len(root_matches) == 1:
        return str(root_matches[0].resolve())

    matches: list[Path] = []
    for root in (CLIENTS_ROOT, FACTORY_PROJECTS_ROOT):
        if not root.is_dir():
            continue
        if _normalize_project_key(root.name) == key:
            matches.append(root)
        for client_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if _normalize_project_key(client_dir.name) == key:
                matches.append(client_dir)
            for project_dir in sorted(p for p in client_dir.iterdir() if p.is_dir()):
                if _normalize_project_key(project_dir.name) == key:
                    matches.append(project_dir)

    if len(matches) == 1:
        return str(matches[0].resolve())
    if not matches:
        raise SourcesConfigError(
            f"no project folder matches --project {value!r}",
            remediation=(
                "pass a literal --project <folder> path, or add a folder named "
                f"like it under {CLIENTS_ROOT.as_posix()}/<client>/ or "
                f"{FACTORY_PROJECTS_ROOT.as_posix()}/"
            ),
        )

    def _display(path: Path) -> str:
        # Matches can come from either root's subtree -- try each in turn rather
        # than assuming the one this listing used to assume.
        for base in (CLIENTS_ROOT.parent, FACTORY_PROJECTS_ROOT.parent):
            try:
                return str(path.relative_to(base).as_posix())
            except ValueError:
                continue
        return path.as_posix()

    listing = ", ".join(_display(m) for m in matches)
    raise SourcesConfigError(
        f"--project {value!r} matches more than one folder: {listing}",
        remediation="pass the full --project <folder> path to disambiguate",
    )


def _cmd_systems(args: argparse.Namespace) -> int:
    _emit(
        {
            name: {
                "transport": "rest",
                "required_env": list(cls.spec.required_env),
                "optional_env": list(cls.spec.optional_env),
                "scope_keys": list(cls.spec.scope_keys),
                "token_hint": cls.spec.token_hint,
            }
            for name, cls in CLIENT_REGISTRY.items()
        }
    )
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        _emit(SourcesConfigLoader(args.sources or sources_path(args.project)).plan())
    except ConnectionSourceError as exc:
        return _fail(exc)
    except OSError as exc:
        return _fail(SourcesConfigError(str(exc)))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    result = doctor(args.project)
    _emit(result)
    return 0 if result["ok"] else 2


def _cmd_env(args: argparse.Namespace) -> int:
    try:
        env = load_project_env(args.project)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"project": str(args.project), "env": redacted_view(env)})
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    try:
        _emit(api.resolve(args.project).to_dict())
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_ping(args: argparse.Namespace) -> int:
    try:
        identity = api.identify(args.project, args.source, timeout=args.timeout)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "ok": True, **identity.to_dict()})
    return 0


def _cmd_health(args: argparse.Namespace) -> int:
    try:
        report = check_project(
            args.project, only=args.source, limit=args.limit, timeout=args.timeout
        )
    except ConnectionSourceError as exc:
        return _fail(exc)

    _emit(report.to_dict())
    if args.out:
        print(f"report written: {write_report(report, args.out)}", file=sys.stderr)
    return 0 if all(c.ok for c in report.connections) else 5


def _split_extra_fields(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [f.strip() for f in raw.split(",") if f.strip()]


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        items = api.read(
            args.project,
            args.source,
            limit=args.limit,
            timeout=args.timeout,
            extra_fields=_split_extra_fields(args.extra_fields),
        )
    except ConnectionSourceError as exc:
        return _fail(exc)

    _emit(
        {
            "source": args.source,
            "item_count": len(items),
            "items": _serialize(items, include_raw=args.raw),
        }
    )
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    try:
        record = api.get(
            args.project,
            args.source,
            args.id,
            timeout=args.timeout,
            extra_fields=_split_extra_fields(args.extra_fields),
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "item": _serialize(record, include_raw=args.raw)})
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    try:
        record = api.create(
            args.project, args.source, timeout=args.timeout, **_parse_fields(args.field)
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "created": record.to_dict()})
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    try:
        record = api.update(
            args.project,
            args.source,
            args.id,
            timeout=args.timeout,
            **_parse_fields(args.field),
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "updated": record.to_dict()})
    return 0


def _cmd_transition(args: argparse.Namespace) -> int:
    try:
        record = api.transition(
            args.project, args.source, args.id, args.to, timeout=args.timeout
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "transitioned": record.to_dict()})
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit(
            {
                "source": args.source,
                "id": args.id,
                "deleted": False,
                "refused": "delete needs --confirm",
            }
        )
        return 1
    try:
        result = api.delete(
            args.project,
            args.source,
            args.id,
            permanent=args.permanent,
            timeout=args.timeout,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, **result})
    return 0


def _project_env(args: argparse.Namespace) -> dict[str, str]:
    """The project's .env merged with the OS env, or {} when no --project was given.

    Used only to pick up VAULT_TOKEN and GITLAB_SSH_KNOWN_HOSTS if a caller wants them
    sourced from a project's .env rather than the bare shell environment -- never
    required, since ssh_vault and gitlab_git both fall back to os.environ directly.
    """
    project = getattr(args, "project", None)
    if not project:
        return {}
    try:
        return load_project_env(project)
    except ConnectionSourceError:
        return {}


def _cmd_ssh_key_import(args: argparse.Namespace) -> int:
    try:
        result = ssh_vault.import_from_path(
            args.alias,
            args.path,
            force=args.force,
            vault_token=_project_env(args).get("VAULT_TOKEN"),
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_ssh_key_list(args: argparse.Namespace) -> int:
    try:
        aliases = ssh_vault.list_aliases(
            vault_token=_project_env(args).get("VAULT_TOKEN")
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"aliases": aliases})
    return 0


def _cmd_ssh_key_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit({"alias": args.alias, "deleted": False, "refused": "ssh-key-delete needs --confirm"})
        return 1
    try:
        ssh_vault.delete_key(
            args.alias, vault_token=_project_env(args).get("VAULT_TOKEN")
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"alias": args.alias, "deleted": True})
    return 0


def _cmd_ssh_key_set_passphrase(args: argparse.Namespace) -> int:
    import shutil
    import subprocess
    import tempfile

    vault_token = _project_env(args).get("VAULT_TOKEN")
    passphrase = getpass.getpass(f"passphrase to vault for alias {args.alias!r}: ")
    if not passphrase:
        _emit({"alias": args.alias, "stored": False, "refused": "empty passphrase"})
        return 1
    ssh_key_path = getattr(args, "ssh_key_path", None)
    if ssh_key_path:
        key_file = Path(ssh_key_path).expanduser()
        if not key_file.is_file():
            del passphrase
            return _fail(CredentialError(f"no such file: {key_file}"))
        key_bytes = key_file.read_bytes()
    else:
        try:
            key_bytes = ssh_vault.get_key(args.alias, vault_token=vault_token)
        except ConnectionSourceError as exc:
            del passphrase
            return _fail(exc)

    # Verify it actually decrypts this key BEFORE storing it -- refusing a wrong
    # passphrase here, loudly and immediately, is much better than silently
    # accepting it and only discovering the mismatch later during a real
    # pull/push (which used to just hang instead of failing clearly).
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen:
        fd, tmp_path = tempfile.mkstemp(prefix="alm-verify-")
        try:
            os.close(fd)
            Path(tmp_path).write_bytes(key_bytes)
            check = subprocess.run(
                [ssh_keygen, "-y", "-f", tmp_path, "-P", passphrase],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        if check.returncode != 0:
            del passphrase
            _emit({
                "alias": args.alias,
                "stored": False,
                "refused": "this passphrase does not decrypt the vaulted key for "
                f"{args.alias!r} -- not stored; retry with the correct passphrase",
            })
            return 3

    try:
        ssh_vault.store_passphrase(args.alias, passphrase, vault_token=vault_token)
    except ConnectionSourceError as exc:
        return _fail(exc)
    finally:
        del passphrase
    _emit({"alias": args.alias, "stored": True, "verified": bool(ssh_keygen)})
    return 0


def _cmd_ssh_key_clear_passphrase(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit(
            {"alias": args.alias, "cleared": False, "refused": "ssh-key-clear-passphrase needs --confirm"}
        )
        return 1
    try:
        ssh_vault.delete_passphrase(
            args.alias, vault_token=_project_env(args).get("VAULT_TOKEN")
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"alias": args.alias, "cleared": True})
    return 0


def _cmd_ssh_key_migrate(args: argparse.Namespace) -> int:
    from . import legacy_keyring_vault

    legacy_passphrase = _project_env(args).get("SSH_VAULT_PASSPHRASE")
    vault_token = _project_env(args).get("VAULT_TOKEN")

    if args.all:
        try:
            aliases = legacy_keyring_vault.list_aliases(vault_passphrase=legacy_passphrase)
        except ConnectionSourceError as exc:
            return _fail(exc)
    else:
        if not args.alias:
            _emit({"error": "ssh-key-migrate needs --alias <alias> or --all"})
            return 1
        aliases = [args.alias]

    results = []
    for alias in aliases:
        entry: dict[str, Any] = {"alias": alias}
        try:
            existing = ssh_vault.list_aliases(vault_token=vault_token)
            if alias in existing and not args.force:
                entry["migrated"] = False
                entry["reason"] = (
                    "already present in the new Vault -- pass --force to overwrite"
                )
                results.append(entry)
                continue
            key_bytes = legacy_keyring_vault.get_key(alias, vault_passphrase=legacy_passphrase)
            ssh_vault.store_key(alias, key_bytes, vault_token=vault_token)
            entry["migrated"] = True
            entry["bytes"] = len(key_bytes)
        except ConnectionSourceError as exc:
            entry["migrated"] = False
            entry["reason"] = exc.message
        results.append(entry)
    _emit({"results": results})
    return 0 if all(r.get("migrated") or "reason" in r for r in results) else 1


def _cmd_ssh_key_legacy_purge(args: argparse.Namespace) -> int:
    from . import legacy_keyring_vault

    if not args.confirm:
        _emit(
            {"alias": args.alias, "deleted": False, "refused": "ssh-key-legacy-purge needs --confirm"}
        )
        return 1
    try:
        legacy_keyring_vault.delete_key(
            args.alias, vault_passphrase=_project_env(args).get("SSH_VAULT_PASSPHRASE")
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"alias": args.alias, "deleted": True, "backend": "legacy-keyring"})
    return 0


def _optional_key_bytes(args: argparse.Namespace) -> bytes | None:
    """Read the private key as one base64 line from stdin when --ssh-key-stdin was
    given -- always the FIRST stdin line, ahead of whatever --passphrase-stdin reads
    next, so callers that pass both compose correctly. None means "nothing supplied
    this way", leaving gitlab_git.py's own --ssh-key-path / Vault-alias fallback in
    charge, exactly as before this flag existed.
    """
    if getattr(args, "ssh_key_stdin", False):
        line = sys.stdin.readline().rstrip("\r\n")
        try:
            return base64.b64decode(line, validate=True)
        except Exception as exc:
            raise ConnectionSourceError(f"--ssh-key-stdin line is not valid base64: {exc}") from exc
    if _key_from_env_active(args):
        try:
            return base64.b64decode(os.environ[_SSH_KEY_ENV].strip(), validate=True)
        except Exception as exc:
            raise ConnectionSourceError(f"{_SSH_KEY_ENV} is not valid base64: {exc}") from exc
    return None


_SSH_KEY_ENV = "GITLAB_SSH_PRIVATE_KEY_B64"
_SSH_PASSPHRASE_ENV = "GITLAB_SSH_KEY_PASSPHRASE"


def _key_from_env_active(args: argparse.Namespace) -> bool:
    """True when the sandbox-provisioned key is the only key source the caller left open.

    An explicit --ssh-key-stdin or --ssh-key-path always wins; on a laptop/CI
    deployment the variable is unset and the vaulted-alias path is untouched.
    """
    return (
        not getattr(args, "ssh_key_stdin", False)
        and not getattr(args, "ssh_key_path", None)
        and bool(os.environ.get(_SSH_KEY_ENV, "").strip())
    )


def _env_passphrase(args: argparse.Namespace) -> str:
    """The sandbox-provisioned passphrase, only when its matching key is the one in use."""
    return os.environ.get(_SSH_PASSPHRASE_ENV, "") if _key_from_env_active(args) else ""


def _optional_passphrase(args: argparse.Namespace) -> str | None:
    """None means "nothing supplied" -- gitlab_git then auto-fetches from Vault
    if it can. Unlike git-push, clone/pull have no required passphrase source,
    so both flags stay optional and the no-flag case keeps today's behaviour.
    """
    if args.prompt_passphrase:
        return getpass.getpass("SSH key passphrase: ")
    if getattr(args, "passphrase_stdin", False):
        return sys.stdin.readline().rstrip("\r\n") or _env_passphrase(args) or ""
    return _env_passphrase(args) or None


def _cmd_git_clone(args: argparse.Namespace) -> int:
    env = _project_env(args)
    key_bytes = _optional_key_bytes(args)
    passphrase = _optional_passphrase(args)
    try:
        result = gitlab_git.clone(
            args.repo_url,
            args.dest,
            ssh_key_alias=args.ssh_key_alias,
            ssh_key_path=args.ssh_key_path,
            key_bytes=key_bytes,
            ref=args.ref,
            known_hosts_path=args.known_hosts
            or env.get("GITLAB_SSH_KNOWN_HOSTS")
            or os.environ.get("GITLAB_SSH_KNOWN_HOSTS"),
            vault_token=env.get("VAULT_TOKEN"),
            passphrase=passphrase,
            timeout=args.timeout,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_git_pull(args: argparse.Namespace) -> int:
    env = _project_env(args)
    key_bytes = _optional_key_bytes(args)
    passphrase = _optional_passphrase(args)
    try:
        result = gitlab_git.pull(
            args.repo_dir,
            ssh_key_alias=args.ssh_key_alias,
            ssh_key_path=args.ssh_key_path,
            key_bytes=key_bytes,
            known_hosts_path=args.known_hosts
            or env.get("GITLAB_SSH_KNOWN_HOSTS")
            or os.environ.get("GITLAB_SSH_KNOWN_HOSTS"),
            vault_token=env.get("VAULT_TOKEN"),
            passphrase=passphrase,
            timeout=args.timeout,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_git_push(args: argparse.Namespace) -> int:
    key_bytes = _optional_key_bytes(args)
    if args.prompt_passphrase:
        passphrase = getpass.getpass("SSH push key passphrase: ")
    else:
        passphrase = sys.stdin.readline().rstrip("\r\n") or _env_passphrase(args)
    env = _project_env(args)
    try:
        result = gitlab_git.push(
            args.repo_dir,
            ssh_key_alias=args.ssh_key_alias,
            ssh_key_path=args.ssh_key_path,
            key_bytes=key_bytes,
            passphrase=passphrase,
            remote=args.remote,
            ref=args.ref,
            known_hosts_path=args.known_hosts
            or env.get("GITLAB_SSH_KNOWN_HOSTS")
            or os.environ.get("GITLAB_SSH_KNOWN_HOSTS"),
            vault_token=env.get("VAULT_TOKEN"),
            timeout=args.timeout,
            open_merge_request=args.open_merge_request,
            merge_request_target=args.merge_request_target,
            merge_request_title=args.merge_request_title,
            merge_request_description=args.merge_request_description,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_comment(args: argparse.Namespace) -> int:
    try:
        if args.source == "jira":
            with api.client(args.project, "jira", timeout=args.timeout) as jira:
                comment_id = jira.comment(args.id, args.text)
        elif args.source == "azure":
            with api.client(args.project, "azure", timeout=args.timeout) as az:
                scope = api.source_for(args.project, "azure").scope
                comment_id = az.comment(args.id, args.text, project=scope["project"])
        elif args.source == "gitlab":
            with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
                scope = api.source_for(args.project, "gitlab").scope
                comment_id = gl.comment(args.id, args.text, project_id=scope.get("project_id"))
        else:
            raise SourcesConfigError(f"comment is not supported for source {args.source!r}")
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": args.source, "id": args.id, "comment_id": comment_id, "commented": True})
    return 0


def _azure_relation(link_type: str) -> str:
    """The Azure DevOps relation for a `link --type`: 'Test' is Tested By, else a reference name."""
    if link_type.casefold() == "test":
        return azure_client.TESTED_BY_FORWARD
    if "." not in link_type:
        raise SourcesConfigError(
            f"Azure DevOps link type {link_type!r} is not a relation reference name",
            server="azuredevops",
            remediation="pass --type Test, or a reference name such as "
            "System.LinkTypes.Related or System.LinkTypes.Dependency-Forward",
        )
    return link_type


def _cmd_link(args: argparse.Namespace) -> int:
    try:
        if args.source == "jira":
            relation = args.type
            with api.client(args.project, "jira", timeout=args.timeout) as jira:
                jira.link_issues(args.to, args.id, link_type=args.type)
        elif args.source == "azure":
            relation = _azure_relation(args.type)
            with api.client(args.project, "azure", timeout=args.timeout) as az:
                az.link_work_items(args.to, args.id, relation=relation)
        else:
            raise SourcesConfigError(f"link is not supported for source {args.source!r}")
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": args.source,
            "outward": args.id,
            "inward": args.to,
            "type": args.type,
            "relation": relation,
            "linked": True,
        }
    )
    return 0


def _cmd_links(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            links = jira.issue_links(args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": "jira",
            "id": args.id,
            "link_count": len(links),
            "links": links,
        }
    )
    return 0


def _cmd_unlink(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            jira.delete_issue_link(args.link)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "link_id": args.link, "unlinked": True})
    return 0


# -- Xray: tier-aware reads and writes, on top of the Jira connection -------


def _guard_writes(project: str) -> None:
    if not api.writes_allowed(project):
        raise WriteBlockedError(
            "writes are disabled for this project (safety.read_only is true)",
            remediation="set safety.read_only to false in sources.json, deliberately",
        )


@contextmanager
def _open_xray(project: str, timeout: float):
    """The Jira connection for this project, alongside its detected Xray tier."""
    source = api.source_for(project, "jira")
    env = load_project_env(project)
    detail_fields = load_detail_fields(load_sync_config(project))
    with operations.open_client(source, env, timeout=timeout) as jira_client:
        probe = xray_mod.XrayServerVia(jira_client)
        tier = xray_mod.detect_tier(
            env,
            jira_probe=probe,
            field_ids=list(detail_fields.values()),
            jira_url=jira_client.base_url,
        )
        yield tier, jira_client, env, detail_fields, source


def _require_write_tier(tier: dict[str, Any]) -> None:
    if tier["tier"] not in ("cloud", "server"):
        raise SourcesConfigError(
            f"this project has no Xray write capability yet (tier={tier['tier']!r})",
            remediation=(
                "add XRAY_CLIENT_ID/XRAY_CLIENT_SECRET Vault references for Xray Cloud, or "
                "confirm the Xray Server/DC plugin answers on this Jira host"
            ),
        )


def _resolve_id(jira_client: Any, key: str) -> str:
    """The numeric issue id Xray Cloud mutations need, for a key the caller gave."""
    return jira_client.get(key).id


def _jira_project_key(source: SourceConfig) -> str:
    key = source.scope.get("project_key")
    if not key:
        raise SourcesConfigError(
            "this project's jira source has no project_key configured",
            remediation="add things.jira.project_key to sources.json",
        )
    return str(key)


def _require_in_project(source: SourceConfig, *keys: str) -> None:
    """Refuses any issue key outside this project's own configured scope.

    Xray's membership and run-status calls take no project parameter at all —
    nothing upstream stops a key from a different Jira project being passed in,
    so this is the one place that can catch it.
    """
    project_key = _jira_project_key(source)
    for key in keys:
        if not key:
            continue
        prefix = key.rsplit("-", 1)[0] if "-" in key else key
        if prefix.upper() != project_key.upper():
            raise SourcesConfigError(
                f"{key!r} is outside the configured project {project_key!r}",
                remediation="only issues already in this project's own scope can be used here",
            )


def _split_step(raw: str) -> dict[str, str]:
    parts = (raw.split("|", 2) + ["", "", ""])[:3]
    return {"action": parts[0].strip(), "data": parts[1].strip(), "result": parts[2].strip()}


def _cmd_xray_tier(args: argparse.Namespace) -> int:
    try:
        with _open_xray(args.project, args.timeout) as (tier, _jira, _env, _fields, _source):
            result = dict(tier)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"project": str(args.project), **result})
    return 0


def _xray_test_inputs(args: argparse.Namespace) -> tuple[str, str, str]:
    scenario = _scenario_of(args.scenario_file)
    summary = args.summary or scenario.get("title")
    if not summary:
        raise SourcesConfigError(
            "xray-test-create needs --summary or --scenario-file",
            remediation="pass --summary, or a scenario JSON that carries a title",
        )
    gherkin = args.gherkin
    if args.gherkin_file:
        try:
            gherkin = Path(args.gherkin_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise SourcesConfigError(
                f"cannot read --gherkin-file {args.gherkin_file!r}: {exc}",
                remediation="pass the path of the scenario's Gherkin text file",
            ) from exc
    if not gherkin and scenario.get("gherkin"):
        gherkin = str(scenario["gherkin"])
    test_type = args.type or ("Cucumber" if gherkin else None)
    if not test_type:
        raise SourcesConfigError(
            "xray-test-create needs --type",
            remediation="pass --type Manual, Cucumber or Generic",
        )
    return str(summary), test_type, gherkin or ""


def _cmd_xray_test_create(args: argparse.Namespace) -> int:
    try:
        summary, test_type, gherkin = _xray_test_inputs(args)
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            _require_in_project(source, *(args.precondition or []))
            steps = [_split_step(s) for s in (args.step or [])]
            if tier["tier"] == "cloud":
                precondition_ids = [_resolve_id(jira_client, k) for k in (args.precondition or [])]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    created = cloud.create_test(
                        _jira_project_key(source),
                        summary,
                        test_type=test_type,
                        steps=steps,
                        gherkin=gherkin,
                        unstructured=args.unstructured or "",
                        description=args.description or "",
                        precondition_issue_ids=precondition_ids,
                    )
                test = created.get("test") or {}
                result = {
                    "id": test.get("issueId"),
                    "key": (test.get("jira") or {}).get("key"),
                    "warnings": created.get("warnings") or [],
                }
            else:
                server = xray_mod.XrayServerVia(jira_client)
                created = server.create_test(
                    _jira_project_key(source),
                    summary,
                    test_type=test_type,
                    steps=steps,
                    gherkin=gherkin,
                    description=args.description or "",
                    field_test_type=detail_fields.get("test_type", ""),
                    field_manual_steps=detail_fields.get("manual_steps", ""),
                    field_cucumber_script=detail_fields.get("cucumber_script", ""),
                )
                result = {"key": created.get("key"), "id": created.get("id")}
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "tier": tier["tier"], "created": result})
    return 0


def _cmd_xray_test_update(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            if tier["tier"] != "cloud":
                raise SourcesConfigError(
                    f"xray-test-update needs the Cloud tier (this project is {tier['tier']!r})",
                    remediation=(
                        "on Xray Server/DC a test's shape lives in ordinary Jira custom "
                        "fields — use `alm-conn update --source jira` on those fields directly"
                    ),
                )
            _require_in_project(source, args.id)
            issue_id = _resolve_id(jira_client, args.id)
            updated: dict[str, Any] = {}
            with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                if args.type:
                    updated["test_type"] = cloud.update_test_type(issue_id, args.type)
                if args.gherkin is not None:
                    updated["gherkin"] = cloud.update_gherkin_test(issue_id, args.gherkin)
                if args.unstructured is not None:
                    updated["unstructured"] = cloud.update_unstructured_test(issue_id, args.unstructured)
            if not updated:
                raise SourcesConfigError(
                    "nothing to update", remediation="pass --type, --gherkin or --unstructured"
                )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "id": args.id, "updated": updated})
    return 0


def _cmd_xray_test_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit({"id": args.id, "deleted": False, "refused": "delete needs --confirm"})
        return 1
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            if tier["tier"] != "cloud":
                raise SourcesConfigError(
                    f"xray-test-delete needs the Cloud tier (this project is {tier['tier']!r})",
                    remediation="on Xray Server/DC use `alm-conn delete --source jira --id ... --confirm`",
                )
            _require_in_project(source, args.id)
            issue_id = _resolve_id(jira_client, args.id)
            with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                message = cloud.delete_test(issue_id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "id": args.id, "deleted": True, "message": message})
    return 0


def _cmd_xray_precondition_create(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            test_keys = args.test or []
            _require_in_project(source, *test_keys)
            if tier["tier"] == "cloud":
                test_ids = [_resolve_id(jira_client, k) for k in test_keys]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    created = cloud.create_precondition(
                        _jira_project_key(source),
                        args.summary,
                        precondition_type=args.type,
                        definition=args.definition or "",
                        test_issue_ids=test_ids,
                        description=args.description or "",
                    )
                precondition = created.get("precondition") or {}
                result = {
                    "id": precondition.get("issueId"),
                    "key": (precondition.get("jira") or {}).get("key"),
                    "warnings": created.get("warnings") or [],
                }
            else:
                server = xray_mod.XrayServerVia(jira_client)
                created = server.create_container(
                    _jira_project_key(source), args.summary, "Precondition", description=args.description or ""
                )
                key = created.get("key")
                definition_field = detail_fields.get("precondition", "")
                if args.definition and key and definition_field:
                    jira_client.update(key, **{definition_field: args.definition})
                result = {"key": key, "id": created.get("id")}
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "tier": tier["tier"], "created": result})
    return 0


def _cmd_xray_plan_create(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            test_keys = args.test or []
            _require_in_project(source, *test_keys)
            if tier["tier"] == "cloud":
                test_ids = [_resolve_id(jira_client, k) for k in test_keys]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    created = cloud.create_test_plan(
                        _jira_project_key(source),
                        args.summary,
                        test_issue_ids=test_ids,
                        description=args.description or "",
                    )
                plan = created.get("testPlan") or {}
                result = {
                    "id": plan.get("issueId"),
                    "key": (plan.get("jira") or {}).get("key"),
                    "warnings": created.get("warnings") or [],
                }
            else:
                server = xray_mod.XrayServerVia(jira_client)
                created = server.create_container(
                    _jira_project_key(source), args.summary, "Test Plan", description=args.description or ""
                )
                key = created.get("key")
                if test_keys and key:
                    server.add_tests_to_plan(key, test_keys)
                result = {"key": key, "id": created.get("id")}
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "tier": tier["tier"], "created": result})
    return 0


def _cmd_xray_set_create(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            test_keys = args.test or []
            _require_in_project(source, *test_keys)
            if tier["tier"] == "cloud":
                test_ids = [_resolve_id(jira_client, k) for k in test_keys]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    created = cloud.create_test_set(
                        _jira_project_key(source),
                        args.summary,
                        test_issue_ids=test_ids,
                        description=args.description or "",
                    )
                test_set = created.get("testSet") or {}
                result = {
                    "id": test_set.get("issueId"),
                    "key": (test_set.get("jira") or {}).get("key"),
                    "warnings": created.get("warnings") or [],
                }
            else:
                server = xray_mod.XrayServerVia(jira_client)
                created = server.create_container(
                    _jira_project_key(source), args.summary, "Test Set", description=args.description or ""
                )
                key = created.get("key")
                if test_keys and key:
                    server.add_tests_to_set(key, test_keys)
                result = {"key": key, "id": created.get("id")}
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "tier": tier["tier"], "created": result})
    return 0


def _cmd_xray_execution_create(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            test_keys = args.test or []
            environments = args.environment or []
            _require_in_project(source, *test_keys)
            if tier["tier"] == "cloud":
                test_ids = [_resolve_id(jira_client, k) for k in test_keys]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    created = cloud.create_test_execution(
                        _jira_project_key(source),
                        args.summary,
                        test_issue_ids=test_ids,
                        test_environments=environments,
                        description=args.description or "",
                    )
                execution = created.get("testExecution") or {}
                result = {
                    "id": execution.get("issueId"),
                    "key": (execution.get("jira") or {}).get("key"),
                    "warnings": created.get("warnings") or [],
                }
            else:
                server = xray_mod.XrayServerVia(jira_client)
                created = server.create_container(
                    _jira_project_key(source), args.summary, "Test Execution", description=args.description or ""
                )
                key = created.get("key")
                if test_keys and key:
                    server.add_tests_to_execution(key, test_keys)
                result = {"key": key, "id": created.get("id")}
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "tier": tier["tier"], "created": result})
    return 0


def _cmd_xray_add_test(args: argparse.Namespace) -> int:
    try:
        containers = [
            ("plan", args.plan),
            ("set", args.set),
            ("execution", args.execution),
            ("precondition", args.precondition),
        ]
        chosen = [(name, key) for name, key in containers if key]
        if len(chosen) != 1:
            raise SourcesConfigError(
                "pass exactly one of --plan, --set, --execution or --precondition"
            )
        container_kind, container_key = chosen[0]
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            _require_in_project(source, container_key, *args.id)
            action = "remove" if args.remove else "add"
            if tier["tier"] == "cloud":
                container_id = _resolve_id(jira_client, container_key)
                test_ids = [_resolve_id(jira_client, k) for k in args.id]
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    cloud_method = {
                        ("plan", "add"): cloud.add_tests_to_plan,
                        ("plan", "remove"): cloud.remove_tests_from_plan,
                        ("set", "add"): cloud.add_tests_to_set,
                        ("set", "remove"): cloud.remove_tests_from_set,
                        ("execution", "add"): cloud.add_tests_to_execution,
                        ("execution", "remove"): cloud.remove_tests_from_execution,
                        ("precondition", "add"): cloud.add_tests_to_precondition,
                        ("precondition", "remove"): cloud.remove_tests_from_precondition,
                    }[(container_kind, action)]
                    result = cloud_method(container_id, test_ids)
            else:
                if container_kind == "precondition":
                    raise SourcesConfigError(
                        "Xray Server/DC precondition membership isn't implemented here",
                        remediation="use the Jira UI, or a Test Set/Plan/Execution instead",
                    )
                server = xray_mod.XrayServerVia(jira_client)
                server_method = {
                    ("plan", "add"): server.add_tests_to_plan,
                    ("plan", "remove"): server.remove_tests_from_plan,
                    ("set", "add"): server.add_tests_to_set,
                    ("set", "remove"): server.remove_tests_from_set,
                    ("execution", "add"): server.add_tests_to_execution,
                }.get((container_kind, action))
                if server_method is None:
                    raise SourcesConfigError(
                        f"Xray Server/DC has no remove endpoint for a {container_kind}",
                        remediation="remove the membership from the Jira UI",
                    )
                result = server_method(container_key, args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": "jira",
            "tier": tier["tier"],
            container_kind: container_key,
            "tests": args.id,
            action: True,
            "result": result,
        }
    )
    return 0


def _cmd_xray_run_status(args: argparse.Namespace) -> int:
    try:
        _guard_writes(args.project)
        with _open_xray(args.project, args.timeout) as (tier, jira_client, env, detail_fields, source):
            _require_write_tier(tier)
            _require_in_project(source, args.execution, args.test)
            if tier["tier"] == "cloud":
                execution_id = _resolve_id(jira_client, args.execution)
                test_id = _resolve_id(jira_client, args.test)
                with xray_mod.XrayCloudClient.from_env(env, timeout=args.timeout) as cloud:
                    message = cloud.update_run_status(execution_id, test_id, args.status)
            else:
                server = xray_mod.XrayServerVia(jira_client)
                message = server.set_run_status(
                    args.execution, args.test, args.status, comment=args.comment or ""
                )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": "jira",
            "tier": tier["tier"],
            "execution": args.execution,
            "test": args.test,
            "status": args.status,
            "result": message,
        }
    )
    return 0


def _cmd_detail_field_list(args: argparse.Namespace) -> int:
    try:
        fields = list_detail_fields(args.project)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"project": str(args.project), "test_detail_fields": fields})
    return 0


def _cmd_detail_field_add(args: argparse.Namespace) -> int:
    try:
        result = add_detail_field(args.project, args.label, args.field)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_detail_field_remove(args: argparse.Namespace) -> int:
    try:
        result = remove_detail_field(args.project, args.label)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_pipelines(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            items = gl.pipelines(scope, limit=args.limit)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "pipeline_count": len(items), "pipelines": items})
    return 0


# A pipeline is done when its status can no longer change by itself.
_PIPELINE_TERMINAL = frozenset({"success", "failed", "canceled", "skipped"})


def _pipeline_web_url(gl: Any, scope: dict[str, Any], pipeline_id: int) -> str:
    project = str(scope.get("project_id") or scope.get("project") or "")
    return f"{gl.base_url}/{project}/-/pipelines/{pipeline_id}"


def _wait_for_pipeline(
    gl: Any,
    scope: dict[str, Any],
    pipeline_id: int,
    wait_timeout: float,
    poll_seconds: float,
) -> tuple[str, bool]:
    """Poll until the pipeline reaches a terminal status.

    Returns (status, timed_out); `timed_out` is true when the deadline passed
    while the pipeline was still running.
    """
    deadline = time.monotonic() + wait_timeout
    status = "unknown"
    while time.monotonic() < deadline:
        status = str(gl.pipeline(scope, pipeline_id).get("status") or "unknown")
        if status in _PIPELINE_TERMINAL:
            return status, False
        time.sleep(poll_seconds)
    return status, True


def _cmd_pipeline_run(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            variables = _parse_fields(args.variable) if args.variable else None
            created = gl.run_pipeline(scope, ref=args.ref, variables=variables)
            pipeline_id = created.get("id")
            if not pipeline_id:
                _emit(
                    {
                        "source": "gitlab",
                        "ref": args.ref,
                        "triggered": False,
                        "message": created.get("message")
                        or "GitLab accepted the run without returning a pipeline id",
                    }
                )
                return 5
            web_url = _pipeline_web_url(gl, scope, pipeline_id)
            status = str(created.get("status") or "created")
            if not args.wait:
                _emit(
                    {
                        "source": "gitlab",
                        "pipeline_id": pipeline_id,
                        "ref": args.ref,
                        "status": status,
                        "web_url": web_url,
                    }
                )
                return 0
            status, timed_out = _wait_for_pipeline(
                gl, scope, pipeline_id, args.wait_timeout, args.poll
            )
            result: dict[str, Any] = {
                "source": "gitlab",
                "pipeline_id": pipeline_id,
                "ref": args.ref,
                "status": status,
                "web_url": web_url,
            }
            if timed_out:
                result["error"] = "wait timed out before the pipeline finished"
                _emit(result)
                return 4
            _emit(result)
            return 0 if status == "success" else 5
    except ConnectionSourceError as exc:
        return _fail(exc)


def _cmd_pipeline_status(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            detail = gl.pipeline(scope, args.pipeline)
            web_url = _pipeline_web_url(gl, scope, args.pipeline)
            status = str(detail.get("status") or "unknown")
            if args.wait and status not in _PIPELINE_TERMINAL:
                status, timed_out = _wait_for_pipeline(
                    gl, scope, args.pipeline, args.wait_timeout, args.poll
                )
                if timed_out:
                    _emit(
                        {
                            "source": "gitlab",
                            "pipeline_id": args.pipeline,
                            "status": status,
                            "web_url": web_url,
                            "error": "wait timed out before the pipeline finished",
                        }
                    )
                    return 4
                detail = gl.pipeline(scope, args.pipeline)
            _emit(
                {
                    "source": "gitlab",
                    "pipeline": detail,
                    "web_url": web_url,
                }
            )
            return 0 if status == "success" else 5
    except ConnectionSourceError as exc:
        return _fail(exc)


def _cmd_pipeline_jobs(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            jobs = gl.pipeline_jobs(
                scope, args.pipeline, include_retried=not args.no_retried
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "pipeline_id": args.pipeline, "job_count": len(jobs), "jobs": jobs})
    return 0


def _cmd_job_status(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            detail = gl.job(scope, args.job)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "job": detail})
    return 0


def _cmd_job_logs(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            trace = gl.job_trace(scope, args.job)
    except ConnectionSourceError as exc:
        return _fail(exc)
    if args.out:
        _write_text(args.out, trace)
    lines = trace.splitlines()
    payload: dict[str, Any] = {
        "source": "gitlab",
        "job_id": args.job,
        "trace_bytes": len(trace),
        "line_count": len(lines),
        "trace": "\n".join(lines[-args.tail:]) if args.tail else trace,
    }
    if args.out:
        payload["written_to"] = args.out
    _emit(payload)
    return 0


def _write_text(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _cmd_job_retry(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            retried = gl.retry_job(scope, args.job)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "job_id": args.job, "retried": retried})
    return 0


def _cmd_job_cancel(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            canceled = gl.cancel_job(scope, args.job)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "job_id": args.job, "canceled": canceled})
    return 0


def _cmd_pipeline_retry(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            retried = gl.retry_pipeline(scope, args.pipeline)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "pipeline_id": args.pipeline, "retried": retried})
    return 0


def _cmd_pipeline_cancel(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            canceled = gl.cancel_pipeline(scope, args.pipeline)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "pipeline_id": args.pipeline, "canceled": canceled})
    return 0


def _cmd_jenkins_plugins_install(args: argparse.Namespace) -> int:
    plugin_ids = [p.strip() for p in args.plugins.split(",") if p.strip()]
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            before = jk.installed_plugins()
            missing = [p for p in plugin_ids if p not in before]
            if not missing:
                _emit({"source": "jenkins", "requested": plugin_ids, "already_installed": True})
                return 0
            jk.install_plugins(missing)
            jk.wait_for_plugin_installs(timeout=args.install_timeout)
            if not args.restart:
                _emit(
                    {
                        "source": "jenkins",
                        "requested": plugin_ids,
                        "installed": True,
                        "active": False,
                        "note": "installed but not yet active -- restart Jenkins to activate",
                    }
                )
                return 0
            jk.restart(safe=args.safe_restart)
            jk.wait_until_up(timeout=args.restart_timeout)
            after = jk.installed_plugins()
            still_missing = [p for p in plugin_ids if p not in after]
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": "jenkins",
            "requested": plugin_ids,
            "installed": not still_missing,
            "active": not still_missing,
            "still_missing": still_missing,
        }
    )
    return 0 if not still_missing else 5


def _cmd_jenkins_job_publish(args: argparse.Namespace) -> int:
    try:
        config_xml = Path(args.config_xml).read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(SourcesConfigError(f"cannot read --config-xml {args.config_xml!r}: {exc}"))
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            result = jk.publish_job(args.job_name, config_xml)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jenkins", **result})
    return 0


def _cmd_jenkins_pipeline_publish(args: argparse.Namespace) -> int:
    """Generate a Jenkinsfile-backed SCM Pipeline job's config.xml and publish it
    in one step -- the command usine.py's approve-ci-publish shells out to, so
    that command never has to generate or stage an XML file itself.
    """
    from .clients.jenkins import pipeline_scm_job_xml

    config_xml = pipeline_scm_job_xml(
        description=args.description or f"Talan usine: {args.job_name}",
        repo_url=args.repo_url,
        branch=args.branch,
        script_path=args.script_path,
        credentials_id=args.credentials_id or "",
    )
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            result = jk.publish_job(args.job_name, config_xml)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jenkins", "repo_url": args.repo_url, "branch": args.branch, **result})
    return 0


# A Jenkins build is done when it is no longer `building`.
def _wait_for_jenkins_build(
    jk: Any, job_name: str, number: int, wait_timeout: float, poll_seconds: float
) -> tuple[str, bool]:
    return jk.wait_for_build(job_name, number, timeout=wait_timeout, poll=poll_seconds)


def _cmd_jenkins_build_run(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            parameters = _parse_fields(args.param) if args.param else None
            triggered = jk.trigger_build(args.job_name, parameters=parameters)
            queue_url = triggered["queue_url"]
            if not args.wait:
                _emit({"source": "jenkins", "job_name": args.job_name, **triggered})
                return 0
            number = jk.wait_for_queued_build(queue_url, timeout=args.queue_timeout)
            status, timed_out = _wait_for_jenkins_build(
                jk, args.job_name, number, args.wait_timeout, args.poll
            )
            result: dict[str, Any] = {
                "source": "jenkins",
                "job_name": args.job_name,
                "build_number": number,
                "status": status,
                "url": f"{jk.base_url}/job/{args.job_name}/{number}/",
            }
            if timed_out:
                result["error"] = "wait timed out before the build finished"
                _emit(result)
                return 4
            _emit(result)
            return 0 if status == "SUCCESS" else 5
    except ConnectionSourceError as exc:
        return _fail(exc)


def _cmd_jenkins_build_status(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            status = str(jk.build_status(args.job_name, args.number).get("result") or "")
            building = bool(jk.build_status(args.job_name, args.number).get("building"))
            if args.wait and (building or not status):
                status, timed_out = _wait_for_jenkins_build(
                    jk, args.job_name, args.number, args.wait_timeout, args.poll
                )
                if timed_out:
                    _emit(
                        {
                            "source": "jenkins",
                            "job_name": args.job_name,
                            "build_number": args.number,
                            "status": status,
                            "error": "wait timed out before the build finished",
                        }
                    )
                    return 4
            detail = jk.build_status(args.job_name, args.number)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jenkins", "job_name": args.job_name, "build": detail})
    return 0 if str(detail.get("result") or "") in ("SUCCESS", "") else 5


def _cmd_jenkins_build_log(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jenkins", timeout=args.timeout) as jk:
            console = jk.build_console(args.job_name, args.number)
    except ConnectionSourceError as exc:
        return _fail(exc)
    if args.out:
        _write_text(args.out, console)
    lines = console.splitlines()
    payload: dict[str, Any] = {
        "source": "jenkins",
        "job_name": args.job_name,
        "build_number": args.number,
        "console_bytes": len(console),
        "line_count": len(lines),
        "console": "\n".join(lines[-args.tail:]) if args.tail else console,
    }
    if args.out:
        payload["written_to"] = args.out
    _emit(payload)
    return 0


def _extract_archive(data: bytes, out_dir: Path) -> list[str]:
    """Unpack an artifact archive, refusing any entry that escapes `out_dir`."""
    written: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for entry in archive.infolist():
            target = (out_dir / entry.filename).resolve()
            if not str(target).startswith(str(out_dir.resolve())):
                raise SourcesConfigError(
                    f"artifact entry escapes the output folder: {entry.filename!r}",
                    remediation="download into an empty folder and inspect the archive",
                )
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
            written.append(str(target))
    return written


def _cmd_artifacts(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            if args.job:
                data = gl.job_artifacts(scope, args.job)
                source = {"kind": "job", "id": args.job}
            else:
                data = gl.pipeline_artifacts(scope, args.pipeline)
                source = {"kind": "pipeline", "id": args.pipeline}
        if not data:
            _emit({**source, "downloaded": False, "message": "no artifacts were produced"})
            return 5
        files = _extract_archive(data, out_dir)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(
        {
            "source": "gitlab",
            **source,
            "out": str(out_dir),
            "file_count": len(files),
            "files": files,
        }
    )
    return 0


def _cmd_iterations(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            items = az.iterations(scope["project"], team=args.team)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "iterations": items})
    return 0


def _cmd_sprint_set_dates(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            node = az.set_iteration_dates(
                scope["project"], args.path, start=args.start, finish=args.finish
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "path": args.path, "attributes": node.get("attributes")})
    return 0


def _cmd_boards(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            scope = api.source_for(args.project, "jira").scope
            boards = jira.boards(scope["project_key"])
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "boards": boards})
    return 0


def _cmd_sprints(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprints = jira.sprints(args.board)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "board": args.board, "sprints": sprints})
    return 0


def _cmd_sprint_create(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprint = jira.create_sprint(
                args.board, args.name, start=args.start, end=args.end, goal=args.goal
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "created": sprint})
    return 0


def _cmd_sprint_start(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprint = jira.start_sprint(
                args.sprint, start=args.start, end=args.end, goal=args.goal
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "started": sprint})
    return 0


def _cmd_sprint_complete(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprint = jira.complete_sprint(args.sprint)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "completed": sprint})
    return 0


def _cmd_sprint_move(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            jira.move_to_sprint(args.sprint, args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "sprint": args.sprint, "moved": args.id})
    return 0


def _cmd_backlog_move(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            jira.move_to_backlog(args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "moved_to_backlog": args.id})
    return 0


def _cmd_sprint_get(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprint = jira.sprint(args.sprint)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "sprint": sprint})
    return 0


def _cmd_sprint_update(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            sprint = jira.update_sprint(args.sprint, **_parse_fields(args.field))
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "updated": sprint})
    return 0


def _cmd_sprint_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit({"source": "jira", "sprint": args.sprint, "deleted": False, "refused": "delete needs --confirm"})
        return 1
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            jira.delete_sprint(args.sprint)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "sprint": args.sprint, "deleted": True})
    return 0


def _cmd_sprint_issues(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            items = jira.sprint_issues(args.sprint)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "sprint": args.sprint, "item_count": len(items), "items": _serialize(items)})
    return 0


def _cmd_backlog_issues(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            items = jira.backlog_issues(args.board)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "board": args.board, "item_count": len(items), "items": _serialize(items)})
    return 0


def _cmd_board_issues(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "jira", timeout=args.timeout) as jira:
            items = jira.board_issues(args.board)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "jira", "board": args.board, "item_count": len(items), "items": _serialize(items)})
    return 0


def _cmd_test_plans(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            plans = az.test_plans(scope["project"])
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plans": plans})
    return 0


def _cmd_test_plan_create(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            plan = az.create_test_plan(
                scope["project"], args.name,
                area_path=args.area_path, iteration=args.iteration,
                start=args.start, end=args.end,
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "created": plan})
    return 0


def _cmd_test_plan_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit({"source": "azure", "plan": args.plan, "deleted": False, "refused": "delete needs --confirm"})
        return 1
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            az.delete_test_plan(scope["project"], args.plan)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plan": args.plan, "deleted": True})
    return 0


def _cmd_test_suites(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            suites = az.test_suites(scope["project"], args.plan)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plan": args.plan, "suites": suites})
    return 0


def _cmd_test_suite_create(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            suite = az.create_test_suite(
                scope["project"], args.plan, args.name, parent_suite_id=args.parent
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "created": suite})
    return 0


def _cmd_test_cases(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            cases = az.test_cases(scope["project"], args.plan, args.suite)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plan": args.plan, "suite": args.suite, "cases": cases})
    return 0


def _gherkin_text(args: argparse.Namespace) -> str | None:
    if args.gherkin_file:
        try:
            return Path(args.gherkin_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise SourcesConfigError(
                f"cannot read --gherkin-file {args.gherkin_file!r}: {exc}",
                remediation="pass the path of the scenario's Gherkin text file",
            ) from exc
    if args.gherkin:
        return args.gherkin.replace("\\n", "\n")
    return None


def _scenario_of(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        scenario = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourcesConfigError(
            f"cannot read --scenario-file {path!r}: {exc}",
            remediation="pass the path of one scenarios/*.json file written by test design",
        ) from exc
    if not isinstance(scenario, dict):
        raise SourcesConfigError(
            f"--scenario-file {path!r} is not a JSON object",
            remediation="pass the path of one scenarios/*.json file written by test design",
        )
    return scenario


def _cmd_test_case_create(args: argparse.Namespace) -> int:
    try:
        scenario = _scenario_of(args.scenario_file)
        title = args.title or scenario.get("title")
        if not title:
            raise SourcesConfigError(
                "test-case-create needs --title or --scenario-file",
                remediation="pass --title, or a scenario JSON that carries a title",
            )
        fields = _parse_fields(args.field)
        steps = json.loads(args.steps) if args.steps else None
        gherkin = _gherkin_text(args)
        if gherkin is None and scenario.get("gherkin"):
            gherkin = str(scenario["gherkin"])
        if gherkin is not None:
            steps = steps or azure_client.gherkin_test_steps(gherkin)
            fields.setdefault("description", azure_client.gherkin_html(gherkin))
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            record = az.create_test_case(scope, title=str(title), steps=steps, **fields)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "created": record.to_dict()})
    return 0


def _cmd_test_case_add(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            result = az.add_test_cases(scope["project"], args.plan, args.suite, args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plan": args.plan, "suite": args.suite, "added": args.id, "result": result})
    return 0


def _cmd_test_case_steps(args: argparse.Namespace) -> int:
    try:
        gherkin = _gherkin_text(args)
        if args.steps:
            steps = json.loads(args.steps)
        elif gherkin is not None:
            steps = azure_client.gherkin_test_steps(gherkin)
        else:
            raise SourcesConfigError(
                "test-case-steps needs --steps or --gherkin",
                remediation="pass --steps '[{\"action\":\"...\",\"expected\":\"...\"}]' or --gherkin-file",
            )
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            record = az.update_test_case_steps(args.id, steps)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "updated": record.to_dict()})
    return 0


def _cmd_test_points(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            points = az.test_points(scope["project"], args.plan, args.suite)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "plan": args.plan, "suite": args.suite, "points": points})
    return 0


def _cmd_test_point_outcome(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            result = az.update_test_point_outcome(
                scope["project"], args.plan, args.suite, args.id, args.outcome
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "updated": args.id, "outcome": args.outcome, "result": result})
    return 0


def _cmd_test_run_create(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            run = az.create_test_run(
                scope["project"], args.name, args.plan, point_ids=args.point
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "created": run})
    return 0


def _cmd_test_run_results(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            results = az.test_run_results(
                scope["project"], args.run, outcomes=args.outcomes, details=args.details
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "run": args.run, "results": results})
    return 0


def _cmd_test_run_results_update(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            result = az.update_test_results(scope["project"], args.run, json.loads(args.results))
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "run": args.run, "result": result})
    return 0


def _cmd_test_run_complete(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            run = az.complete_test_run(scope["project"], args.run, state=args.state)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "completed": run})
    return 0


def _cmd_test_results_by_build(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "azure", timeout=args.timeout) as az:
            scope = api.source_for(args.project, "azure").scope
            results = az.test_results_by_build(scope["project"], args.build)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "azure", "build": args.build, "results": results})
    return 0


def _cmd_milestones(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            items = gl.milestones(scope["project_id"], state=args.state)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "milestones": items})
    return 0


def _cmd_milestone_create(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            milestone = gl.create_milestone(
                scope["project_id"], args.title,
                description=args.description, start_date=args.start_date, due_date=args.due_date,
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "created": milestone})
    return 0


def _cmd_milestone_update(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            milestone = gl.update_milestone(
                scope["project_id"], args.milestone, **_parse_fields(args.field)
            )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "updated": milestone})
    return 0


def _cmd_milestone_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        _emit({"source": "gitlab", "milestone": args.milestone, "deleted": False, "refused": "delete needs --confirm"})
        return 1
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            gl.delete_milestone(scope["project_id"], args.milestone)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "milestone": args.milestone, "deleted": True})
    return 0


def _cmd_milestone_issues(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            items = gl.milestone_issues(scope["project_id"], args.milestone)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "milestone": args.milestone, "item_count": len(items), "items": _serialize(items)})
    return 0


def _cmd_milestone_move(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            record = gl.move_to_milestone(scope["project_id"], args.id, args.milestone)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "moved": record.to_dict()})
    return 0


def _cmd_milestone_remove(args: argparse.Namespace) -> int:
    try:
        with api.client(args.project, "gitlab", timeout=args.timeout) as gl:
            scope = api.source_for(args.project, "gitlab").scope
            record = gl.remove_from_milestone(scope["project_id"], args.id)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"source": "gitlab", "removed": record.to_dict()})
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        result = export_project(
            args.project,
            source=args.source,
            out=args.out,
            limit=args.limit,
            timeout=args.timeout,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        config = SourcesConfigLoader(args.sources or sources_path(args.project)).load()
        results_path = Path(args.results)
        if not results_path.exists():
            raise ReportError(f"results file not found: {results_path}")
        raw = json.loads(results_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ReportError("results must be a JSON list of connection results")
        report = ConnectionReportBuilder.from_results(
            config.project, raw, datetime.now(timezone.utc).isoformat()
        )
        out = ConnectionReportBuilder(config.project).write(report, args.out)
    except ConnectionSourceError as exc:
        return _fail(exc)
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(ReportError(str(exc)))
    _emit(report.to_dict())
    print(f"report written: {out}", file=sys.stderr)
    return 0


def _default_state_dir(project: str) -> Path:
    return sync_default_state_dir(project)


def _schedule_interval_minutes(project: str, override: int | None) -> int:
    """Minutes between runs: the flag, else the project's own polling interval.

    Rounded up rather than down, and never below one, so a sub-minute interval in
    `sources.json` cannot turn into a schedule that fires continuously.
    """
    if override is not None:
        return max(1, override)
    seconds = int(load_sync_config(project).get("interval_seconds", DEFAULT_INTERVAL))
    return max(1, -(-seconds // 60))


def _cmd_state_init(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else _default_state_dir(args.project)
    try:
        config = load_project(args.project)
        state = init_state(state_dir / "state.json", project_id=config.project, force=args.force)
        schedule = (
            schedule_install(
                args.project,
                state_dir=state_dir,
                interval_minutes=_schedule_interval_minutes(args.project, args.every_minutes),
                source=args.source,
                backend=args.backend,
            )
            if args.schedule
            else None
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit({"state": state, "schedule": schedule})
    return 0


def _cmd_schedule(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else _default_state_dir(args.project)
    try:
        if args.action == "install":
            result = schedule_install(
                args.project,
                state_dir=state_dir,
                interval_minutes=_schedule_interval_minutes(args.project, args.every_minutes),
                source=args.source,
                backend=args.backend,
                graph=args.graph,
                graph_prune=args.graph_prune,
                graph_state=args.graph_state,
            )
        elif args.action == "remove":
            result = schedule_remove(args.project, state_dir=state_dir, backend=args.backend)
        elif args.action in {"start", "stop"}:
            result = schedule_set_enabled(
                args.project, args.action == "start", state_dir=state_dir, backend=args.backend
            )
        elif args.action == "run-now":
            result = schedule_run_now(args.project, state_dir=state_dir, backend=args.backend)
        else:
            result = schedule_status(args.project, state_dir=state_dir, backend=args.backend)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result)
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir) if args.state_dir else _default_state_dir(args.project)

    def report(result: SyncResult) -> None:
        _emit(result.to_dict())

    try:
        if args.watch:
            count = sync_watch(
                args.project,
                state_dir=state_dir,
                interval=args.interval,
                max_cycles=args.max_cycles,
                on_result=report,
                source=args.source,
                limit=args.limit,
                timeout=args.timeout,
                dry_run=args.dry_run,
                force_full=args.force_full,
                emit_actions=not args.no_actions,
            )
            print(f"sync: ran {count} cycle(s)", file=sys.stderr)
            return 0

        result = sync_once(
            args.project,
            state_dir=state_dir,
            source=args.source,
            limit=args.limit,
            timeout=args.timeout,
            dry_run=args.dry_run,
            force_full=args.force_full,
            emit_actions=not args.no_actions,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(result.to_dict())
    return 0


def _cmd_graph_load(args: argparse.Namespace) -> int:
    try:
        result, batch = graph.run_build(
            args.project,
            sources=[args.source] if args.source else None,
            limit=args.limit,
            timeout=args.timeout,
            jql=args.jql,
            dry_run=args.dry_run,
            prune=args.prune,
            run_derivations=not args.no_derive,
            batch_size=args.batch_size,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    if args.out:
        _write_json(args.out, batch.to_dict())
    if args.state:
        _write_json(args.state, graph.state_of(result, batch))
    _emit(result.to_dict())
    return 0


def _cmd_graph_state(args: argparse.Namespace) -> int:
    """The whole project as one JSON document, read straight from the trackers.

    Deliberately a dry run: the state is a projection of what was read, so producing
    it needs no database at all. That makes it the one graph command that works on a
    machine with no Neo4j — which is most machines, most of the time.
    """
    try:
        result, batch = graph.run_build(
            args.project,
            sources=[args.source] if args.source else None,
            limit=args.limit,
            timeout=args.timeout,
            jql=args.jql,
            dry_run=True,
            run_derivations=False,
        )
    except ConnectionSourceError as exc:
        return _fail(exc)

    state = graph.state_of(result, batch)
    if args.out:
        _write_json(args.out, state)
    _emit({k: state[k] for k in _STATE_SUMMARY if k in state} if args.summary else state)
    return 0


def _cmd_graph_doctor(args: argparse.Namespace) -> int:
    try:
        env = load_project_env(args.project)
    except ConnectionSourceError as exc:
        return _fail(exc)
    config = graph.load_graph_config(args.project, env)
    report: dict[str, Any] = {"project": args.project, "config": config.redacted()}

    try:
        config.require_credentials()
    except ConnectionSourceError as exc:
        report["neo4j"] = {"ok": False, **exc.to_dict()}
    else:
        try:
            with graph.open_session(config) as session:
                session.run("RETURN 1")
        except ConnectionSourceError as exc:
            report["neo4j"] = {"ok": False, **exc.to_dict()}
        else:
            report["neo4j"] = {"ok": True, "uri": config.uri, "database": config.database}

    report["xray_tier"] = graph.detect_tier(env)
    _emit(report)
    return 0


def _graph_query_site(system: str, env: Mapping[str, str]) -> str:
    """The site a `--system` resolves to, mirroring how each builder derives it."""
    if system == "azuredevops":
        org = env.get("AZURE_DEVOPS_ORG", "")
        return graph.site_of(f"https://dev.azure.com/{org}" if org else "")
    if system == "gitlab":
        return graph.site_of(env.get("GITLAB_URL", ""))
    if system == "confluence":
        return graph.site_of(env.get("CONFLUENCE_URL") or env.get("JIRA_URL", ""))
    return graph.site_of(env.get("JIRA_URL", ""))


def _cmd_graph_query(args: argparse.Namespace) -> int:
    if args.list or not args.name:
        _emit(graph.query_catalogue())
        return 0
    if not args.project:
        print(json.dumps({"error": "pass --project"}), file=sys.stderr)
        return 1

    try:
        env = load_project_env(args.project)
    except ConnectionSourceError as exc:
        return _fail(exc)
    config = graph.load_graph_config(args.project, env)
    system = args.system or graph.SYSTEM
    site = args.site or _graph_query_site(system, env)
    if args.scope:
        # Same platform folding as graph-load (runner.run_build), or a scoped query
        # inside a sandbox builds a prefix no node was written under.
        prefix = graph.Namespace.make(
            system, site, args.scope, platform=os.environ.get("PROJECT_ID") or None
        ).prefix
    else:
        prefix = f"{system}:{site}:"

    params: dict[str, Any] = {"limit": args.limit}
    if args.version is not None:
        params["version"] = args.version
    for item in args.param or ():
        if "=" not in item:
            print(json.dumps({"error": f"--param expects key=value, got {item!r}"}), file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        params[key.strip()] = _parse_param_value(value)
    try:
        statement, query_params = graph.build_query(args.name, prefix, **params)
    except KeyError as exc:
        return _fail(SourcesConfigError(str(exc)))

    try:
        with graph.open_session(config) as session:
            run = graph.session_runner(session)
            rows = run(statement, query_params)
    except ConnectionSourceError as exc:
        return _fail(exc)
    _emit(rows)
    return 0


def _cmd_graph_labels(args: argparse.Namespace) -> int:
    _emit({"labels": sorted(graph.LABELS)})
    return 0


def _cmd_graph_relationships(args: argparse.Namespace) -> int:
    _emit({"relationships": sorted(graph.RELATIONSHIPS)})
    return 0


def _cmd_graph_alias_list(args: argparse.Namespace) -> int:
    try:
        _emit(graph.list_issue_type_aliases(args.project))
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_graph_alias_add(args: argparse.Namespace) -> int:
    try:
        _emit(graph.add_issue_type_alias(args.project, args.type_name, args.label))
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_graph_alias_remove(args: argparse.Namespace) -> int:
    try:
        _emit(graph.remove_issue_type_alias(args.project, args.type_name))
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_graph_link_alias_list(args: argparse.Namespace) -> int:
    try:
        _emit(graph.list_link_type_aliases(args.project))
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_graph_link_alias_add(args: argparse.Namespace) -> int:
    try:
        _emit(
            graph.add_link_type_alias(
                args.project, args.link_type_name, args.relationship, reverse=args.reverse
            )
        )
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def _cmd_graph_link_alias_remove(args: argparse.Namespace) -> int:
    try:
        _emit(graph.remove_link_type_alias(args.project, args.link_type_name))
    except ConnectionSourceError as exc:
        return _fail(exc)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alm-conn",
        description="ALM connection sources: Jira, Azure DevOps, GitLab over REST.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def project_arg(p: argparse.ArgumentParser, required: bool = True) -> None:
        p.add_argument(
            "--project",
            required=required,
            help="Client project folder, or its name under clients/<client>/ or "
            "Talan_Factory/Talan_usine_config/",
        )

    def timeout_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    def source_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument("--source", required=True)

    def field_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--field",
            action="append",
            default=[],
            metavar="KEY=VALUE",
            help="Field to set; repeat per field",
        )

    def gherkin_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--gherkin",
            help="Gherkin scenario turned into steps; a literal \\n is a line break",
        )
        p.add_argument(
            "--gherkin-file", help="Path to a text file holding the Gherkin scenario"
        )

    sub.add_parser("systems", help="Print the supported systems and what they need.")

    p_plan = sub.add_parser("plan", help="Print the connection plan.")
    project_arg(p_plan, required=False)
    p_plan.add_argument("--sources", help="Explicit path to sources.json")

    project_arg(sub.add_parser("doctor", help="Offline preflight."))
    project_arg(sub.add_parser("env", help="Show credentials, redacted."))
    project_arg(sub.add_parser("resolve", help="What this project connects to (offline)."))

    p_ping = sub.add_parser("ping", help="Prove one source's credential works.")
    project_arg(p_ping)
    source_arg(p_ping)
    timeout_arg(p_ping)

    p_health = sub.add_parser("health", help="Probe every enabled source.")
    project_arg(p_health)
    p_health.add_argument("--source")
    p_health.add_argument("--limit", type=int, default=5)
    p_health.add_argument("--out")
    timeout_arg(p_health)

    p_fetch = sub.add_parser("fetch", help="Read records from one source.")
    project_arg(p_fetch)
    source_arg(p_fetch)
    p_fetch.add_argument("--limit", type=int, default=100)
    p_fetch.add_argument("--raw", action="store_true", help="Include the untouched payload")
    p_fetch.add_argument(
        "--extra-fields",
        help="Comma-separated custom field ids to also fetch (jira only, e.g. "
        "customfield_12345); values land in the raw payload, so combine with --raw",
    )
    timeout_arg(p_fetch)

    p_get = sub.add_parser(
        "get", help="Read exactly one item by id/key — for verifying a write."
    )
    project_arg(p_get)
    source_arg(p_get)
    p_get.add_argument("--id", required=True)
    p_get.add_argument("--raw", action="store_true", help="Include the untouched payload")
    p_get.add_argument(
        "--extra-fields",
        help="Comma-separated custom field ids to also fetch (jira only, e.g. "
        "customfield_12345); values land in the raw payload, so combine with --raw",
    )
    timeout_arg(p_get)

    p_create = sub.add_parser("create", help="Create an issue or work item.")
    project_arg(p_create)
    source_arg(p_create)
    field_arg(p_create)
    timeout_arg(p_create)

    p_update = sub.add_parser("update", help="Change fields on one item.")
    project_arg(p_update)
    source_arg(p_update)
    p_update.add_argument("--id", required=True, help="Issue key or work item id")
    field_arg(p_update)
    timeout_arg(p_update)

    p_transition = sub.add_parser("transition", help="Move one item to a new status.")
    project_arg(p_transition)
    source_arg(p_transition)
    p_transition.add_argument("--id", required=True)
    p_transition.add_argument("--to", required=True, help="Target status name")
    timeout_arg(p_transition)

    p_delete = sub.add_parser("delete", help="Delete one item. Needs --confirm.")
    project_arg(p_delete)
    source_arg(p_delete)
    p_delete.add_argument("--id", required=True)
    p_delete.add_argument("--confirm", action="store_true", help="Required to proceed")
    p_delete.add_argument(
        "--permanent",
        action="store_true",
        help="Azure DevOps: destroy instead of sending to the recycle bin",
    )
    timeout_arg(p_delete)

    p_comment = sub.add_parser("comment", help="Add a comment/note to one item.")
    project_arg(p_comment)
    source_arg(p_comment)
    p_comment.add_argument("--id", required=True)
    p_comment.add_argument("--text", required=True, help="Comment body")
    timeout_arg(p_comment)

    p_link = sub.add_parser(
        "link",
        help="Create one link. For the 'Test' type, --id is the test (the outward "
        "side) and --to is what it tests. On Azure DevOps 'Test' becomes the "
        "story's Tested By relation; any other --type is a relation reference name.",
    )
    project_arg(p_link)
    p_link.add_argument(
        "--source", choices=("jira", "azure"), default="jira",
        help="Tracker to link in; default 'jira'",
    )
    p_link.add_argument("--id", required=True, help="The outward side of the link")
    p_link.add_argument("--to", required=True, help="The inward side of the link")
    p_link.add_argument("--type", default="Test", help="Link type name; default 'Test'")
    timeout_arg(p_link)

    p_links = sub.add_parser("links", help="Jira: list one issue's issue links.")
    project_arg(p_links)
    p_links.add_argument("--id", required=True)
    timeout_arg(p_links)

    p_unlink = sub.add_parser(
        "unlink", help="Jira: remove one issue link by its id (see `links`)."
    )
    project_arg(p_unlink)
    p_unlink.add_argument("--link", required=True, help="Issue link id to delete")
    timeout_arg(p_unlink)

    p_xray_tier = sub.add_parser(
        "xray-tier",
        help="Report which Xray tier this project's Jira has: cloud, server, fields or none.",
    )
    project_arg(p_xray_tier)
    timeout_arg(p_xray_tier)

    def xray_test_shape_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--type", choices=["Manual", "Cucumber", "Generic"], help="Required unless a Gherkin source implies Cucumber")
        p.add_argument(
            "--step",
            action="append",
            default=[],
            metavar="ACTION|DATA|RESULT",
            help="One manual step; repeat per step",
        )
        p.add_argument("--gherkin", help="Gherkin text for a Cucumber test")
        p.add_argument("--gherkin-file", help="Read the Gherkin from this text file")
        p.add_argument("--scenario-file", help="Read title and Gherkin from a test-design scenario JSON")
        p.add_argument("--unstructured", help="Free-text definition for a Generic test")

    p_test_create = sub.add_parser(
        "xray-test-create", help="Create a Manual, Cucumber or Generic Test issue."
    )
    project_arg(p_test_create)
    p_test_create.add_argument("--summary", help="Required unless --scenario-file carries a title")
    xray_test_shape_args(p_test_create)
    p_test_create.add_argument(
        "--precondition",
        action="append",
        default=[],
        metavar="KEY",
        help="Precondition issue key to associate; repeat per precondition",
    )
    p_test_create.add_argument("--description", default="")
    timeout_arg(p_test_create)

    p_test_update = sub.add_parser(
        "xray-test-update",
        help="Change a Test's type, Gherkin or unstructured definition. Cloud tier only.",
    )
    project_arg(p_test_update)
    p_test_update.add_argument("--id", required=True, help="Test issue key")
    p_test_update.add_argument("--type", choices=["Manual", "Cucumber", "Generic"])
    p_test_update.add_argument("--gherkin")
    p_test_update.add_argument("--unstructured")
    timeout_arg(p_test_update)

    p_test_delete = sub.add_parser(
        "xray-test-delete", help="Delete a Test issue. Needs --confirm. Cloud tier only."
    )
    project_arg(p_test_delete)
    p_test_delete.add_argument("--id", required=True)
    p_test_delete.add_argument("--confirm", action="store_true", help="Required to proceed")
    timeout_arg(p_test_delete)

    p_precondition_create = sub.add_parser(
        "xray-precondition-create", help="Create a Precondition issue."
    )
    project_arg(p_precondition_create)
    p_precondition_create.add_argument("--summary", required=True)
    p_precondition_create.add_argument(
        "--type", default="Generic", help="Precondition type name; default 'Generic'"
    )
    p_precondition_create.add_argument("--definition", default="")
    p_precondition_create.add_argument(
        "--test",
        action="append",
        default=[],
        metavar="KEY",
        help="Test issue key to associate; repeat per test",
    )
    p_precondition_create.add_argument("--description", default="")
    timeout_arg(p_precondition_create)

    def xray_container_create_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--summary", required=True)
        p.add_argument(
            "--test",
            action="append",
            default=[],
            metavar="KEY",
            help="Test issue key to include; repeat per test",
        )
        p.add_argument("--description", default="")

    p_plan_create = sub.add_parser("xray-plan-create", help="Create a Test Plan issue.")
    project_arg(p_plan_create)
    xray_container_create_args(p_plan_create)
    timeout_arg(p_plan_create)

    p_set_create = sub.add_parser("xray-set-create", help="Create a Test Set issue.")
    project_arg(p_set_create)
    xray_container_create_args(p_set_create)
    timeout_arg(p_set_create)

    p_execution_create = sub.add_parser(
        "xray-execution-create", help="Create a Test Execution issue."
    )
    project_arg(p_execution_create)
    xray_container_create_args(p_execution_create)
    p_execution_create.add_argument(
        "--environment",
        action="append",
        default=[],
        metavar="NAME",
        help="Test environment name (Cloud tier); repeat per environment",
    )
    timeout_arg(p_execution_create)

    p_add_test = sub.add_parser(
        "xray-add-test",
        help="Add (or with --remove, remove) one or more tests to a Test Plan, Test "
        "Set, Test Execution or Precondition.",
    )
    project_arg(p_add_test)
    p_add_test.add_argument(
        "--id", action="append", required=True, metavar="KEY", help="Test issue key; repeat per test"
    )
    p_add_test.add_argument("--plan", metavar="KEY", help="Test Plan issue key")
    p_add_test.add_argument("--set", metavar="KEY", help="Test Set issue key")
    p_add_test.add_argument("--execution", metavar="KEY", help="Test Execution issue key")
    p_add_test.add_argument("--precondition", metavar="KEY", help="Precondition issue key")
    p_add_test.add_argument("--remove", action="store_true", help="Remove instead of add")
    timeout_arg(p_add_test)

    p_run_status = sub.add_parser(
        "xray-run-status", help="Set one test's result inside one execution."
    )
    project_arg(p_run_status)
    p_run_status.add_argument("--execution", required=True, metavar="KEY")
    p_run_status.add_argument("--test", required=True, metavar="KEY")
    p_run_status.add_argument(
        "--status", required=True, help="e.g. PASSED, FAILED, TODO (Cloud) or PASS, FAIL, TODO (Server)"
    )
    p_run_status.add_argument("--comment", default="")
    timeout_arg(p_run_status)

    p_detail_field_list = sub.add_parser(
        "detail-field-list",
        help="Show this project's own sync.test_detail_fields (label -> customfield id) map.",
    )
    project_arg(p_detail_field_list)

    p_detail_field_add = sub.add_parser(
        "detail-field-add",
        help="Declare which custom field id this Jira site uses for a test-detail label "
        "(e.g. test_type, test_steps, precondition, expected_result, or any name of your own).",
    )
    project_arg(p_detail_field_add)
    p_detail_field_add.add_argument("--label", required=True, help="Your name for the field, e.g. test_type")
    p_detail_field_add.add_argument("--field", required=True, help="This site's field id, e.g. customfield_10167")

    p_detail_field_remove = sub.add_parser(
        "detail-field-remove", help="Stop watching a test-detail label declared by detail-field-add."
    )
    project_arg(p_detail_field_remove)
    p_detail_field_remove.add_argument("--label", required=True)

    p_pipelines = sub.add_parser("pipelines", help="GitLab: list a project's CI pipelines.")
    project_arg(p_pipelines)
    p_pipelines.add_argument("--limit", type=int, default=20)
    timeout_arg(p_pipelines)

    def wait_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--wait",
            action="store_true",
            help="Poll until the pipeline reaches a terminal status, then exit "
            "0 on success and 5 on failure/cancel",
        )
        p.add_argument(
            "--wait-timeout",
            type=float,
            default=900.0,
            help="Seconds to keep polling under --wait (default 900)",
        )
        p.add_argument(
            "--poll", type=float, default=10.0, help="Seconds between polls (default 10)"
        )

    p_pipeline_run = sub.add_parser("pipeline-run", help="GitLab: start a pipeline on a ref.")
    project_arg(p_pipeline_run)
    p_pipeline_run.add_argument("--ref", default="main", help="Branch or tag to run (default main)")
    p_pipeline_run.add_argument(
        "--variable",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="CI variable to set on the run; repeat per variable",
    )
    wait_args(p_pipeline_run)
    timeout_arg(p_pipeline_run)

    p_pipeline_status = sub.add_parser(
        "pipeline-status", help="GitLab: get one pipeline's status and details."
    )
    project_arg(p_pipeline_status)
    p_pipeline_status.add_argument("--pipeline", required=True, type=int)
    wait_args(p_pipeline_status)
    timeout_arg(p_pipeline_status)

    p_pipeline_jobs = sub.add_parser(
        "pipeline-jobs", help="GitLab: list the jobs of one pipeline with their status."
    )
    project_arg(p_pipeline_jobs)
    p_pipeline_jobs.add_argument("--pipeline", required=True, type=int)
    p_pipeline_jobs.add_argument(
        "--no-retried",
        action="store_true",
        help="Hide retried jobs (default shows every attempt)",
    )
    timeout_arg(p_pipeline_jobs)

    p_job_status = sub.add_parser("job-status", help="GitLab: get one job's status and details.")
    project_arg(p_job_status)
    p_job_status.add_argument("--job", required=True, type=int)
    timeout_arg(p_job_status)

    p_job_logs = sub.add_parser("job-logs", help="GitLab: fetch one job's trace (the log).")
    project_arg(p_job_logs)
    p_job_logs.add_argument("--job", required=True, type=int)
    p_job_logs.add_argument("--tail", type=int, help="Keep only the last N lines of the trace")
    p_job_logs.add_argument("--out", help="Also write the raw trace to this file")
    timeout_arg(p_job_logs)

    p_job_retry = sub.add_parser("job-retry", help="GitLab: rerun one failed job.")
    project_arg(p_job_retry)
    p_job_retry.add_argument("--job", required=True, type=int)
    timeout_arg(p_job_retry)

    p_job_cancel = sub.add_parser("job-cancel", help="GitLab: cancel one running job.")
    project_arg(p_job_cancel)
    p_job_cancel.add_argument("--job", required=True, type=int)
    timeout_arg(p_job_cancel)

    p_pipeline_retry = sub.add_parser("pipeline-retry", help="GitLab: rerun a pipeline's failed jobs.")
    project_arg(p_pipeline_retry)
    p_pipeline_retry.add_argument("--pipeline", required=True, type=int)
    timeout_arg(p_pipeline_retry)

    p_pipeline_cancel = sub.add_parser("pipeline-cancel", help="GitLab: cancel a running pipeline.")
    project_arg(p_pipeline_cancel)
    p_pipeline_cancel.add_argument("--pipeline", required=True, type=int)
    timeout_arg(p_pipeline_cancel)

    p_jenkins_plugins = sub.add_parser(
        "jenkins-plugins-install", help="Jenkins: install plugins (and their deps), then restart to activate."
    )
    project_arg(p_jenkins_plugins)
    p_jenkins_plugins.add_argument(
        "--plugins", required=True, help="Comma-separated plugin short names, e.g. git,workflow-aggregator"
    )
    p_jenkins_plugins.add_argument(
        "--restart", dest="restart", action="store_true", default=True,
        help="Restart Jenkins once installs finish, so the plugins actually activate (default on)",
    )
    p_jenkins_plugins.add_argument("--no-restart", dest="restart", action="store_false")
    p_jenkins_plugins.add_argument(
        "--safe-restart", dest="safe_restart", action="store_true", default=True,
        help="Wait for running builds to finish before restarting (default on)",
    )
    p_jenkins_plugins.add_argument("--no-safe-restart", dest="safe_restart", action="store_false")
    p_jenkins_plugins.add_argument("--install-timeout", type=float, default=300.0)
    p_jenkins_plugins.add_argument("--restart-timeout", type=float, default=180.0)
    timeout_arg(p_jenkins_plugins)

    p_jenkins_publish = sub.add_parser(
        "jenkins-job-publish", help="Jenkins: create or update a job from a config.xml file."
    )
    project_arg(p_jenkins_publish)
    p_jenkins_publish.add_argument("--job-name", required=True)
    p_jenkins_publish.add_argument(
        "--config-xml", required=True, help="Path to the job's config.xml (see clients/jenkins.py's "
        "pipeline_scm_job_xml/freestyle_shell_job_xml for how to generate one)",
    )
    timeout_arg(p_jenkins_publish)

    p_jenkins_pipeline_publish = sub.add_parser(
        "jenkins-pipeline-publish",
        help="Jenkins: generate a Jenkinsfile-backed SCM Pipeline job's config.xml and publish it.",
    )
    project_arg(p_jenkins_pipeline_publish)
    p_jenkins_pipeline_publish.add_argument("--job-name", required=True)
    p_jenkins_pipeline_publish.add_argument("--repo-url", required=True)
    p_jenkins_pipeline_publish.add_argument("--branch", required=True, help="e.g. main, or */main")
    p_jenkins_pipeline_publish.add_argument("--script-path", default="Jenkinsfile")
    p_jenkins_pipeline_publish.add_argument(
        "--credentials-id", default="",
        help="Jenkins credential id for cloning a private repo (configured by a human in "
        "Jenkins -> Credentials beforehand); omit for a publicly-clonable repo",
    )
    p_jenkins_pipeline_publish.add_argument("--description", default="")
    timeout_arg(p_jenkins_pipeline_publish)

    p_jenkins_build_run = sub.add_parser("jenkins-build-run", help="Jenkins: trigger a build.")
    project_arg(p_jenkins_build_run)
    p_jenkins_build_run.add_argument("--job-name", required=True)
    p_jenkins_build_run.add_argument(
        "--param", action="append", default=[], metavar="KEY=VALUE",
        help="Build parameter to set on the run; repeat per parameter",
    )
    wait_args(p_jenkins_build_run)
    p_jenkins_build_run.add_argument(
        "--queue-timeout", type=float, default=60.0,
        help="Seconds to wait for Jenkins to assign a build number (default 60)",
    )
    timeout_arg(p_jenkins_build_run)

    p_jenkins_build_status = sub.add_parser(
        "jenkins-build-status", help="Jenkins: get one build's status and details."
    )
    project_arg(p_jenkins_build_status)
    p_jenkins_build_status.add_argument("--job-name", required=True)
    p_jenkins_build_status.add_argument("--number", required=True, type=int)
    wait_args(p_jenkins_build_status)
    timeout_arg(p_jenkins_build_status)

    p_jenkins_build_log = sub.add_parser(
        "jenkins-build-log", help="Jenkins: fetch one build's console log."
    )
    project_arg(p_jenkins_build_log)
    p_jenkins_build_log.add_argument("--job-name", required=True)
    p_jenkins_build_log.add_argument("--number", required=True, type=int)
    p_jenkins_build_log.add_argument("--tail", type=int, help="Keep only the last N lines")
    p_jenkins_build_log.add_argument("--out", help="Also write the raw console log to this file")
    timeout_arg(p_jenkins_build_log)

    p_artifacts = sub.add_parser(
        "artifacts", help="GitLab: download and unpack a job's or a pipeline's artifacts."
    )
    project_arg(p_artifacts)
    p_artifacts.add_argument(
        "--pipeline", type=int, help="Pipeline id; downloads the combined archive"
    )
    p_artifacts.add_argument("--job", type=int, help="Job id; downloads that job's archive")
    p_artifacts.add_argument("--out", required=True, help="Folder to unpack into")
    timeout_arg(p_artifacts)

    p_iterations = sub.add_parser("iterations", help="Azure DevOps: list a team's sprints.")
    project_arg(p_iterations)
    p_iterations.add_argument("--team", help="Team id; default is the project's default team")
    timeout_arg(p_iterations)

    p_sprint_dates = sub.add_parser(
        "sprint-set-dates", help="Azure DevOps: set a sprint's start/finish dates."
    )
    project_arg(p_sprint_dates)
    p_sprint_dates.add_argument("--path", required=True, help="e.g. 'Iteration 1'")
    p_sprint_dates.add_argument("--start", required=True, help="ISO 8601, e.g. 2025-01-15")
    p_sprint_dates.add_argument("--finish", required=True, help="ISO 8601, e.g. 2025-01-22")
    timeout_arg(p_sprint_dates)

    p_boards = sub.add_parser("boards", help="Jira: list Agile boards for the project.")
    project_arg(p_boards)
    timeout_arg(p_boards)

    p_sprints = sub.add_parser("sprints", help="Jira: list a board's sprints.")
    project_arg(p_sprints)
    p_sprints.add_argument("--board", required=True, type=int)
    timeout_arg(p_sprints)

    p_sprint_create = sub.add_parser("sprint-create", help="Jira: create a sprint.")
    project_arg(p_sprint_create)
    p_sprint_create.add_argument("--board", required=True, type=int)
    p_sprint_create.add_argument("--name", required=True)
    p_sprint_create.add_argument("--start", help="ISO 8601")
    p_sprint_create.add_argument("--end", help="ISO 8601")
    p_sprint_create.add_argument("--goal")
    timeout_arg(p_sprint_create)

    p_sprint_start = sub.add_parser(
        "sprint-start", help="Jira: start a sprint (future -> active)."
    )
    project_arg(p_sprint_start)
    p_sprint_start.add_argument("--sprint", required=True, type=int)
    p_sprint_start.add_argument("--start", required=True, help="ISO 8601")
    p_sprint_start.add_argument("--end", required=True, help="ISO 8601")
    p_sprint_start.add_argument("--goal")
    timeout_arg(p_sprint_start)

    p_sprint_complete = sub.add_parser(
        "sprint-complete", help="Jira: close an active sprint. Not reversible."
    )
    project_arg(p_sprint_complete)
    p_sprint_complete.add_argument("--sprint", required=True, type=int)
    timeout_arg(p_sprint_complete)

    p_sprint_move = sub.add_parser(
        "sprint-move", help="Jira: move one or more issues into a sprint."
    )
    project_arg(p_sprint_move)
    p_sprint_move.add_argument("--sprint", required=True, type=int)
    p_sprint_move.add_argument("--id", action="append", required=True, help="Repeat per issue")
    timeout_arg(p_sprint_move)

    p_backlog_move = sub.add_parser(
        "backlog-move", help="Jira: move one or more issues to the backlog."
    )
    project_arg(p_backlog_move)
    p_backlog_move.add_argument("--id", action="append", required=True, help="Repeat per issue")
    timeout_arg(p_backlog_move)

    p_sprint_get = sub.add_parser("sprint-get", help="Jira: get one sprint by id.")
    project_arg(p_sprint_get)
    p_sprint_get.add_argument("--sprint", required=True, type=int)
    timeout_arg(p_sprint_get)

    p_sprint_update = sub.add_parser(
        "sprint-update", help="Jira: rename a sprint or change its goal/dates, no state change."
    )
    project_arg(p_sprint_update)
    p_sprint_update.add_argument("--sprint", required=True, type=int)
    field_arg(p_sprint_update)
    timeout_arg(p_sprint_update)

    p_sprint_delete = sub.add_parser(
        "sprint-delete", help="Jira: delete a sprint that was never started. Needs --confirm."
    )
    project_arg(p_sprint_delete)
    p_sprint_delete.add_argument("--sprint", required=True, type=int)
    p_sprint_delete.add_argument("--confirm", action="store_true")
    timeout_arg(p_sprint_delete)

    p_sprint_issues = sub.add_parser("sprint-issues", help="Jira: list issues in a sprint.")
    project_arg(p_sprint_issues)
    p_sprint_issues.add_argument("--sprint", required=True, type=int)
    timeout_arg(p_sprint_issues)

    p_backlog_issues = sub.add_parser("backlog-issues", help="Jira: list a board's backlog issues.")
    project_arg(p_backlog_issues)
    p_backlog_issues.add_argument("--board", required=True, type=int)
    timeout_arg(p_backlog_issues)

    p_board_issues = sub.add_parser(
        "board-issues", help="Jira: list every issue on a board (sprints and backlog)."
    )
    project_arg(p_board_issues)
    p_board_issues.add_argument("--board", required=True, type=int)
    timeout_arg(p_board_issues)

    p_test_plans = sub.add_parser("test-plans", help="Azure DevOps: list test plans.")
    project_arg(p_test_plans)
    timeout_arg(p_test_plans)

    p_test_plan_create = sub.add_parser("test-plan-create", help="Azure DevOps: create a test plan.")
    project_arg(p_test_plan_create)
    p_test_plan_create.add_argument("--name", required=True)
    p_test_plan_create.add_argument("--area-path")
    p_test_plan_create.add_argument("--iteration")
    p_test_plan_create.add_argument("--start")
    p_test_plan_create.add_argument("--end")
    timeout_arg(p_test_plan_create)

    p_test_plan_delete = sub.add_parser(
        "test-plan-delete", help="Azure DevOps: delete a test plan. Needs --confirm."
    )
    project_arg(p_test_plan_delete)
    p_test_plan_delete.add_argument("--plan", required=True, type=int)
    p_test_plan_delete.add_argument("--confirm", action="store_true")
    timeout_arg(p_test_plan_delete)

    p_test_suites = sub.add_parser("test-suites", help="Azure DevOps: list a plan's test suites.")
    project_arg(p_test_suites)
    p_test_suites.add_argument("--plan", required=True, type=int)
    timeout_arg(p_test_suites)

    p_test_suite_create = sub.add_parser(
        "test-suite-create", help="Azure DevOps: create a static test suite under a parent suite."
    )
    project_arg(p_test_suite_create)
    p_test_suite_create.add_argument("--plan", required=True, type=int)
    p_test_suite_create.add_argument("--name", required=True)
    p_test_suite_create.add_argument(
        "--parent", required=True, type=int, help="Parent suite id; a plan's root suite counts"
    )
    timeout_arg(p_test_suite_create)

    p_test_cases = sub.add_parser("test-cases", help="Azure DevOps: list a suite's test cases.")
    project_arg(p_test_cases)
    p_test_cases.add_argument("--plan", required=True, type=int)
    p_test_cases.add_argument("--suite", required=True, type=int)
    timeout_arg(p_test_cases)

    p_test_case_create = sub.add_parser(
        "test-case-create", help="Azure DevOps: create a Test Case work item."
    )
    project_arg(p_test_case_create)
    p_test_case_create.add_argument("--title")
    p_test_case_create.add_argument(
        "--scenario-file",
        help="Path to a test-design scenario JSON; supplies the title and the Gherkin",
    )
    p_test_case_create.add_argument(
        "--steps", help='JSON list, e.g. \'[{"action":"...","expected":"..."}]\''
    )
    gherkin_args(p_test_case_create)
    field_arg(p_test_case_create)
    timeout_arg(p_test_case_create)

    p_test_case_add = sub.add_parser(
        "test-case-add", help="Azure DevOps: file existing Test Case work items into a suite."
    )
    project_arg(p_test_case_add)
    p_test_case_add.add_argument("--plan", required=True, type=int)
    p_test_case_add.add_argument("--suite", required=True, type=int)
    p_test_case_add.add_argument("--id", action="append", required=True, help="Repeat per Test Case id")
    timeout_arg(p_test_case_add)

    p_test_case_steps = sub.add_parser(
        "test-case-steps", help="Azure DevOps: replace a Test Case's steps."
    )
    project_arg(p_test_case_steps)
    p_test_case_steps.add_argument("--id", required=True, help="Test Case work item id")
    p_test_case_steps.add_argument(
        "--steps", help='JSON list, e.g. \'[{"action":"...","expected":"..."}]\''
    )
    gherkin_args(p_test_case_steps)
    timeout_arg(p_test_case_steps)

    p_test_points = sub.add_parser("test-points", help="Azure DevOps: list a suite's test points.")
    project_arg(p_test_points)
    p_test_points.add_argument("--plan", required=True, type=int)
    p_test_points.add_argument("--suite", required=True, type=int)
    timeout_arg(p_test_points)

    p_test_point_outcome = sub.add_parser(
        "test-point-outcome", help="Azure DevOps: set the outcome on one or more test points."
    )
    project_arg(p_test_point_outcome)
    p_test_point_outcome.add_argument("--plan", required=True, type=int)
    p_test_point_outcome.add_argument("--suite", required=True, type=int)
    p_test_point_outcome.add_argument("--id", action="append", required=True, help="Repeat per point id")
    p_test_point_outcome.add_argument("--outcome", required=True, help="e.g. Passed, Failed, Blocked")
    timeout_arg(p_test_point_outcome)

    p_test_run_create = sub.add_parser(
        "test-run-create", help="Azure DevOps: create a test run for manual execution."
    )
    project_arg(p_test_run_create)
    p_test_run_create.add_argument("--plan", required=True, type=int)
    p_test_run_create.add_argument("--name", required=True)
    p_test_run_create.add_argument("--point", action="append", help="Test point id; repeat per point")
    timeout_arg(p_test_run_create)

    p_test_run_results = sub.add_parser(
        "test-run-results", help="Azure DevOps: get results for a test run."
    )
    project_arg(p_test_run_results)
    p_test_run_results.add_argument("--run", required=True, type=int)
    p_test_run_results.add_argument("--outcomes", help="Comma-separated filter, e.g. Passed,Failed")
    p_test_run_results.add_argument("--details", help="e.g. iterations,workItems")
    timeout_arg(p_test_run_results)

    p_test_run_results_update = sub.add_parser(
        "test-run-results-update", help="Azure DevOps: set outcomes/details for results in a run."
    )
    project_arg(p_test_run_results_update)
    p_test_run_results_update.add_argument("--run", required=True, type=int)
    p_test_run_results_update.add_argument(
        "--results", required=True,
        help='JSON list, e.g. \'[{"id":1,"outcome":"Passed"}]\'',
    )
    timeout_arg(p_test_run_results_update)

    p_test_run_complete = sub.add_parser(
        "test-run-complete", help="Azure DevOps: complete or abort a test run."
    )
    project_arg(p_test_run_complete)
    p_test_run_complete.add_argument("--run", required=True, type=int)
    p_test_run_complete.add_argument("--state", default="Completed", help="Completed or Aborted")
    timeout_arg(p_test_run_complete)

    p_test_results_by_build = sub.add_parser(
        "test-results-by-build", help="Azure DevOps: get test results from a build id."
    )
    project_arg(p_test_results_by_build)
    p_test_results_by_build.add_argument("--build", required=True, type=int)
    timeout_arg(p_test_results_by_build)

    p_milestones = sub.add_parser("milestones", help="GitLab: list milestones (its sprint equivalent).")
    project_arg(p_milestones)
    p_milestones.add_argument("--state", help="active or closed")
    timeout_arg(p_milestones)

    p_milestone_create = sub.add_parser("milestone-create", help="GitLab: create a milestone.")
    project_arg(p_milestone_create)
    p_milestone_create.add_argument("--title", required=True)
    p_milestone_create.add_argument("--description")
    p_milestone_create.add_argument("--start-date", help="YYYY-MM-DD")
    p_milestone_create.add_argument("--due-date", help="YYYY-MM-DD")
    timeout_arg(p_milestone_create)

    p_milestone_update = sub.add_parser(
        "milestone-update", help="GitLab: update a milestone, or close/reactivate it."
    )
    project_arg(p_milestone_update)
    p_milestone_update.add_argument("--milestone", required=True, type=int)
    field_arg(p_milestone_update)
    timeout_arg(p_milestone_update)

    p_milestone_delete = sub.add_parser(
        "milestone-delete", help="GitLab: delete a milestone. Needs --confirm."
    )
    project_arg(p_milestone_delete)
    p_milestone_delete.add_argument("--milestone", required=True, type=int)
    p_milestone_delete.add_argument("--confirm", action="store_true")
    timeout_arg(p_milestone_delete)

    p_milestone_issues = sub.add_parser("milestone-issues", help="GitLab: list a milestone's issues.")
    project_arg(p_milestone_issues)
    p_milestone_issues.add_argument("--milestone", required=True, type=int)
    timeout_arg(p_milestone_issues)

    p_milestone_move = sub.add_parser(
        "milestone-move", help="GitLab: file an issue into a milestone."
    )
    project_arg(p_milestone_move)
    p_milestone_move.add_argument("--id", required=True, help="Issue iid")
    p_milestone_move.add_argument("--milestone", required=True, type=int)
    timeout_arg(p_milestone_move)

    p_milestone_remove = sub.add_parser(
        "milestone-remove", help="GitLab: pull an issue out of its milestone."
    )
    project_arg(p_milestone_remove)
    p_milestone_remove.add_argument("--id", required=True, help="Issue iid")
    timeout_arg(p_milestone_remove)

    p_export = sub.add_parser(
        "export", help="Fetch every enabled source (or one) and save each item as its own JSON file."
    )
    project_arg(p_export)
    p_export.add_argument("--source", help="Limit to one source; default is every enabled source")
    p_export.add_argument("--out", help="Output folder; default <project>/_bmad_input")
    p_export.add_argument("--limit", type=int, default=1000)
    timeout_arg(p_export)

    p_report = sub.add_parser("report", help="Build a report from raw results.")
    project_arg(p_report, required=False)
    p_report.add_argument("--sources")
    p_report.add_argument("--results", required=True)
    p_report.add_argument("--out", required=True)

    p_state_init = sub.add_parser(
        "state-init",
        help="Construct state.json (metadata/orchestration/item buckets/queues), "
        "empty, matching the agreed schema. Idempotent unless --force.",
    )
    project_arg(p_state_init)
    p_state_init.add_argument(
        "--state-dir", help="Where state.json lives; default <project>/_bmad_state"
    )
    p_state_init.add_argument(
        "--force", action="store_true", help="Overwrite an existing state.json"
    )
    p_state_init.add_argument(
        "--schedule",
        action="store_true",
        help="Also register the recurring sync with the OS scheduler",
    )
    p_state_init.add_argument(
        "--source", help="With --schedule: poll only this source"
    )
    p_state_init.add_argument(
        "--every-minutes",
        type=int,
        default=None,
        help="With --schedule: minutes between runs; default from sources.json",
    )
    p_state_init.add_argument(
        "--backend",
        choices=list(SCHEDULE_BACKENDS),
        default="auto",
        help="With --schedule: which scheduler to register with",
    )

    p_schedule = sub.add_parser(
        "schedule",
        help="Register, inspect, pause or remove the recurring sync in the OS "
        "scheduler (Task Scheduler on Windows, a systemd timer on Linux, cron "
        "where systemd is absent).",
    )
    p_schedule.add_argument(
        "action",
        choices=["install", "status", "start", "stop", "remove", "run-now"],
        help="install: register | status: what is registered | stop/start: pause "
        "and resume | remove: unregister | run-now: fire one run immediately",
    )
    project_arg(p_schedule)
    p_schedule.add_argument(
        "--state-dir", help="Where state.json and the launcher live; default <project>/_bmad_state"
    )
    p_schedule.add_argument("--source", help="Poll only this source")
    p_schedule.add_argument(
        "--every-minutes",
        type=int,
        default=None,
        help="Minutes between runs; default from sources.json sync.interval_seconds",
    )
    p_schedule.add_argument(
        "--backend",
        choices=list(SCHEDULE_BACKENDS),
        default="auto",
        help="Scheduler to use; auto picks Task Scheduler on Windows and a systemd "
        "timer on Linux, falling back to cron where systemd is absent",
    )
    p_schedule.add_argument(
        "--graph",
        action="store_true",
        help="With install: also rebuild the Neo4j graph on every run",
    )
    p_schedule.add_argument(
        "--graph-prune",
        action="store_true",
        help="With --graph: delete graph nodes a run did not confirm",
    )
    p_schedule.add_argument(
        "--graph-state",
        help="With --graph: also write the project state document to this path each run",
    )

    p_sync = sub.add_parser(
        "sync",
        help="Fetch every enabled source (or one), diff against the last cycle, "
        "and record what changed into state.json and history.",
    )
    project_arg(p_sync)
    p_sync.add_argument("--source", help="Limit to one source; default is every enabled source")
    p_sync.add_argument("--limit", type=int, default=1000)
    timeout_arg(p_sync)
    p_sync.add_argument(
        "--state-dir", help="Where state.json/snapshots/history live; default <project>/_bmad_state"
    )
    p_sync.add_argument(
        "--dry-run", action="store_true", help="Fetch and diff, but write nothing"
    )
    p_sync.add_argument(
        "--force-full",
        action="store_true",
        help="Ignore the incremental schedule and read the full configured scope",
    )
    p_sync.add_argument(
        "--no-actions",
        action="store_true",
        help="Record changes in state and history, but do not queue pending_actions",
    )
    p_sync.add_argument(
        "--watch", action="store_true", help="Repeat the cycle forever (or --max-cycles times)"
    )
    p_sync.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between cycles under --watch; default from sources.json or 300",
    )
    p_sync.add_argument(
        "--max-cycles", type=int, default=None, help="Stop --watch after this many cycles"
    )

    p_graph_load = sub.add_parser(
        "graph-load",
        help="Build the Neo4j traceability graph from Jira (and Xray, where reachable): "
        "every issue, its people, hierarchy, links, sprints, comments and history, plus "
        "tests, steps, preconditions and runs when Xray is present.",
    )
    project_arg(p_graph_load)
    p_graph_load.add_argument(
        "--source", help="Limit to one jira source; default is every enabled jira source"
    )
    p_graph_load.add_argument("--limit", type=int, default=1000)
    timeout_arg(p_graph_load)
    p_graph_load.add_argument("--jql", help="Override the source's configured JQL scope for this build")
    p_graph_load.add_argument(
        "--dry-run", action="store_true", help="Extract and report, but write nothing to Neo4j"
    )
    p_graph_load.add_argument(
        "--prune",
        action="store_true",
        help="After loading, delete nodes/relationships this run did not confirm",
    )
    p_graph_load.add_argument(
        "--no-derive",
        action="store_true",
        help="Skip recomputing coverage, latest-run and hierarchy-depth after loading",
    )
    p_graph_load.add_argument("--batch-size", type=int, default=None)
    p_graph_load.add_argument(
        "--out", help="Also write the extracted batch (nodes + edges) as JSON to this path"
    )
    p_graph_load.add_argument(
        "--state", help="Also write the project state document as JSON to this path"
    )

    p_graph_state = sub.add_parser(
        "graph-state",
        help="The whole project as one JSON document: every requirement with its "
        "coverage, every test with its steps and last result, and every gap. Reads "
        "the trackers directly and needs no Neo4j.",
    )
    project_arg(p_graph_state)
    p_graph_state.add_argument("--source", help="Limit to one source; default is every enabled one")
    p_graph_state.add_argument("--limit", type=int, default=1000)
    timeout_arg(p_graph_state)
    p_graph_state.add_argument("--jql", help="Override the source's configured JQL scope")
    p_graph_state.add_argument("--out", help="Write the document to this path as well as stdout")
    p_graph_state.add_argument(
        "--summary",
        action="store_true",
        help="Print only what was read, the totals and the gaps, not every item",
    )

    project_arg(sub.add_parser(
        "graph-doctor", help="Check graph config, Neo4j connectivity and which Xray tier this site has."
    ))

    p_graph_query = sub.add_parser(
        "graph-query", help="Run one of the graph's named, read-only questions."
    )
    project_arg(p_graph_query, required=False)
    p_graph_query.add_argument("name", nargs="?", help="Query name; omit (or use --list) to see them all")
    p_graph_query.add_argument("--list", action="store_true", help="Print every query name and what it answers")
    p_graph_query.add_argument("--site", help="Override the site prefix; default is derived from --system's URL")
    p_graph_query.add_argument(
        "--system", default=None, help="Tracker system to query (jira, azuredevops, gitlab, confluence); default jira"
    )
    p_graph_query.add_argument(
        "--scope", default=None, help="Project scope to query; default: every scope on this system+site"
    )
    p_graph_query.add_argument("--limit", type=int, default=50)
    p_graph_query.add_argument(
        "--version", type=int, default=None, help="For the 'stale' query: the version to compare against"
    )
    p_graph_query.add_argument(
        "--param", action="append", metavar="KEY=VALUE", help="Extra query parameter, e.g. explore's label/search"
    )

    sub.add_parser("graph-labels", help="Print the closed set of node labels the graph can write.")
    sub.add_parser(
        "graph-relationships", help="Print the closed set of relationship types the graph can write."
    )

    project_arg(sub.add_parser(
        "graph-alias-list",
        help="Show what issue-type names map to which graph label, for this project.",
    ))

    p_alias_add = sub.add_parser(
        "graph-alias-add",
        help='Teach the graph a name this tracker uses for an issue type, e.g. "User Story" -> Story.',
    )
    project_arg(p_alias_add)
    p_alias_add.add_argument("type_name", help="The tracker's own issue type name, e.g. 'User Story'")
    p_alias_add.add_argument("label", help="The graph label it should carry; see graph-labels")

    p_alias_remove = sub.add_parser(
        "graph-alias-remove",
        help="Remove a project-level issue-type name override (shipped defaults are untouched).",
    )
    project_arg(p_alias_remove)
    p_alias_remove.add_argument("type_name")

    project_arg(sub.add_parser(
        "graph-link-alias-list",
        help="Show what Jira issue-link type names map to which graph relationship.",
    ))

    p_link_alias_add = sub.add_parser(
        "graph-link-alias-add",
        help='Teach the graph a Jira issue-link type name, e.g. "is validated by" -> COVERS.',
    )
    project_arg(p_link_alias_add)
    p_link_alias_add.add_argument("link_type_name")
    p_link_alias_add.add_argument(
        "relationship", help="The graph relationship type it should carry; see graph-relationships"
    )
    p_link_alias_add.add_argument(
        "--reverse", action="store_true", help="Store the edge from the inward issue to the outward one"
    )

    p_link_alias_remove = sub.add_parser(
        "graph-link-alias-remove", help="Remove a project-level link-type name override."
    )
    project_arg(p_link_alias_remove)
    p_link_alias_remove.add_argument("link_type_name")

    p_ssh_import = sub.add_parser(
        "ssh-key-import",
        help="Vault a private key read once from an explicit --path, under an alias. "
        "Never accepts key material as a bare argument, never defaults --path.",
    )
    project_arg(p_ssh_import, required=False)
    p_ssh_import.add_argument("--alias", required=True, help="Alias to store the key under")
    p_ssh_import.add_argument(
        "--path", required=True, help="Path to an existing private key file to read once"
    )
    p_ssh_import.add_argument(
        "--force",
        action="store_true",
        help="Allow importing a file that looks like the OS-default SSH identity "
        "(same name, sitting in ~/.ssh or %%USERPROFILE%%\\.ssh) -- refused otherwise",
    )

    p_ssh_list = sub.add_parser("ssh-key-list", help="List the aliases currently vaulted.")
    project_arg(p_ssh_list, required=False)

    p_ssh_delete = sub.add_parser("ssh-key-delete", help="Remove one vaulted key. Needs --confirm.")
    project_arg(p_ssh_delete, required=False)
    p_ssh_delete.add_argument("--alias", required=True)
    p_ssh_delete.add_argument("--confirm", action="store_true", help="Required to proceed")

    p_ssh_set_pass = sub.add_parser(
        "ssh-key-set-passphrase",
        help="Vault this alias's key passphrase (prompted, hidden input) so an "
        "already-approved push can fetch it automatically instead of a human typing "
        "it every time. Opt-in per alias -- an alias with nothing stored here still "
        "falls back to a manual prompt.",
    )
    project_arg(p_ssh_set_pass, required=False)
    p_ssh_set_pass.add_argument("--alias", required=True)
    p_ssh_set_pass.add_argument("--ssh-key-path", help="Verify against this on-disk key "
        "file instead of one vaulted under --alias -- use this when the alias is used "
        "with --ssh-key-path at clone/pull/push time rather than a Vault-imported key.")

    p_ssh_clear_pass = sub.add_parser(
        "ssh-key-clear-passphrase",
        help="Remove a vaulted passphrase for one alias, reverting it to manual entry. "
        "Needs --confirm.",
    )
    project_arg(p_ssh_clear_pass, required=False)
    p_ssh_clear_pass.add_argument("--alias", required=True)
    p_ssh_clear_pass.add_argument("--confirm", action="store_true", help="Required to proceed")

    p_ssh_migrate = sub.add_parser(
        "ssh-key-migrate",
        help="Copy one or all aliases from the retired OS-keyring vault into the "
        "HashiCorp Vault backend. Never overwrites an alias already present in the "
        "new Vault unless --force.",
    )
    project_arg(p_ssh_migrate, required=False)
    p_ssh_migrate.add_argument("--alias", help="Migrate this one alias")
    p_ssh_migrate.add_argument("--all", action="store_true", help="Migrate every legacy alias")
    p_ssh_migrate.add_argument(
        "--force", action="store_true", help="Overwrite the alias if it already exists in Vault"
    )

    p_ssh_purge = sub.add_parser(
        "ssh-key-legacy-purge",
        help="Remove one alias from the retired OS-keyring vault only. Needs --confirm. "
        "Run this only after verifying the migrated copy in Vault works.",
    )
    project_arg(p_ssh_purge, required=False)
    p_ssh_purge.add_argument("--alias", required=True)
    p_ssh_purge.add_argument("--confirm", action="store_true", help="Required to proceed")

    def known_hosts_arg(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--known-hosts",
            help="Override known_hosts file; otherwise GITLAB_SSH_KNOWN_HOSTS from the "
            "GITLAB_SSH_KNOWN_HOSTS, or the bundled gitlab.com host keys",
        )

    p_git_clone = sub.add_parser(
        "git-clone", help="Clone a GitLab repo over SSH with a vaulted read-only key."
    )
    project_arg(p_git_clone, required=False)
    p_git_clone.add_argument("--repo-url", required=True, help="e.g. git@gitlab.com:group/project.git")
    p_git_clone.add_argument("--dest", required=True, help="Destination folder for the clone")
    p_git_clone.add_argument("--ssh-key-alias", required=True, help="Passphrase is looked up "
        "under this name either way; the key material itself comes from here unless "
        "--ssh-key-path is also given")
    p_git_clone.add_argument("--ssh-key-path", help="Read the private key from this file "
        "directly (e.g. an existing ~/.ssh/id_ed25519) instead of from Vault -- never "
        "copies it anywhere, just reads it at use-time. --ssh-key-alias is still "
        "required and still names the Vault entry the passphrase is auto-fetched from.")
    p_git_clone.add_argument("--ssh-key-stdin", action="store_true", help="Read the "
        "private key as one base64 line from stdin (before the passphrase line, if "
        "--passphrase-stdin is also given) instead of from a file or Vault -- for a "
        "caller that already holds the key material in memory (e.g. a sandbox that "
        "got it from the backend) and must never write it to disk at all. Takes "
        "priority over --ssh-key-path if both are given.")
    p_git_clone.add_argument("--ref", help="Branch to clone")
    clone_passphrase_source = p_git_clone.add_mutually_exclusive_group()
    clone_passphrase_source.add_argument(
        "--prompt-passphrase",
        action="store_true",
        help="Interactively prompt (hidden input, terminal only) for the key's "
        "passphrase before use. Only for a human running this by hand -- never pass "
        "this from a scheduled task or a spawned agent, since there is no terminal "
        "there to answer it and the process would hang.",
    )
    clone_passphrase_source.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="Read the passphrase from stdin, one line, with no prompt shown -- for "
        "an automated caller that already has it, such as a sandbox where the key "
        "was supplied out of band and no Vault credentials are available to look "
        "one up. Omit both flags to keep the Vault auto-fetch.",
    )
    known_hosts_arg(p_git_clone)
    timeout_arg(p_git_clone)

    p_git_pull = sub.add_parser(
        "git-pull", help="Fast-forward pull an existing checkout over SSH with a vaulted read-only key."
    )
    project_arg(p_git_pull, required=False)
    p_git_pull.add_argument("--repo-dir", default=".", help="Existing checkout to pull in")
    p_git_pull.add_argument("--ssh-key-alias", required=True)
    p_git_pull.add_argument("--ssh-key-path", help="Read the private key from this file "
        "directly instead of from Vault; --ssh-key-alias still names the passphrase entry")
    p_git_pull.add_argument("--ssh-key-stdin", action="store_true", help="Read the "
        "private key as one base64 line from stdin (before the passphrase line, if "
        "--passphrase-stdin is also given) instead of from a file or Vault. Takes "
        "priority over --ssh-key-path if both are given.")
    pull_passphrase_source = p_git_pull.add_mutually_exclusive_group()
    pull_passphrase_source.add_argument(
        "--prompt-passphrase",
        action="store_true",
        help="Interactively prompt (hidden input, terminal only) for the key's "
        "passphrase before use. Only for a human running this by hand -- never pass "
        "this from a scheduled task or a spawned agent, since there is no terminal "
        "there to answer it and the process would hang.",
    )
    pull_passphrase_source.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="Read the passphrase from stdin, one line, with no prompt shown -- for "
        "an automated caller that already has it, such as a sandbox where the key "
        "was supplied out of band and no Vault credentials are available to look "
        "one up. Omit both flags to keep the Vault auto-fetch.",
    )
    known_hosts_arg(p_git_pull)
    timeout_arg(p_git_pull)

    p_git_push = sub.add_parser(
        "git-push",
        help="Push over SSH with a vaulted, passphrase-protected key. The passphrase "
        "is read fresh from stdin, one line; an empty line falls back to a Vault-"
        "stored passphrase for this alias if one was vaulted via "
        "ssh-key-set-passphrase, otherwise the push is refused. Never cached either way.",
    )
    project_arg(p_git_push, required=False)
    p_git_push.add_argument("--repo-dir", default=".", help="Existing checkout to push from")
    p_git_push.add_argument("--ssh-key-alias", required=True)
    p_git_push.add_argument("--ssh-key-path", help="Read the private key from this file "
        "directly instead of from Vault; --ssh-key-alias still names the passphrase entry")
    p_git_push.add_argument("--ssh-key-stdin", action="store_true", help="Read the "
        "private key as one base64 line from stdin, BEFORE the passphrase line that "
        "--passphrase-stdin also reads, instead of from a file or Vault. Takes "
        "priority over --ssh-key-path if both are given.")
    p_git_push.add_argument("--remote", default="origin")
    p_git_push.add_argument("--ref", help="Branch/refspec to push; default is the current branch")
    p_git_push.add_argument("--open-merge-request", action="store_true", help="Open (never "
        "merge) a GitLab merge request from the pushed branch, in the same SSH push via "
        "GitLab push options -- no REST token needed. An MR that already exists for the "
        "branch is reported, not duplicated. The JSON result carries merge_request.url.")
    p_git_push.add_argument("--merge-request-target", help="Branch the merge request "
        "targets; omitted means the repository's default branch")
    p_git_push.add_argument("--merge-request-title", help="Merge request title")
    p_git_push.add_argument("--merge-request-description", help="Merge request description "
        "(single line; newlines are collapsed)")
    push_passphrase_source = p_git_push.add_mutually_exclusive_group(required=True)
    push_passphrase_source.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="Read the passphrase from stdin, one line, with no prompt shown -- for a "
        "scripted/automated caller (e.g. usine.py approve-push) that already pipes "
        "it in. Confusing to type by hand: nothing is displayed to tell you it is "
        "waiting, so an accidental blank line reads as an empty passphrase. Use "
        "--prompt-passphrase instead when typing this yourself.",
    )
    push_passphrase_source.add_argument(
        "--prompt-passphrase",
        action="store_true",
        help="Interactively prompt (hidden input, terminal only) for the passphrase. "
        "For a human running this command by hand.",
    )
    known_hosts_arg(p_git_push)
    timeout_arg(p_git_push)

    return parser


_COMMANDS = {
    "systems": _cmd_systems,
    "plan": _cmd_plan,
    "resolve": _cmd_resolve,
    "doctor": _cmd_doctor,
    "env": _cmd_env,
    "ping": _cmd_ping,
    "health": _cmd_health,
    "fetch": _cmd_fetch,
    "get": _cmd_get,
    "create": _cmd_create,
    "update": _cmd_update,
    "transition": _cmd_transition,
    "delete": _cmd_delete,
    "comment": _cmd_comment,
    "link": _cmd_link,
    "links": _cmd_links,
    "unlink": _cmd_unlink,
    "xray-tier": _cmd_xray_tier,
    "xray-test-create": _cmd_xray_test_create,
    "xray-test-update": _cmd_xray_test_update,
    "xray-test-delete": _cmd_xray_test_delete,
    "xray-precondition-create": _cmd_xray_precondition_create,
    "xray-plan-create": _cmd_xray_plan_create,
    "xray-set-create": _cmd_xray_set_create,
    "xray-execution-create": _cmd_xray_execution_create,
    "xray-add-test": _cmd_xray_add_test,
    "xray-run-status": _cmd_xray_run_status,
    "detail-field-list": _cmd_detail_field_list,
    "detail-field-add": _cmd_detail_field_add,
    "detail-field-remove": _cmd_detail_field_remove,
    "pipelines": _cmd_pipelines,
    "pipeline-run": _cmd_pipeline_run,
    "pipeline-status": _cmd_pipeline_status,
    "pipeline-jobs": _cmd_pipeline_jobs,
    "job-status": _cmd_job_status,
    "job-logs": _cmd_job_logs,
    "job-retry": _cmd_job_retry,
    "job-cancel": _cmd_job_cancel,
    "pipeline-retry": _cmd_pipeline_retry,
    "pipeline-cancel": _cmd_pipeline_cancel,
    "jenkins-plugins-install": _cmd_jenkins_plugins_install,
    "jenkins-job-publish": _cmd_jenkins_job_publish,
    "jenkins-pipeline-publish": _cmd_jenkins_pipeline_publish,
    "jenkins-build-run": _cmd_jenkins_build_run,
    "jenkins-build-status": _cmd_jenkins_build_status,
    "jenkins-build-log": _cmd_jenkins_build_log,
    "artifacts": _cmd_artifacts,
    "iterations": _cmd_iterations,
    "sprint-set-dates": _cmd_sprint_set_dates,
    "boards": _cmd_boards,
    "sprints": _cmd_sprints,
    "sprint-create": _cmd_sprint_create,
    "sprint-start": _cmd_sprint_start,
    "sprint-complete": _cmd_sprint_complete,
    "sprint-move": _cmd_sprint_move,
    "backlog-move": _cmd_backlog_move,
    "sprint-get": _cmd_sprint_get,
    "sprint-update": _cmd_sprint_update,
    "sprint-delete": _cmd_sprint_delete,
    "sprint-issues": _cmd_sprint_issues,
    "backlog-issues": _cmd_backlog_issues,
    "board-issues": _cmd_board_issues,
    "test-plans": _cmd_test_plans,
    "test-plan-create": _cmd_test_plan_create,
    "test-plan-delete": _cmd_test_plan_delete,
    "test-suites": _cmd_test_suites,
    "test-suite-create": _cmd_test_suite_create,
    "test-cases": _cmd_test_cases,
    "test-case-create": _cmd_test_case_create,
    "test-case-add": _cmd_test_case_add,
    "test-case-steps": _cmd_test_case_steps,
    "test-points": _cmd_test_points,
    "test-point-outcome": _cmd_test_point_outcome,
    "test-run-create": _cmd_test_run_create,
    "test-run-results": _cmd_test_run_results,
    "test-run-results-update": _cmd_test_run_results_update,
    "test-run-complete": _cmd_test_run_complete,
    "test-results-by-build": _cmd_test_results_by_build,
    "milestones": _cmd_milestones,
    "milestone-create": _cmd_milestone_create,
    "milestone-update": _cmd_milestone_update,
    "milestone-delete": _cmd_milestone_delete,
    "milestone-issues": _cmd_milestone_issues,
    "milestone-move": _cmd_milestone_move,
    "milestone-remove": _cmd_milestone_remove,
    "export": _cmd_export,
    "report": _cmd_report,
    "state-init": _cmd_state_init,
    "sync": _cmd_sync,
    "schedule": _cmd_schedule,
    "graph-load": _cmd_graph_load,
    "graph-state": _cmd_graph_state,
    "graph-doctor": _cmd_graph_doctor,
    "graph-query": _cmd_graph_query,
    "graph-labels": _cmd_graph_labels,
    "graph-relationships": _cmd_graph_relationships,
    "graph-alias-list": _cmd_graph_alias_list,
    "graph-alias-add": _cmd_graph_alias_add,
    "graph-alias-remove": _cmd_graph_alias_remove,
    "graph-link-alias-list": _cmd_graph_link_alias_list,
    "graph-link-alias-add": _cmd_graph_link_alias_add,
    "graph-link-alias-remove": _cmd_graph_link_alias_remove,
    "ssh-key-import": _cmd_ssh_key_import,
    "ssh-key-list": _cmd_ssh_key_list,
    "ssh-key-delete": _cmd_ssh_key_delete,
    "ssh-key-set-passphrase": _cmd_ssh_key_set_passphrase,
    "ssh-key-clear-passphrase": _cmd_ssh_key_clear_passphrase,
    "ssh-key-migrate": _cmd_ssh_key_migrate,
    "ssh-key-legacy-purge": _cmd_ssh_key_legacy_purge,
    "git-clone": _cmd_git_clone,
    "git-pull": _cmd_git_pull,
    "git-push": _cmd_git_push,
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    if args.command == "sync":
        logging.getLogger("connection_sources.cli").info(
            "sync launched: project=%s source=%s watch=%s force_full=%s",
            getattr(args, "project", None), getattr(args, "source", None),
            getattr(args, "watch", False), getattr(args, "force_full", False),
        )
    if args.command in {"plan", "report"} and not (args.project or args.sources):
        print(json.dumps({"error": "pass --project or --sources"}), file=sys.stderr)
        return 1
    if args.command == "artifacts" and not (args.pipeline or args.job):
        print(json.dumps({"error": "artifacts needs --pipeline or --job"}), file=sys.stderr)
        return 1
    if getattr(args, "project", None):
        try:
            args.project = _resolve_project(args.project)
        except SourcesConfigError as exc:
            return _fail(exc)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
