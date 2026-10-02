"""Which repository to index, and the namespace its nodes are written under.

The repository location is not hardcoded: the sandbox clones the project's repo under
/workspace (today `/workspace/repo`; the listener owns that choice), so `CODE_GRAPH_REPO`
wins when set and otherwise the first of the known locations that is a git checkout.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from connection_sources.errors import SourcesConfigError
from connection_sources.graph.model import Namespace

from .schema import SYSTEM

__all__ = ["RepoInfo", "exclude_from_git", "find_repo", "repo_info"]

DEFAULT_CANDIDATES = ("/workspace/repo", "/workspace/tests")

# What `cgc index` writes into or next to the indexed tree. Listed in
# .git/info/exclude so the client repository's `git status` stays clean — a dirty
# tree would show up in Ray's proposal diffs.
GIT_EXCLUDES = (".cgcignore", ".codegraphcontext/")


def find_repo(explicit: str | None = None) -> Path:
    """The repository root to index."""
    for candidate in (explicit, os.environ.get("CODE_GRAPH_REPO")):
        if candidate:
            path = Path(candidate).expanduser().resolve()
            if not path.is_dir():
                raise SourcesConfigError(f"code-graph: repository {path} does not exist", server="code-graph")
            return path
    for candidate in DEFAULT_CANDIDATES:
        path = Path(candidate)
        if (path / ".git").exists():
            return path.resolve()
    raise SourcesConfigError(
        "code-graph: no repository to index",
        server="code-graph",
        remediation="pass --repo, set CODE_GRAPH_REPO, or clone the project into /workspace/repo",
    )


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _site_and_scope(remote: str, root: Path) -> tuple[str, str]:
    """`(host, group/project)` from a remote URL; `("local", dirname)` without one."""
    if not remote:
        return "local", root.name
    if "://" in remote:
        parsed = urlparse(remote)
        host, path = (parsed.hostname or "local"), parsed.path
    elif "@" in remote and ":" in remote:  # git@host:group/project.git
        host, path = remote.split("@", 1)[1].split(":", 1)
    else:
        return "local", root.name
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return host.lower(), (path or root.name)


@dataclass(frozen=True)
class RepoInfo:
    root: Path
    head_sha: str
    remote: str
    namespace: Namespace


def repo_info(root: Path, *, platform: str | None = None) -> RepoInfo:
    remote = _git(root, "remote", "get-url", "origin")
    site, scope = _site_and_scope(remote, root)
    platform = platform if platform is not None else (os.environ.get("PROJECT_ID") or None)
    return RepoInfo(
        root=root,
        head_sha=_git(root, "rev-parse", "HEAD"),
        remote=remote,
        namespace=Namespace.make(SYSTEM, site, scope, platform=platform),
    )


def exclude_from_git(root: Path) -> None:
    """Add CGC's droppings to .git/info/exclude, idempotently. No-op outside git."""
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
    missing = [p for p in GIT_EXCLUDES if p not in existing]
    if missing:
        with exclude.open("a", encoding="utf-8") as fh:
            if existing and existing[-1].strip():
                fh.write("\n")
            fh.write("# code-graph (CodeGraphContext) artefacts\n")
            fh.write("\n".join(missing) + "\n")
