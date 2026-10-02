"""The entry point the scheduler runs: one cycle, then refresh `state.json`.

    python -m connection_sources.update_state --project <folder>

This is the same cycle `alm-conn sync` performs — it calls straight into
`sync.sync_once` and adds no logic of its own. What it adds is the contract an
unattended runner needs and an interactive command does not:

*It never raises.* A traceback on stderr is a fine answer to a person at a
terminal, who can read it and try something else. Here there is nobody to read it:
the only trace of the run is a line in a log and a number the scheduler records, so
every failure has to arrive as both. An unexpected exception is caught for the same
reason a network error is — the alternative is a poller whose failures are
indistinguishable from a quiet project.

*It says what happened in one machine-readable line.* The launcher redirects
stdout to `scheduler/sync.log`, so this output is the run's history. It is a single
JSON object rather than the indented document the CLI prints, because a log is read
by tailing it and one run should occupy one line.

*Its exit code distinguishes the three outcomes that matter.* A schedule that
records `0` for everything cannot be diagnosed after the fact:

    0  the cycle completed and every configured source was read
    2  the cycle could not run at all — bad config, unreachable host, refused
       credentials. Nothing was written.
    3  the cycle ran and wrote state, but at least one source failed. The state is
       correct for the sources that answered.
    1  an unexpected error. This is a bug here, not a condition in the project.

None of them stop the schedule. The scheduler fires again on its own interval
regardless of what the last run returned, which is the point: one failed execution
must not block the process. The code exists so that a person reading the log
afterwards can tell a run that did nothing because nothing changed from a run that
did nothing because it never got off the ground.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from .clients.base import DEFAULT_TIMEOUT
from .errors import ConnectionSourceError
from .sync import SyncResult, sync_once
from .sync import default_state_dir as _resolve_state_dir
from .sync import load_sync_config

__all__ = [
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_UNEXPECTED",
    "main",
    "run",
    "summarise_graph",
]

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_FAILED = 2
EXIT_PARTIAL = 3


def default_state_dir(project: str | Path) -> Path:
    """Where a project's state lives when the caller does not say."""
    return _resolve_state_dir(project)


def summarise(result: SyncResult) -> dict:
    """The one line this run leaves behind.

    Deliberately not the full `SyncResult`: that carries every event in full, and a
    reconcile over a busy project would put thousands of lines of payload into a log
    that is meant to be skimmed. What a reader needs from the log is whether the run
    worked and whether anything moved; the events themselves are already in the
    history file, in full, keyed by the same `state_version` printed here.
    """
    return {
        "event": "alm_state_update",
        "project": result.project,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "cycle": result.cycle,
        "full_reconcile": result.full_reconcile,
        "items_seen": result.items_seen,
        "state_version": result.state_version,
        "changes": len(result.events),
        "ignored": result.ignored,
        "queued": result.actions_queued,
        "history_written": result.history_written,
        "exports_refreshed": result.exports_refreshed,
        "unresolved": result.unresolved,
        "errors": result.errors,
        # The kinds alone, deduplicated: enough to see at a glance that a test
        # changed type without unfolding the whole event list.
        "kinds": sorted({event.kind for event in result.events}),
    }


def summarise_graph(result: Any) -> dict:
    """The graph half of the line, at the same grain as the sync half.

    The build result carries every count by label and every relationship by type,
    which is the right level of detail for someone reading one build and the wrong
    level for a log with one run per line. What belongs here is whether it wrote,
    how much, which Xray tier each site turned out to have — the answer changes when
    a plugin is installed or a key expires, and a schedule is where that gets noticed
    — and anything that failed.
    """
    counts = result.counts or {}
    return {
        "version": result.version,
        "sources": result.sources,
        "namespaces": result.namespaces,
        "issues_read": result.issues_read,
        "nodes": counts.get("nodes", 0),
        "relationships": counts.get("edges", 0),
        "xray": {name: tier.get("tier") for name, tier in (result.xray_tier or {}).items()},
        "nodes_merged": (result.load or {}).get("nodes_merged", 0),
        "relationships_merged": (result.load or {}).get("relationships_merged", 0),
        "pruned_nodes": result.pruned_nodes,
        "errors": result.errors,
    }


def _build_graph(
    project: str | Path,
    *,
    source: str | None,
    limit: int,
    timeout: float,
    prune: bool,
    state_path: str | Path | None,
) -> dict:
    """Rebuild the graph, and optionally leave the state document beside it.

    Imported here rather than at module scope because the graph needs the Neo4j
    driver, and a project that only polls for changes should not have to install one
    to run its schedule.

    Failures are returned, never raised: the sync cycle has already written state by
    the time this runs, and losing that record because a database was unreachable
    would be the wrong trade — the exit code still says the run was partial.
    """
    from . import graph as graph_mod

    try:
        result, batch = graph_mod.run_build(
            project, sources=[source] if source else None, limit=limit,
            timeout=timeout, prune=prune,
        )
    except ConnectionSourceError as exc:
        return {"ok": False, **exc.to_dict()}

    line = summarise_graph(result)
    line["ok"] = not result.errors
    if state_path:
        document = graph_mod.state_of(result, batch)
        target = Path(state_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(document, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        line["state_file"] = str(target)
    return line


def _run_period_check(project: str | Path, sync_config: dict[str, Any]) -> dict[str, Any] | None:
    """Re-derive DAY/NIGHT for the Orchestrator's state.json, gated entirely by
    config -- a project with no `period_check_script` declared in its `sync`
    block (the vast majority; this is an Orchestrator-specific concern, not an
    ALM one) skips this silently, so nothing here assumes every project using
    this scheduler entry point has an Orchestrator attached at all.

    Runs after the sync cycle, in the same process, never in place of it or
    racing it: both write to state.json through the same update_state.py, one
    subprocess call after the other here, so there is nothing left for a lock
    to arbitrate between *these two* calls -- only against a concurrent
    Orchestrator/Planner write from a different process entirely, which
    state.json's own optimistic-concurrency check (and whatever lock the
    project adds on top of it) already has to handle regardless of whether
    this function exists.

    Never raises -- same contract as the rest of this module (see its
    docstring): a project that declared the script but whose path turned out
    stale, or whose run failed, reports that in the log line instead of
    losing the ALM cycle's own result.
    """
    script = sync_config.get("period_check_script")
    if not script:
        return None
    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = Path(project) / script_path
    if not script_path.is_file():
        return {
            "ok": False,
            "error": "FileNotFoundError",
            "message": f"period_check_script not found: {script_path}",
        }

    args = [sys.executable, str(script_path)]
    project_root = sync_config.get("period_check_project_root")
    if project_root:
        root_path = Path(project_root)
        if not root_path.is_absolute():
            root_path = Path(project) / root_path
        args += ["--project-root", str(root_path)]

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 - see module docstring: never raise the cycle
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}

    if proc.returncode != 0:
        return {
            "ok": False,
            "returncode": proc.returncode,
            "message": (proc.stderr or proc.stdout).strip(),
        }
    try:
        return {"ok": True, **json.loads(proc.stdout.strip() or "{}")}
    except json.JSONDecodeError:
        return {"ok": True, "output": proc.stdout.strip()}


def run(
    project: str | Path,
    *,
    state_dir: str | Path | None = None,
    source: str | None = None,
    limit: int = 1000,
    timeout: float = DEFAULT_TIMEOUT,
    force_full: bool = False,
    emit_actions: bool = True,
    graph: bool = False,
    graph_prune: bool = False,
    graph_state: str | Path | None = None,
) -> tuple[int, dict]:
    """Perform one cycle. Returns the exit code and the line to print.

    Split from `main` so the outcome can be asserted directly in a test without
    capturing stdout or trapping SystemExit.

    The graph rebuild, when asked for, runs *after* the sync and never in place of
    it. Change detection is the thing the schedule exists for and it works with
    nothing but the tracker; the graph is a second consumer of the same read, and a
    Neo4j that is down must not cost the project its change history.
    """
    where = Path(state_dir) if state_dir else default_state_dir(project)
    try:
        result = sync_once(
            project,
            state_dir=where,
            source=source,
            limit=limit,
            timeout=timeout,
            force_full=force_full,
            emit_actions=emit_actions,
        )
    except ConnectionSourceError as exc:
        return EXIT_FAILED, {
            "event": "alm_state_update",
            "project": str(project),
            "ok": False,
            **exc.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        return EXIT_UNEXPECTED, {
            "event": "alm_state_update",
            "project": str(project),
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }

    line = summarise(result)
    failed = bool(result.errors)

    period_check = _run_period_check(project, load_sync_config(project))
    if period_check is not None:
        line["period_check"] = period_check

    if graph:
        try:
            line["graph"] = _build_graph(
                project,
                source=source,
                limit=limit,
                timeout=timeout,
                prune=graph_prune,
                state_path=graph_state,
            )
        except Exception as exc:  # noqa: BLE001 - see the module docstring
            line["graph"] = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
        failed = failed or not line["graph"].get("ok")

    code = EXIT_PARTIAL if failed else EXIT_OK
    line["ok"] = code == EXIT_OK
    return code, line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_state",
        description="Refresh state.json from the project's ALM sources. "
        "Runs one cycle and exits; the scheduler is what repeats it.",
    )
    parser.add_argument("--project", required=True, help="Client project folder")
    parser.add_argument(
        "--state-dir",
        help="Where state.json/snapshots/history live; default <project>/_bmad_state",
    )
    parser.add_argument(
        "--source", help="Limit to one source; default is every enabled source"
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="Ignore the incremental schedule and read the full configured scope",
    )
    parser.add_argument(
        "--no-actions",
        action="store_true",
        help="Record changes in state and history, but do not queue pending_actions",
    )
    parser.add_argument(
        "--graph",
        action="store_true",
        help="Also rebuild the Neo4j traceability graph after the cycle",
    )
    parser.add_argument(
        "--graph-prune",
        action="store_true",
        help="With --graph, delete graph nodes this build did not confirm",
    )
    parser.add_argument(
        "--graph-state",
        help="With --graph, also write the project state document to this path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    logging.getLogger("connection_sources.update_state").info(
        "update_state launched: project=%s state_dir=%s source=%s force_full=%s",
        args.project, args.state_dir, args.source, args.force_full,
    )
    code, line = run(
        args.project,
        state_dir=args.state_dir,
        source=args.source,
        limit=args.limit,
        timeout=args.timeout,
        force_full=args.force_full,
        emit_actions=not args.no_actions,
        graph=args.graph,
        graph_prune=args.graph_prune,
        graph_state=args.graph_state,
    )
    print(json.dumps(line, ensure_ascii=False, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
