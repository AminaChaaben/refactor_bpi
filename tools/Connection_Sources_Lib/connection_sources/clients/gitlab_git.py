"""GitLab git transport over SSH: clone/pull with a read-only key, push with a
passphrase-protected key whose passphrase comes from the caller, the sandbox env or Vault.

Not an `AlmClient` -- there is nothing REST about this, it shells out to the system
`git`/`ssh-agent`/`ssh-add` binaries (Git for Windows bundles OpenSSH's agent tools, so
the same code path works on both supported platforms; macOS is rejected upstream by
`ssh_vault`). No PAT, no `GITLAB_TOKEN` -- SSH only.

Safety properties, and how each is achieved:
  - The company/default SSH identity is never read, written or offered. Every
    subprocess env starts from `_clean_env()`, which strips any inherited
    SSH_AUTH_SOCK/SSH_AGENT_PID before a brand-new, isolated ssh-agent is started --
    so an ambient agent already holding the company key (Pageant, a desktop
    ssh-agent, ...) is never reachable through these commands. `IdentitiesOnly=yes`
    plus an explicit `IdentityFile` (a stub holding only the *public* half of the
    vaulted key, obtained from the agent itself) also suppresses ssh's own built-in
    fallback to `~/.ssh/id_rsa`/`id_ed25519`/etc, which normal ssh tries whenever no
    IdentityFile is configured.
  - Host verification is never `StrictHostKeyChecking=no`. `UserKnownHostsFile`
    always points at a pinned file (the bundled `resources/gitlab_known_hosts`, or a
    project-supplied `GITLAB_SSH_KNOWN_HOSTS` for self-hosted GitLab); `BatchMode=yes`
    means a host key that does not match, or any other prompt ssh would normally show
    interactively, fails the command outright instead of hanging or silently trusting.
  - The push key's decrypted material never survives past one push: a fresh
    ssh-agent is started per push and killed in `finally` regardless of outcome, so
    every push resolves the passphrase again (given, then Vault-stored for the alias);
    a human is only needed when neither exists.
  - Nothing here ever writes a private key or a passphrase to disk. The key is fed to
    `ssh-add` over stdin; the passphrase reaches `ssh-add` only via an `SSH_ASKPASS`
    helper script that reads it back out of an environment variable at run time (the
    script's own file content never contains the secret).
  - A caller that already holds the key material in memory (e.g. a sandbox that got
    it handed down from the backend, never from its own Vault credentials) can pass
    `key_bytes` directly to `clone`/`pull`/`push` -- or, from the CLI, `--ssh-key-
    stdin` -- and the key never touches a file at all, not even transiently:
    `_resolve_key_bytes` returns it straight through to `_EphemeralAgent.add_key`,
    the same stdin-fed path a Vault-resolved key already uses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import re
import uuid
from pathlib import Path
from typing import Any

from .. import ssh_vault
from ..errors import (
    CredentialError,
    SourcesConfigError,
    ToolCallError,
    ToolNotFoundError,
    TransportError,
)

DEFAULT_TIMEOUT = 60.0

_BUNDLED_KNOWN_HOSTS = Path(__file__).resolve().parent.parent / "resources" / "gitlab_known_hosts"
_AUTH_SOCK_RE = re.compile(r"SSH_AUTH_SOCK=(?P<sock>[^;]+);")
_AGENT_PID_RE = re.compile(r"SSH_AGENT_PID=(?P<pid>\d+);")
_MERGE_REQUEST_URL_RE = re.compile(r"https?://\S+/-/merge_requests/\d+")
_UNREACHABLE_MARKERS = (
    "could not resolve hostname",
    "name or service not known",
    "temporary failure in name resolution",
    "connection timed out",
    "no route to host",
    "network is unreachable",
    "connection refused",
)


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("SSH_AUTH_SOCK", None)
    env.pop("SSH_AGENT_PID", None)
    return env


def _windows_git_tool_dir() -> Path | None:
    if os.name != "nt":
        return None
    git_bin = shutil.which("git")
    if not git_bin:
        return None
    candidate = Path(git_bin).resolve().parent.parent / "usr" / "bin"
    return candidate if candidate.is_dir() else None


def _resolve_tool(name: str) -> str | None:
    """Path to `name`, preferring Git for Windows' own MSYS build over Windows' native
    OpenSSH on PATH.

    Windows' built-in ssh-agent is tied to a system service and a named pipe, not the
    standalone spawn-and-print-env-vars behaviour the rest of this module depends on --
    invoking it directly as a subprocess produces no usable output even once its
    service is running. Git for Windows bundles a genuine MSYS ssh/ssh-agent/ssh-add
    that behaves the same way on Windows as everywhere else, so it is preferred
    whenever present. ssh, ssh-agent and ssh-add must all come from this same bundle
    together: the agent's Unix-style socket path is only understood by the matching
    ssh/ssh-add, not Windows' native ones.
    """
    tool_dir = _windows_git_tool_dir()
    if tool_dir:
        candidate = tool_dir / f"{name}.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def _quote(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _write_askpass_helper(passphrase_env_var: str) -> Path:
    is_windows = os.name == "nt"
    fd, raw_path = tempfile.mkstemp(prefix="alm-askpass-", suffix=".bat" if is_windows else ".sh")
    path = Path(raw_path)
    try:
        if is_windows:
            path.write_text(f"@echo off\r\necho %{passphrase_env_var}%\r\n", encoding="ascii")
        else:
            path.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "${passphrase_env_var}"\n', encoding="ascii")
            os.chmod(path, 0o700)
    finally:
        os.close(fd)
    return path


def _write_pubkey_stub(pub_line: str) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="alm-vaulted-key-", suffix=".pub")
    path = Path(raw_path)
    try:
        path.write_text(pub_line + "\n", encoding="ascii")
    finally:
        os.close(fd)
    return path


class _EphemeralAgent:
    """One ssh-agent process, isolated from anything already running, killed on exit."""

    def __init__(self, base_env: dict[str, str]) -> None:
        self._base_env = base_env
        self._pid: str | None = None
        self._socket_path: Path | None = None
        self.env: dict[str, str] = {}

    def __enter__(self) -> "_EphemeralAgent":
        agent_bin = _resolve_tool("ssh-agent")
        if not agent_bin:
            raise ToolNotFoundError(
                "ssh-agent not found on PATH",
                remediation="install OpenSSH client tools (Git for Windows bundles "
                "them) and retry",
            )
        # Git for Windows' ssh-agent defaults its Unix-domain-socket file to
        # $HOME/.ssh/agent/s.<...>.agent.<...>. A real `usine.py spawn`'s isolated HOME
        # (e.g. C:\ProgramData\talan-usine\runs\<agent>\<run_id>) is long enough that
        # this default then exceeds the OS's ~108-byte sockaddr_un limit -- reproduced
        # live: "path ... too long for Unix domain socket" / "Couldn't prepare agent
        # socket", with an empty stdout that made the old bare "did not report a
        # socket/pid" error impossible to diagnose. This never showed up interactively
        # because a real user HOME is short. Force a short, explicit socket path under
        # the real system temp dir instead of trusting the agent's HOME-derived default,
        # which isolation can't guarantee stays short.
        socket_path = Path(tempfile.gettempdir()) / f"talan-ssh-agent-{uuid.uuid4().hex[:10]}.sock"
        try:
            result = subprocess.run(
                [agent_bin, "-s", "-a", str(socket_path)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                env=self._base_env,
            )
        except OSError as exc:
            raise ToolNotFoundError(f"could not start ssh-agent: {exc}") from exc
        pid = _AGENT_PID_RE.search(result.stdout)
        if not pid or not socket_path.exists():
            raise ToolCallError(
                "ssh-agent did not report a pid, or its socket was never created "
                f"(binary={agent_bin!r} socket_path={str(socket_path)!r} "
                f"returncode={result.returncode} stdout={result.stdout!r} "
                f"stderr={result.stderr!r})",
                remediation="check that ssh-agent on PATH is a genuine OpenSSH agent",
            )
        self._pid = pid.group("pid")
        self._socket_path = socket_path
        self.env = dict(self._base_env)
        self.env["SSH_AUTH_SOCK"] = str(socket_path)
        self.env["SSH_AGENT_PID"] = self._pid
        return self

    def add_key(self, key_bytes: bytes, *, passphrase: str | None) -> None:
        add_bin = _resolve_tool("ssh-add")
        if not add_bin:
            raise ToolNotFoundError("ssh-add not found on PATH")
        env = dict(self.env)
        # Always wire an askpass helper, even with no passphrase in hand -- an empty
        # value still answers instantly. Without this, a protected key with no
        # passphrase available falls through to ssh-add trying to read from a
        # controlling terminal that doesn't exist here, which doesn't fail -- it
        # hangs until our own subprocess timeout below, a confusing dead end instead
        # of a clear error.
        askpass_path = _write_askpass_helper("SSH_VAULT_ASKPASS_VALUE")
        env["SSH_ASKPASS"] = str(askpass_path)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["SSH_VAULT_ASKPASS_VALUE"] = passphrase or ""
        env.setdefault("DISPLAY", ":0")
        try:
            result = subprocess.run(
                [add_bin, "-"], input=key_bytes, capture_output=True, env=env, timeout=15
            )
        finally:
            askpass_path.unlink(missing_ok=True)
        if result.returncode != 0:
            hint = (
                "this alias has no passphrase given or vaulted -- if the key needs "
                "one, vault it with 'alm-conn ssh-key-set-passphrase --alias ...'"
                if not passphrase
                else "retry with the correct passphrase for this alias"
            )
            raise CredentialError(
                "ssh-add refused the key -- wrong passphrase, or the vaulted key is "
                "not a valid OpenSSH private key",
                remediation=hint,
            )

    def public_key_line(self) -> str:
        add_bin = _resolve_tool("ssh-add")
        result = subprocess.run(
            [add_bin, "-L"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=10,
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        if not lines:
            raise ToolCallError("ssh-add -L returned no identity after the key was added")
        return lines[0]

    def __exit__(self, *exc_info: object) -> None:
        agent_bin = _resolve_tool("ssh-agent")
        if agent_bin and self._pid:
            subprocess.run(
                [agent_bin, "-k"],
                stdin=subprocess.DEVNULL,
                env=self.env,
                capture_output=True,
                timeout=10,
            )
        # Belt-and-suspenders: `-k` should already remove its own socket file, but an
        # explicit `-a` path is our own naming, not the agent's, so clean it up
        # ourselves too rather than trust that convention held.
        if self._socket_path is not None:
            self._socket_path.unlink(missing_ok=True)


def _resolve_known_hosts(known_hosts_path: str | None) -> Path:
    if known_hosts_path:
        candidate = Path(known_hosts_path)
        if not candidate.is_file():
            raise SourcesConfigError(
                f"GITLAB_SSH_KNOWN_HOSTS points at a missing file: {candidate}",
                remediation="fix the path, or unset it to use the bundled gitlab.com host keys",
            )
        return candidate
    if not _BUNDLED_KNOWN_HOSTS.is_file():
        raise SourcesConfigError(
            "the bundled gitlab_known_hosts resource is missing",
            remediation="reinstall Connection_Sources_Lib",
        )
    return _BUNDLED_KNOWN_HOSTS


def _ssh_command(known_hosts: Path, identity_file: Path) -> str:
    parts = [
        _resolve_tool("ssh") or "ssh",
        "-o", "IdentitiesOnly=yes",
        "-o", f"IdentityFile={identity_file.as_posix()}",
        "-o", f"UserKnownHostsFile={known_hosts.as_posix()}",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no",
        "-o", "BatchMode=yes",
    ]
    return " ".join(_quote(p) for p in parts)


def _run_git(args: list[str], *, cwd: str | None, env: dict[str, str], timeout: float) -> dict[str, str]:
    git_bin = shutil.which("git")
    if not git_bin:
        raise ToolNotFoundError("git not found on PATH")
    try:
        result = subprocess.run(
            [git_bin, *args],
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise TransportError(
            f"git {' '.join(args)} timed out after {timeout}s", retryable=True
        ) from exc
    except OSError as exc:
        raise ToolNotFoundError(f"could not run git: {exc}") from exc

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        combined = stderr or stdout or f"git exited {result.returncode}"
        lowered = combined.lower()
        if any(marker in lowered for marker in _UNREACHABLE_MARKERS):
            raise TransportError(
                f"git host unreachable: {combined}",
                remediation="the git host could not be reached (DNS or network) -- check "
                "the VPN/corporate network and that the hostname resolves from here",
                retryable=True,
            )
        if "permission denied (publickey)" in lowered or "could not read from remote" in lowered:
            raise CredentialError(
                f"git authentication failed: {combined}",
                remediation="check --ssh-key-alias is correct and, for a push, that "
                "the passphrase entered was right",
            )
        if "host key verification failed" in lowered:
            raise TransportError(
                f"git host-key verification failed: {combined}",
                remediation="the pinned known_hosts does not match this host -- verify "
                "GITLAB_SSH_KNOWN_HOSTS or the bundled resource; never disable checking",
            )
        raise ToolCallError(f"git {' '.join(args)} failed: {combined}")
    return {"stdout": stdout, "stderr": stderr}


def _resolve_key_bytes(
    ssh_key_alias: str,
    ssh_key_path: str | None,
    vault_token: str | None,
    key_bytes: bytes | None = None,
) -> bytes:
    """Key material from, in priority order: bytes the caller already holds in memory
    (`key_bytes` -- e.g. a sandbox that got its key handed to it by the backend and
    never wants it touching disk at all), an explicit on-disk path (e.g.
    `~/.ssh/id_ed25519`, left exactly where it already lives, never copied into
    Vault), or Vault under `ssh_key_alias` as before. `ssh_key_alias` still names the
    Vault entry a passphrase is looked up under in every case -- neither `key_bytes`
    nor a path-based key skips Vault for the passphrase, only for the key bytes
    themselves.
    """
    if key_bytes is not None:
        return key_bytes
    if ssh_key_path:
        path = Path(ssh_key_path).expanduser()
        if not path.is_file():
            raise CredentialError(
                f"no such SSH key file: {path}",
                remediation="check --ssh-key-path (or the configured path) points at "
                "an existing private key file",
            )
        return path.read_bytes()
    return ssh_vault.get_key(ssh_key_alias, vault_token=vault_token)


def _vaulted_passphrase(ssh_key_alias: str, vault_token: str | None) -> str | None:
    """Vault's answer, or None when Vault cannot be reached at all.

    A sandbox gets its key and passphrase handed to it by the backend and holds
    no Vault credentials, so _resolve_client would raise before the caller's own
    "no passphrase" path could report anything useful. Treating unreachable as
    "nothing vaulted" keeps that case on the caller's error, and still lets a
    laptop with a working VAULT_TOKEN auto-fetch exactly as before.
    """
    try:
        return ssh_vault.get_passphrase(ssh_key_alias, vault_token=vault_token)
    except CredentialError:
        return None


def clone(
    repo_url: str,
    dest: str,
    *,
    ssh_key_alias: str,
    ssh_key_path: str | None = None,
    key_bytes: bytes | None = None,
    ref: str | None = None,
    known_hosts_path: str | None = None,
    vault_token: str | None = None,
    passphrase: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    # Read operations carry no human-approval gate in this design -- they're meant
    # to run fully unattended -- so auto-fetching a Vault-stored passphrase here is
    # safe and consistent (unlike push, where the human step is the point). A key
    # with no passphrase vaulted and none given just tries unprotected, exactly as
    # before.
    if not passphrase:
        passphrase = _vaulted_passphrase(ssh_key_alias, vault_token)
    key_bytes = _resolve_key_bytes(ssh_key_alias, ssh_key_path, vault_token, key_bytes)
    known_hosts = _resolve_known_hosts(known_hosts_path)
    with _EphemeralAgent(_clean_env()) as agent:
        agent.add_key(key_bytes, passphrase=passphrase)
        identity_file = _write_pubkey_stub(agent.public_key_line())
        try:
            git_env = dict(agent.env)
            git_env["GIT_SSH_COMMAND"] = _ssh_command(known_hosts, identity_file)
            args = ["clone", repo_url, dest]
            if ref:
                args += ["--branch", ref]
            result = _run_git(args, cwd=None, env=git_env, timeout=timeout)
        finally:
            identity_file.unlink(missing_ok=True)
    return {"ok": True, "cloned": repo_url, "dest": str(Path(dest).resolve()), **result}


def pull(
    repo_dir: str,
    *,
    ssh_key_alias: str,
    ssh_key_path: str | None = None,
    key_bytes: bytes | None = None,
    known_hosts_path: str | None = None,
    vault_token: str | None = None,
    passphrase: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    if not passphrase:
        passphrase = _vaulted_passphrase(ssh_key_alias, vault_token)
    key_bytes = _resolve_key_bytes(ssh_key_alias, ssh_key_path, vault_token, key_bytes)
    known_hosts = _resolve_known_hosts(known_hosts_path)
    with _EphemeralAgent(_clean_env()) as agent:
        agent.add_key(key_bytes, passphrase=passphrase)
        identity_file = _write_pubkey_stub(agent.public_key_line())
        try:
            git_env = dict(agent.env)
            git_env["GIT_SSH_COMMAND"] = _ssh_command(known_hosts, identity_file)
            result = _run_git(["pull", "--ff-only"], cwd=repo_dir, env=git_env, timeout=timeout)
        finally:
            identity_file.unlink(missing_ok=True)
    return {"ok": True, "pulled": str(Path(repo_dir).resolve()), **result}


def _source_branch(ref: str | None) -> str | None:
    """The remote branch name a push refspec lands on, or None for "current branch"."""
    if not ref:
        return None
    destination = ref.rsplit(":", 1)[-1].lstrip("+")
    return destination.removeprefix("refs/heads/") or None


def _push_option_value(value: str) -> str:
    """Git refuses a push option containing a newline, so collapse all whitespace runs."""
    return " ".join(value.split())


def _merge_request_push_options(
    *, ref: str | None, target: str | None, title: str | None, description: str | None
) -> list[str]:
    """GitLab push options that open a merge request from the pushed branch.

    Opens it, never merges it: `merge_request.merge_when_pipeline_succeeds` is
    deliberately never sent, so the merge stays a human decision in GitLab.
    """
    target = (target or "").strip()
    source = _source_branch(ref)
    if target and source and source == target:
        raise SourcesConfigError(
            f"merge request source and target are both {target!r}",
            remediation="push to a new branch and target the project's selected branch, "
            "or omit --merge-request-target to use the repository's default branch",
        )
    options = ["-o", "merge_request.create"]
    if target:
        options += ["-o", f"merge_request.target={target}"]
    if title and title.strip():
        options += ["-o", f"merge_request.title={_push_option_value(title)}"]
    if description and description.strip():
        options += ["-o", f"merge_request.description={_push_option_value(description)}"]
    return options


def _merge_request_url(git_output: str) -> str | None:
    """The MR link GitLab prints on push ("View merge request for ..."), if any."""
    match = _MERGE_REQUEST_URL_RE.search(git_output)
    return match.group(0) if match else None


def push(
    repo_dir: str,
    *,
    ssh_key_alias: str,
    ssh_key_path: str | None = None,
    key_bytes: bytes | None = None,
    passphrase: str | None = None,
    remote: str = "origin",
    ref: str | None = None,
    known_hosts_path: str | None = None,
    vault_token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    open_merge_request: bool = False,
    merge_request_target: str | None = None,
    merge_request_title: str | None = None,
    merge_request_description: str | None = None,
) -> dict[str, Any]:
    # A passphrase supplied by the caller (typed by a human this turn) always wins.
    # Only when none was given do we fall back to a Vault-stored one -- and only an
    # alias that has had a passphrase explicitly vaulted via
    # 'alm-conn ssh-key-set-passphrase' has one to find; every other alias behaves
    # exactly as before and still requires it typed fresh. The human authorization
    # for *this push* is enforced upstream, by whatever gate called push() (e.g.
    # usine.py approve-push's interactive confirmation) -- this function only
    # decides where the key material comes from.
    passphrase_source = "provided"
    if not passphrase:
        passphrase = _vaulted_passphrase(ssh_key_alias, vault_token)
        passphrase_source = "vault"
    if not passphrase:
        raise CredentialError(
            "git-push needs the push key's passphrase, entered fresh this turn",
            remediation="the alm-gitlab-git push command prompts for it interactively, "
            "or vault one for this alias with 'alm-conn ssh-key-set-passphrase "
            "--alias ...' -- never pass it as a bare CLI flag from a script or shell "
            "history",
        )
    push_options: list[str] = []
    if open_merge_request:
        push_options = _merge_request_push_options(
            ref=ref,
            target=merge_request_target,
            title=merge_request_title,
            description=merge_request_description,
        )
    key_bytes = _resolve_key_bytes(ssh_key_alias, ssh_key_path, vault_token, key_bytes)
    known_hosts = _resolve_known_hosts(known_hosts_path)
    with _EphemeralAgent(_clean_env()) as agent:
        agent.add_key(key_bytes, passphrase=passphrase)
        identity_file = _write_pubkey_stub(agent.public_key_line())
        try:
            git_env = dict(agent.env)
            git_env["GIT_SSH_COMMAND"] = _ssh_command(known_hosts, identity_file)
            args = ["push", *push_options, remote]
            if ref:
                args.append(ref)
            result = _run_git(args, cwd=repo_dir, env=git_env, timeout=timeout)
        finally:
            identity_file.unlink(missing_ok=True)
    # __exit__ above always kills this agent, success or failure -- the very next
    # push needs a passphrase resolved again, not just reused from this one.
    outcome: dict[str, Any] = {
        "ok": True,
        "pushed": str(Path(repo_dir).resolve()),
        "remote": remote,
        "passphrase_source": passphrase_source,
        **result,
    }
    if open_merge_request:
        url = _merge_request_url(f"{result['stdout']}\n{result['stderr']}")
        outcome["merge_request"] = {
            "source": _source_branch(ref),
            "target": (merge_request_target or "").strip() or None,
            "url": url,
            "opened": url is not None,
        }
        if url is None:
            outcome["merge_request"]["warning"] = (
                "the push succeeded but GitLab reported no merge request -- nothing new "
                "was pushed, or this GitLab does not accept push options"
            )
    return outcome
