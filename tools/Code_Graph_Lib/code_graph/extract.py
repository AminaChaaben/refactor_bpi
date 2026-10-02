"""CodeGraphContext as an extractor: index into a scratch KùzuDB store, export a bundle.

CGC never writes to the project's Neo4j. It parses into a throwaway embedded store in
a temp directory (its per-repo `.codegraphcontext/` lands there too, never in the
client repository), and `cgc bundle export` turns that store into nodes.jsonl /
edges.jsonl, which `bundle.py` maps onto the code graph's own identities.

Grammar check: tree-sitter-language-pack 1.x downloads each grammar on first use. When
that download is impossible (a sandbox has no internet) CGC does not fail — it logs a
warning and skips every file of that language, producing an empty graph that looks
like a clean codebase. So the grammars a repository needs are checked (and fetched,
when possible) before indexing, and their absence is a hard error.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from connection_sources.errors import ConnectionSourceError

__all__ = ["Bundle", "EXT_LANGUAGE", "extract", "languages_in"]

# File extension -> tree-sitter-language-pack grammar name, for the languages CGC
# builds symbols for. Grammars are pre-fetched into the sandbox image for these.
EXT_LANGUAGE: dict[str, str] = {
    ".java": "java",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".go": "go",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".swift": "swift",
    ".dart": "dart",
}

_SKIP_DIRS = {".git", "node_modules", "target", "build", "dist", ".venv", "venv", "__pycache__", ".codegraphcontext", ".gradle", ".idea"}


@dataclass(frozen=True)
class Bundle:
    workdir: Path
    nodes_path: Path
    edges_path: Path
    metadata: dict[str, Any]

    def nodes(self) -> Iterator[dict[str, Any]]:
        with self.nodes_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)

    def edges(self) -> Iterator[dict[str, Any]]:
        with self.edges_path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)


def languages_in(root: Path) -> dict[str, int]:
    """Grammar name -> number of files, for the source files under `root`."""
    counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            lang = EXT_LANGUAGE.get(os.path.splitext(name)[1].lower())
            if lang:
                counts[lang] = counts.get(lang, 0) + 1
    return counts


# Where the sandbox image pre-fetches the grammar bundle. Pinned through the
# library's own env var rather than left under $HOME/.cache: an agent spawned by
# usine.py runs with an isolated HOME, where the image's cache would not be found.
GRAMMAR_DIR = "/opt/code-graph/grammars"
_GRAMMAR_ENV = "TREE_SITTER_LANGUAGE_PACK_CACHE_DIR"


def grammar_env() -> dict[str, str]:
    """The grammar cache setting to use, if any (explicit env wins over the image default)."""
    directory = os.environ.get(_GRAMMAR_ENV) or (GRAMMAR_DIR if Path(GRAMMAR_DIR).is_dir() else "")
    return {_GRAMMAR_ENV: directory} if directory else {}


def configure_grammars() -> None:
    """Point this process at the grammar cache; call before tree-sitter is first used."""
    os.environ.update(grammar_env())


def ensure_grammars(needed: set[str]) -> None:
    """Make sure every needed grammar loads, or fail with the list of missing ones.

    `get_language` is the check: the pack downloads one bundle per platform and
    unpacks each grammar from it on first use (offline once the bundle is cached),
    so a grammar not yet unpacked is not missing — `downloaded_languages()` would
    say it is.
    """
    if not needed:
        return
    configure_grammars()
    import tree_sitter_language_pack as tslp  # noqa: PLC0415

    missing = []
    for lang in sorted(needed):
        try:
            tslp.get_language(lang)
        except Exception:  # noqa: BLE001 - offline and not cached; reported below
            missing.append(lang)
    if missing:
        raise ConnectionSourceError(
            f"code-graph: tree-sitter grammars not available: {', '.join(missing)}",
            server="code-graph",
            remediation=(
                "without them CodeGraphContext silently skips those files. Pre-fetch them "
                "into the image (tree_sitter_language_pack.download([...])) or run once "
                "with internet access; cache: " + tslp.cache_dir()
            ),
        )


def _cgc_command() -> list[str]:
    exe_dir = Path(sys.executable).parent
    for name in ("cgc.exe", "cgc"):
        candidate = exe_dir / name
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which("cgc")
    if found:
        return [found]
    raise ConnectionSourceError("code-graph: the `cgc` executable was not found", server="code-graph")


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ConnectionSourceError(
            f"code-graph: `{' '.join(cmd[1:3])}` failed (exit {proc.returncode}): {output[-2000:]}",
            server="code-graph",
        )
    return output


def extract(root: Path, *, workdir: Path | None = None) -> Bundle:
    """Index `root` into a scratch store and export it. The caller owns `workdir`."""
    ensure_grammars(set(languages_in(root)))
    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="code-graph-"))
    work.mkdir(parents=True, exist_ok=True)
    store = str(work / "kuzu")
    env = {
        **os.environ,
        **grammar_env(),
        "CGC_RUNTIME_DB_TYPE": "kuzudb",
        # CGC_RUNTIME_DB_PATH, not just KUZUDB_PATH: CGC resolves a "context" first and
        # its path (~/.codegraphcontext/global/db) wins over KUZUDB_PATH, which made
        # every run accumulate into one persistent store. Only this override beats it.
        "CGC_RUNTIME_DB_PATH": store,
        "KUZUDB_PATH": store,
        "INDEX_SOURCE": "true",
        "PYTHONIOENCODING": "utf-8",
    }
    # Never let an inherited Neo4j setting point CGC at the project's database.
    for key in ("DEFAULT_DATABASE", "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE"):
        env.pop(key, None)
    cgc = _cgc_command()
    _run([*cgc, "index", str(root), "--no-progress"], cwd=work, env=env)
    archive = work / "out.cgc"
    _run([*cgc, "bundle", "export", str(archive)], cwd=work, env=env)
    out = work / "bundle"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(out)
    meta_path = out / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    # The scratch store must hold exactly the repository just indexed; anything else
    # means CGC wrote to a store other than ours and the export would mix codebases.
    exported = {
        str(r.get("path", "")).replace("\\", "/").rstrip("/").lower()
        for r in metadata.get("repositories", [])
    }
    expected = str(root).replace("\\", "/").rstrip("/").lower()
    if exported and exported != {expected}:
        raise ConnectionSourceError(
            f"code-graph: the scratch store held other repositories: {sorted(exported)}",
            server="code-graph",
        )
    return Bundle(workdir=work, nodes_path=out / "nodes.jsonl", edges_path=out / "edges.jsonl", metadata=metadata)
