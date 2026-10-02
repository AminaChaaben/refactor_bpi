"""`code-graph`: build and query the project's code graph.

    code-graph index  [--repo PATH] [--dry-run] [--out FILE] [--no-metrics] [--no-similarity] [--wait S]
    code-graph status [--repo PATH] [--wait S]
    code-graph query  --list | NAME [--param key=value ...] [--limit N] [--repo PATH]
    code-graph state  [--repo PATH] [--out FILE]

JSON on stdout, typed JSON errors on stderr with the error's exit code — the same
contract as `alm-conn`. Neo4j comes from NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD /
NEO4J_DATABASE, the variables the sandbox receives for the project's graph database.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping

from connection_sources.errors import ConnectionSourceError, SourcesConfigError, TransportError
from connection_sources.graph import loader
from connection_sources.graph.config import DEFAULT_DATABASE, DEFAULT_URI, GraphConfig
from connection_sources.graph.ontology import load_ontology

from . import queries
from .bundle import map_bundle
from .extract import configure_grammars, extract, languages_in
from .metrics import METRICS_VERSION, compute_metrics
from .repo import exclude_from_git, find_repo, repo_info
from .schema import CODE_SCHEMA

try:
    from importlib.metadata import version as _pkg_version

    CGC_VERSION = _pkg_version("codegraphcontext")
except Exception:  # noqa: BLE001
    CGC_VERSION = "unknown"


def _emit(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _fail(exc: ConnectionSourceError) -> int:
    print(json.dumps(exc.to_dict(), default=str), file=sys.stderr)
    return exc.exit_code


def _neo4j_env() -> Mapping[str, str]:
    """NEO4J_* from the process, else from the sandbox's credential broker.

    Agents spawned through `usine spawn` get an isolated environment without
    NEO4J_*, only TALAN_CREDENTIALS_URL/TOKEN, the same way alm-conn gets its
    credentials. The broker's debug print goes to stderr: stdout is our JSON.
    """
    if os.environ.get("NEO4J_PASSWORD"):
        return os.environ
    try:
        from connection_sources.env import broker_configured, load_broker_env
    except ImportError:
        return os.environ
    if not broker_configured():
        return os.environ
    with contextlib.redirect_stdout(sys.stderr):
        brokered = load_broker_env()
    return {**brokered, **{k: v for k, v in os.environ.items() if v}}


def _config() -> GraphConfig:
    env = _neo4j_env()
    return GraphConfig(
        uri=env.get("NEO4J_URI") or DEFAULT_URI,
        user=env.get("NEO4J_USERNAME") or env.get("NEO4J_USER") or "neo4j",
        password=env.get("NEO4J_PASSWORD") or "",
        database=env.get("NEO4J_DATABASE") or DEFAULT_DATABASE,
        batch_size=500,
        changelog_limit=0,
        include=frozenset(),
        sources=(),
        ontology=load_ontology(),
    )


@contextlib.contextmanager
def _session(wait: float) -> Iterator[loader.Runner]:
    """A runner on the project's Neo4j, retrying while it starts (bolt comes up late)."""
    deadline = time.monotonic() + max(wait, 0)
    delay = 2.0
    while True:
        try:
            with loader.open_session(_config()) as session:
                yield loader.session_runner(session)
            return
        except TransportError as exc:
            if not exc.retryable or time.monotonic() + delay > deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 30.0)


@contextlib.contextmanager
def _index_lock(root: Path) -> Iterator[bool]:
    """One index at a time per repository; a second caller gets `busy` instead of racing."""
    base = Path(os.environ.get("CODE_GRAPH_STATE_DIR") or (root.parent / "_bmad_state" / "code-graph"))
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(os.environ.get("TMPDIR") or "/tmp")
    lock = base / "index.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # A lock older than 6h is from a crashed run.
        if time.time() - lock.stat().st_mtime > 6 * 3600:
            lock.unlink(missing_ok=True)
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            yield False
            return
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield True
    finally:
        lock.unlink(missing_ok=True)


def _build(root: Path, *, metrics: bool, similarity: bool) -> tuple[Any, dict[str, Any], Any]:
    info = repo_info(root)
    exclude_from_git(root)
    bundle = extract(root)
    try:
        batch, map_report, _ = map_bundle(bundle.nodes(), bundle.edges(), namespace=info.namespace, repo_root=root)
    finally:
        if not os.environ.get("CODE_GRAPH_KEEP_WORKDIR"):
            shutil.rmtree(bundle.workdir, ignore_errors=True)
    functions = sum(1 for n in batch.nodes.values() if n.labels and n.labels[0] == "Function")
    source_files = languages_in(root)
    if source_files and functions == 0:
        raise ConnectionSourceError(
            f"code-graph: {sum(source_files.values())} source files ({', '.join(sorted(source_files))}) produced no functions",
            server="code-graph",
            remediation="CodeGraphContext parsed nothing; run with CODE_GRAPH_KEEP_WORKDIR=1 and check its output",
        )
    metrics_report = compute_metrics(batch, root, similarity=similarity) if metrics else {}
    repo_uid = info.namespace.uid("repo", ".")
    if repo_uid in batch.nodes:
        batch.nodes[repo_uid].props.update(
            {
                "head_sha": info.head_sha or batch.nodes[repo_uid].props.get("head_sha"),
                "remote": info.remote,
                "indexed_at": datetime.now(UTC).isoformat(),
                "cgc_version": CGC_VERSION,
                "metrics_version": METRICS_VERSION if metrics else 0,
                "functions": functions,
                "functions_unmatched": metrics_report.get("functions_unmatched", 0),
            }
        )
    report = {
        "repo": str(root),
        "prefix": info.namespace.prefix,
        "head_sha": info.head_sha,
        "languages": source_files,
        "mapping": map_report.to_dict(),
        "metrics": metrics_report,
        "counts": batch.counts(),
    }
    return batch, report, info


def cmd_index(args: argparse.Namespace) -> int:
    root = find_repo(args.repo)
    with _index_lock(root) as acquired:
        if not acquired:
            _emit({"status": "busy", "repo": str(root)})
            return 0
        started = time.monotonic()
        batch, report, info = _build(root, metrics=not args.no_metrics, similarity=not args.no_similarity)
        if args.out:
            Path(args.out).write_text(json.dumps(batch.to_dict(), indent=1, default=str), encoding="utf-8")
        if args.dry_run:
            _emit({"status": "dry-run", **report, "seconds": round(time.monotonic() - started, 1)})
            return 0
        version = int(time.time())
        seen_at = datetime.now(UTC).isoformat()
        with _session(args.wait) as run:
            result = loader.load_batch(batch, run, version=version, seen_at=seen_at, schema=CODE_SCHEMA)
            pruned_nodes, pruned_rels = loader.prune(
                run, info.namespace, version=version, relationships=True, schema=CODE_SCHEMA
            )
        result.pruned_nodes, result.pruned_relationships = pruned_nodes, pruned_rels
        _emit({"status": "indexed", **report, "load": result.to_dict(), "seconds": round(time.monotonic() - started, 1)})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    root = find_repo(args.repo)
    info = repo_info(root)
    statement, params = queries.build("status", info.namespace.prefix)
    with _session(args.wait) as run:
        rows = run(statement, params)
    row = rows[0] if rows else {}
    indexed_sha = row.get("head_sha")
    _emit(
        {
            "repo": str(root),
            "prefix": info.namespace.prefix,
            "indexed": bool(row),
            "stale": bool(row) and bool(info.head_sha) and indexed_sha != info.head_sha,
            "head_sha_repo": info.head_sha,
            **{k: row.get(k) for k in ("head_sha", "indexed_at", "metrics_version", "functions", "calls", "cgc_version")},
        }
    )
    return 0


def _parse_value(text: str) -> Any:
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


def cmd_query(args: argparse.Namespace) -> int:
    if args.list or not args.name:
        _emit(queries.catalogue())
        return 0
    params: dict[str, Any] = {}
    for item in args.param or []:
        if "=" not in item:
            return _fail(SourcesConfigError(f"--param expects key=value, got {item!r}"))
        key, value = item.split("=", 1)
        params[key.strip()] = _parse_value(value)
    if args.limit is not None:
        params["limit"] = args.limit
    root = find_repo(args.repo)
    prefix = repo_info(root).namespace.prefix
    try:
        statement, query_params = queries.build(args.name, prefix, **params)
    except KeyError as exc:
        return _fail(SourcesConfigError(str(exc).strip("'\"")))
    with _session(args.wait) as run:
        rows = run(statement, query_params)
    _emit({"query": args.name, "prefix": prefix, "rows": rows, "count": len(rows)})
    return 0


def cmd_state(args: argparse.Namespace) -> int:
    root = find_repo(args.repo)
    batch, report, _ = _build(root, metrics=True, similarity=True)
    if args.out:
        Path(args.out).write_text(json.dumps(batch.to_dict(), indent=1, default=str), encoding="utf-8")
    _emit(report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="code-graph", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="Index the repository and load it into the project's graph.")
    p.add_argument("--repo")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--out")
    p.add_argument("--no-metrics", action="store_true")
    p.add_argument("--no-similarity", action="store_true")
    p.add_argument("--wait", type=float, default=0, help="seconds to wait for Neo4j to accept connections")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("status", help="Is the repository indexed, and is the index behind HEAD?")
    p.add_argument("--repo")
    p.add_argument("--wait", type=float, default=0)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("query", help="Run a named query (--list shows them all).")
    p.add_argument("name", nargs="?")
    p.add_argument("--list", action="store_true")
    p.add_argument("--param", action="append", metavar="KEY=VALUE")
    p.add_argument("--limit", type=int)
    p.add_argument("--repo")
    p.add_argument("--wait", type=float, default=0)
    p.set_defaults(func=cmd_query)

    p = sub.add_parser("state", help="Extract and compute metrics without touching Neo4j.")
    p.add_argument("--repo")
    p.add_argument("--out")
    p.set_defaults(func=cmd_state)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Neo4j logs a notification for every label a query names that this repository
    # has no node of (a Java repo has no :Struct); noise on the agent's stderr.
    logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
    configure_grammars()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConnectionSourceError as exc:
        return _fail(exc)


if __name__ == "__main__":
    raise SystemExit(main())
