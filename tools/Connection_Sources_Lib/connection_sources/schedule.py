"""Hand the sync cycle to the operating system's own scheduler and keep it there.

Polling is only useful if it survives everything: a closed terminal, a logout, a
reboot, the machine sitting untouched over a weekend. That rules out anything that
lives inside a process we start — a background thread, a `--watch` loop in a shell —
because all of those die with whatever launched them, silently, and a poller that
has silently stopped looks exactly like a project where nothing is happening.

So the schedule is registered with the platform's own scheduler and nothing here
stays resident: Task Scheduler on Windows, and on Linux a systemd timer where one
is available, falling back to cron where it is not. All three survive reboots on
their own, and none needs this library to be running to fire.

systemd is preferred over cron when both exist because a timer has `Persistent=true`
— a run missed while the machine was off happens once at boot instead of being lost
— and because its runs land in the journal, where a failure is visible without
anyone having thought to look at a log file first. cron remains fully supported for
hosts that do not run systemd.

Two decisions make the rest of this file simple.

*The scheduler runs a launcher script, not a command line.* Every entry points at a
small generated `.cmd`/`.sh` in the state directory. Embedding a full interpreter
invocation in a `schtasks /TR` argument or a crontab line means quoting it correctly
for two different parsers — and cron additionally treats `%` as a line terminator,
which silently truncates any command containing one. A path to a script has no
quoting hazards, can be read to see exactly what will run, and can be executed by
hand to reproduce a failed run outside the scheduler entirely.

*The interpreter is resolved at install time, not at run time.* The launcher hard-
codes the absolute path of the Python running the install, because a scheduled task
starts with an environment nothing like an interactive shell's — no virtualenv
activated, frequently a bare `PATH` — and a launcher that depends on finding the
right interpreter is a launcher that works when tested by hand and fails at 3am.

Everything that decides *what to write* is a pure function taking the target system
as an argument, and every process call goes through an injected runner. That is what
makes the cron path testable on a machine that has no cron.
"""

from __future__ import annotations

import hashlib
import locale
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence
from xml.sax.saxutils import escape

from .errors import ConnectionSourceError
from .sync.store import atomic_write_json, now_iso, read_json

__all__ = [
    "BACKENDS",
    "ScheduleError",
    "CommandResult",
    "windows_task_xml",
    "cron_line",
    "install",
    "remove",
    "resolve_backend",
    "run_now",
    "scheduler_dir",
    "set_enabled",
    "status",
    "systemd_units",
    "task_name",
]

WINDOWS = "Windows"

# "auto" picks per platform; the rest force a backend, which is what a host with
# systemd installed but deliberately unused needs in order to get cron instead.
BACKENDS = ("auto", "schtasks", "systemd", "cron")

_SLUG = re.compile(r"[^A-Za-z0-9]+")

# Cron's minute field takes `*/n` only for n that divide the hour sensibly; beyond
# that the schedule has to be expressed in hours. Anything that fits neither is
# rejected rather than approximated, because a schedule that silently fires at a
# different rate than the one asked for is worse than one that refuses to install.
_MAX_MINUTE_STEP = 59
_MAX_HOUR_STEP = 23


class ScheduleError(ConnectionSourceError):
    """The schedule could not be registered, inspected or removed."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What a scheduler process reported back."""

    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Runner = Callable[[Sequence[str], str | None], CommandResult]


def _decode(raw: bytes) -> str:
    """Turn scheduler output into text without ever raising.

    Which encoding a scheduler writes is not knowable in advance, and the candidates
    are tried in an order chosen so that a wrong guess cannot pass silently.

    UTF-8 leads because it is the only one of these that can *fail*: a single-byte
    codepage maps all 256 byte values, so it decodes anything into something and
    would quietly mistranslate genuine UTF-8 rather than fall through. It is also
    correct outright wherever a console has been switched to codepage 65001.

    `oem` comes next, and on Windows it is the one that matters. A console program
    writes its output in the machine's *OEM* codepage — 850 on a French install, 437
    on an English one — while `locale.getpreferredencoding` reports the *ANSI*
    codepage, 1252. The two disagree on every accented character, so decoding one as
    the other is not a near miss: `Dernière exécution` arrives as `DerniŠre
    ex‚cution`, which is unreadable exactly when someone is reading it to find out
    why a schedule is not firing. The codec is Windows-only and raises `LookupError`
    anywhere else, which the loop treats as a candidate that did not apply.

    That ordering settles a choice the two cannot share: both map all 256 byte values
    and so neither ever fails, meaning whichever is tried first always wins. OEM wins
    because it is what actually produces these bytes — `schtasks`, `systemctl` and
    `crontab` are console programs, and a console program writes in the console
    codepage. The ANSI entry therefore only ever applies away from Windows, where
    `oem` does not exist and a non-UTF-8 locale is still possible.

    Nothing here is parsed for control flow — that is what makes guessing acceptable
    at all — but it is reported verbatim to a person diagnosing a schedule, so it has
    to stay readable.
    """
    if not raw:
        return ""
    for encoding in ("utf-8", "oem", locale.getpreferredencoding(False)):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 maps every byte to some character, so this cannot fail.
    return raw.decode("latin-1")


def _detail_lines(text: str) -> list[str]:
    """Scheduler output split into lines, for a report a person has to read.

    The words stay verbatim — they are the scheduler's own, in whatever language the
    machine speaks, and reading them for meaning is what this module deliberately
    never does. The split is presentation only: carried as a single string, every
    line break in a `/V` listing survives into JSON as an escape sequence, and two
    dozen fields arrive as one unreadable line. As a list, each field is its own
    entry and the escapes disappear.

    `splitlines` rather than a split on one separator, because the three schedulers
    between them emit CRLF and LF, and neither should reach the reader as a stray
    character at the end of a line.
    """
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def _run(argv: Sequence[str], stdin_text: str | None = None) -> CommandResult:
    """Execute a scheduler command, capturing everything it says."""
    try:
        completed = subprocess.run(
            list(argv),
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ScheduleError(
            f"{argv[0]} not found on this machine",
            remediation="the platform scheduler is required to register a recurring sync",
        ) from exc
    except OSError as exc:
        raise ScheduleError(f"could not run {argv[0]}: {exc}") from exc
    return CommandResult(
        completed.returncode, _decode(completed.stdout), _decode(completed.stderr)
    )


def _system(system: str | None) -> str:
    return system or platform.system()


def _is_windows(system: str | None) -> bool:
    return _system(system) == WINDOWS


def _systemd_available(runner: Runner) -> bool:
    """Whether this host can run user timers at all."""
    try:
        return runner(["systemctl", "--user", "--version"], None).ok
    except ScheduleError:
        # `systemctl` is simply not installed. That is an answer, not a failure.
        return False


def resolve_backend(
    system: str | None = None, backend: str | None = None, runner: Runner = None  # type: ignore[assignment]
) -> str:
    """Which scheduler will be used, given the platform and any explicit choice.

    Exposed rather than kept private because "which mechanism did this actually
    install into" is the first question asked when a schedule is not firing, and
    answering it should not require reading this module.
    """
    if backend and backend != "auto":
        if backend not in BACKENDS:
            raise ScheduleError(
                f"unknown scheduler backend {backend!r}",
                remediation=f"one of: {', '.join(BACKENDS)}",
            )
        return backend
    if _is_windows(system):
        return "schtasks"
    return "systemd" if _systemd_available(runner or _run) else "cron"


# --------------------------------------------------------------------------- names


def task_name(project: str | Path) -> str:
    """A stable, unique name for this project's schedule entry.

    The folder name alone is not enough — two clients whose project folders are both
    called `project` would otherwise install over each other's entry — so the
    absolute path is hashed in. The same project always resolves to the same name,
    which is what makes install and remove idempotent.
    """
    resolved = Path(project).expanduser().resolve()
    slug = _SLUG.sub("-", resolved.name).strip("-").lower() or "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"alm-conn-sync-{slug}-{digest}"


def scheduler_dir(state_dir: str | Path) -> Path:
    """Where the launcher, the log and the record of what was installed live."""
    return Path(state_dir) / "scheduler"


def _record_path(state_dir: str | Path) -> Path:
    return scheduler_dir(state_dir) / "schedule.json"


def _launcher_path(state_dir: str | Path, system: str | None = None) -> Path:
    name = "run-sync.cmd" if _is_windows(system) else "run-sync.sh"
    return scheduler_dir(state_dir) / name


def _log_path(state_dir: str | Path) -> Path:
    return scheduler_dir(state_dir) / "sync.log"


def _vbs_wrapper_path(state_dir: str | Path) -> Path:
    return scheduler_dir(state_dir) / "run-sync-hidden.vbs"


def _render_vbs_wrapper(launcher: Path) -> str:
    """Wrap `launcher` so Task Scheduler never flashes a console window.

    Task Scheduler's own `<Hidden>` XML element only hides the task from the
    Task Scheduler UI list -- it does not suppress the console window a .cmd
    launcher opens. `WshShell.Run`'s window-style 0 does. Synchronous
    (waitOnReturn True), never fire-and-forget: `MultipleInstancesPolicy=
    IgnoreNew` (see `windows_task_xml`'s own docstring) only protects against
    overlap while Task Scheduler still considers the task running, which
    requires this wrapper to block until the launcher actually exits.
    """
    escaped = str(launcher).replace('"', '""')
    return (
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run """{escaped}""", 0, True\r\n'
    )


def _task_xml_path(state_dir: str | Path) -> Path:
    return scheduler_dir(state_dir) / "task.xml"


# ----------------------------------------------------------------------- launchers


def _sync_argv(
    python_exe: str,
    project: str | Path,
    *,
    state_dir: str | Path,
    source: str | None,
    graph: bool = False,
    graph_prune: bool = False,
    graph_state: str | Path | None = None,
) -> list[str]:
    """The interpreter invocation the launcher wraps.

    Run through `-m` rather than the installed `alm-conn` console script: the script
    is a generated shim whose location depends on how the package was installed,
    while the module path is the same everywhere the package can be imported at all.

    `update_state` rather than `cli sync`, though the cycle is identical, because the
    two are answerable to different readers. `sync` prints an indented document and
    lets an error reach a person as a traceback; this runs with nobody watching, so
    it needs one line per run in a log and an exit code that separates "nothing
    changed" from "never ran". Anyone can still run the same module by hand — that
    is exactly what the launcher does.

    The graph rides on the same entry point rather than getting a schedule of its own.
    Two entries would read the tracker twice on every tick, double the rate-limit
    cost, and drift apart the moment one of them was paused — and the interesting
    failure, "state moved but the graph did not", would be invisible in either log.
    One process, one line, one exit code.
    """
    argv = [
        python_exe,
        "-m",
        "connection_sources.update_state",
        "--project",
        str(Path(project).expanduser().resolve()),
        "--state-dir",
        str(Path(state_dir).expanduser().resolve()),
    ]
    if source:
        argv += ["--source", source]
    if graph:
        argv.append("--graph")
        if graph_prune:
            argv.append("--graph-prune")
        if graph_state:
            argv += ["--graph-state", str(Path(graph_state).expanduser().resolve())]
    return argv


def _render_windows_launcher(argv: Sequence[str], log: Path) -> str:
    """Render the .cmd wrapper, one command per line.

    Deliberately not written as a single parenthesised block with one redirect on
    the outside, which would read better: `cmd.exe` expands `%ERRORLEVEL%` when it
    *parses* a block, not when it reaches that line, so the exit code logged inside
    one is the code from before the block began. It reads as a clean success on
    every run, including the runs that failed — and this log is the only account of
    what happened while nobody was watching.

    Each line is parsed on its own here, so the status captured after the sync is
    the sync's own, and the launcher exits with it for the scheduler to record.

    The sync is invoked through `call` for the same reason: a batch file that hands
    off to another batch file without it never gets control back, and would exit
    before writing the line that says what happened.
    """
    for part in list(argv) + [str(log)]:
        if '"' in part:
            raise ScheduleError(
                f"path or argument contains a double quote and cannot be scheduled: {part!r}"
            )
        if "%" in part:
            raise ScheduleError(
                f"path or argument contains a percent sign and cannot be scheduled: {part!r}",
                remediation="`%` starts a variable expansion in a .cmd file; "
                "move the project somewhere without one",
            )
    command = " ".join(f'"{part}"' for part in argv)
    return (
        "@echo off\r\n"
        "REM Generated launcher for the recurring ALM sync. Safe to run by hand:\r\n"
        "REM doing so performs exactly the cycle the scheduler performs.\r\n"
        f'>>"{log}" echo [%DATE% %TIME%] sync start\r\n'
        f'call {command} >>"{log}" 2>&1\r\n'
        'set "STATUS=%ERRORLEVEL%"\r\n'
        f'>>"{log}" echo [%DATE% %TIME%] sync exit %STATUS%\r\n'
        "exit /b %STATUS%\r\n"
    )


def _render_posix_launcher(argv: Sequence[str], log: Path) -> str:
    command = " ".join(shlex.quote(part) for part in argv)
    quoted_log = shlex.quote(str(log))
    return (
        "#!/bin/sh\n"
        "# Generated launcher for the recurring ALM sync. Safe to run by hand:\n"
        "# doing so performs exactly the cycle the scheduler performs.\n"
        "set -u\n"
        f"LOG={quoted_log}\n"
        'printf \'[%s] sync start\\n\' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$LOG"\n'
        f'{command} >>"$LOG" 2>&1\n'
        "STATUS=$?\n"
        'printf \'[%s] sync exit %s\\n\' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$STATUS" >>"$LOG"\n'
        "exit $STATUS\n"
    )


def _write_launcher(
    state_dir: str | Path,
    project: str | Path,
    *,
    system: str | None,
    python_exe: str,
    source: str | None,
    graph: bool = False,
    graph_prune: bool = False,
    graph_state: str | Path | None = None,
) -> Path:
    path = _launcher_path(state_dir, system)
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = _sync_argv(
        python_exe,
        project,
        state_dir=state_dir,
        source=source,
        graph=graph,
        graph_prune=graph_prune,
        graph_state=graph_state,
    )
    log = _log_path(state_dir)

    if _is_windows(system):
        path.write_text(_render_windows_launcher(argv, log), encoding="utf-8", newline="")
    else:
        path.write_text(_render_posix_launcher(argv, log), encoding="utf-8", newline="\n")
        path.chmod(0o755)
    return path


# ------------------------------------------------------------------- windows task


def windows_task_xml(
    launcher: str | Path, interval_minutes: int, *, start: str | None = None
) -> str:
    """The Task Scheduler definition for this schedule, as XML.

    `schtasks /Create /SC MINUTE` is one line and would do the job, except for what
    it cannot say. A task created that way takes Task Scheduler's defaults, and two
    of those defaults stop a poller dead on any machine that is not a desk-bound
    server:

    * `DisallowStartIfOnBatteries` defaults to true. On a laptop the task is created,
      reports itself enabled, shows a next-run time — and simply never starts while
      the machine is unplugged. Nothing anywhere says so; the task sits queued.
    * `StopIfGoingOnBatteries` defaults to true, which kills a cycle mid-write if the
      charger is pulled while it runs.

    Neither is reachable from any `schtasks` command-line flag, so the definition is
    written as XML instead, which is also the only form in which these settings are
    locale-independent. `StartWhenAvailable` is the third one worth having: a run due
    while the machine was asleep happens once on waking, which is the property that
    made systemd preferable to cron on Linux.

    `IgnoreNew` matters for a different reason. Cycles share one state file behind a
    lock, so a slow run overlapping the next one would have the second wait on the
    first and achieve nothing; skipping it is both cheaper and what the interval
    already means.
    """
    if interval_minutes < 1:
        raise ScheduleError("interval must be at least one minute")
    # Local time, and deliberately not UTC: Task Scheduler reads an unsuffixed
    # StartBoundary as local, and a UTC instant written here would delay the first
    # run by the machine's offset.
    boundary = start or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    launcher = Path(launcher)
    if launcher.suffix.lower() == ".vbs":
        # A .vbs file has no PE header -- CreateProcess (what Task Scheduler
        # actually calls) cannot launch it directly. wscript.exe is a fixed
        # system path, present on every Windows install.
        command = escape(r"%windir%\System32\wscript.exe")
        raw_arguments = '//B "' + str(launcher) + '"'
        arguments_line = "      <Arguments>" + escape(raw_arguments) + "</Arguments>\r\n"
    else:
        command = escape(str(launcher))
        arguments_line = ""
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        "  <RegistrationInfo>\r\n"
        "    <Description>ALM change detection cycle</Description>\r\n"
        "  </RegistrationInfo>\r\n"
        "  <Triggers>\r\n"
        "    <TimeTrigger>\r\n"
        "      <Repetition>\r\n"
        f"        <Interval>PT{int(interval_minutes)}M</Interval>\r\n"
        "        <StopAtDurationEnd>false</StopAtDurationEnd>\r\n"
        "      </Repetition>\r\n"
        f"      <StartBoundary>{boundary}</StartBoundary>\r\n"
        "      <Enabled>true</Enabled>\r\n"
        "    </TimeTrigger>\r\n"
        "  </Triggers>\r\n"
        "  <Principals>\r\n"
        '    <Principal id="Author">\r\n'
        "      <LogonType>InteractiveToken</LogonType>\r\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\r\n"
        "    </Principal>\r\n"
        "  </Principals>\r\n"
        "  <Settings>\r\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\r\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\r\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\r\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\r\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\r\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\r\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\r\n"
        "    <Enabled>true</Enabled>\r\n"
        "    <Hidden>false</Hidden>\r\n"
        "    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>\r\n"
        "    <IdleSettings>\r\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\r\n"
        "      <RestartOnIdle>false</RestartOnIdle>\r\n"
        "    </IdleSettings>\r\n"
        "  </Settings>\r\n"
        '  <Actions Context="Author">\r\n'
        "    <Exec>\r\n"
        f"      <Command>{command}</Command>\r\n"
        f"{arguments_line}"
        "    </Exec>\r\n"
        "  </Actions>\r\n"
        "</Task>\r\n"
    )


def _write_task_xml(state_dir: str | Path, launcher: Path, interval_minutes: int) -> Path:
    """Write the definition where `schtasks /XML` can read it.

    UTF-16 rather than UTF-8: `schtasks` rejects the file outright if it is not
    Unicode with a byte-order mark, and says only that the XML is malformed.
    """
    path = _task_xml_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        windows_task_xml(launcher, interval_minutes), encoding="utf-16", newline=""
    )
    return path


# ---------------------------------------------------------------------------- cron


def cron_line(interval_minutes: int, launcher: str | Path, marker: str) -> str:
    """One crontab line for this schedule, tagged so it can be found again.

    The trailing marker comment is how every later operation identifies its own
    line. Matching on the command path instead would break the moment a project
    moved, leaving an orphaned entry firing against a path that no longer exists.
    """
    if interval_minutes < 1:
        raise ScheduleError("interval must be at least one minute")
    if interval_minutes <= _MAX_MINUTE_STEP:
        expression = f"*/{interval_minutes} * * * *"
    elif interval_minutes % 60 == 0 and interval_minutes // 60 <= _MAX_HOUR_STEP:
        expression = f"0 */{interval_minutes // 60} * * *"
    else:
        raise ScheduleError(
            f"cannot express a {interval_minutes}-minute interval in cron",
            remediation=(
                f"use 1-{_MAX_MINUTE_STEP} minutes, or a whole number of hours "
                f"up to {_MAX_HOUR_STEP}"
            ),
        )
    return f"{expression} {shlex.quote(str(launcher))} {marker}"


def _cron_marker(name: str) -> str:
    return f"# {name}"


def _read_crontab(runner: Runner) -> list[str]:
    """The current crontab as lines. No crontab at all reads as empty."""
    result = runner(["crontab", "-l"], None)
    if not result.ok:
        # `crontab -l` exits non-zero both for "no crontab for user" and for real
        # failures, and the message is locale-dependent, so the two cannot be told
        # apart reliably. Treating it as empty is the safe reading: the worst case
        # is installing a fresh entry, never destroying entries we failed to read,
        # because a write only ever happens with our own marker line appended.
        return []
    return result.stdout.splitlines()


def _write_crontab(lines: Sequence[str], runner: Runner) -> None:
    body = "\n".join(lines).strip("\n")
    result = runner(["crontab", "-"], body + "\n" if body else "\n")
    if not result.ok:
        raise ScheduleError(f"crontab could not be written: {result.stderr.strip()}")


def _without_marker(lines: Sequence[str], marker: str) -> list[str]:
    """Every line except this schedule's, commented-out ones included."""
    return [line for line in lines if marker not in line]


def _apply_cron(lines: Sequence[str], marker: str, new_line: str | None) -> list[str]:
    """Replace this schedule's line, or drop it when `new_line` is None.

    A read-modify-write of the whole crontab, keeping every line that is not ours
    exactly as found — the user's other jobs live in the same file, and this is the
    one operation here that could destroy work nobody can recover.
    """
    kept = _without_marker(lines, marker)
    return kept + [new_line] if new_line is not None else kept


def _set_cron_enabled(lines: Sequence[str], marker: str, enabled: bool) -> list[str]:
    """Comment this schedule's line out, or bring it back.

    Pausing by commenting rather than deleting keeps the schedule visible in the
    crontab and makes resuming exact — the interval and the command survive the
    pause instead of having to be reconstructed from memory.
    """
    updated: list[str] = []
    for line in lines:
        if marker not in line:
            updated.append(line)
            continue
        stripped = line.lstrip()
        if enabled:
            updated.append(stripped[1:].lstrip() if stripped.startswith("#") else line)
        else:
            updated.append(line if stripped.startswith("#") else f"# {line}")
    return updated


# ------------------------------------------------------------------------ systemd


def _default_unit_dir() -> Path:
    """Where user units live. User-level, not system-level, on purpose.

    A system unit needs root to install and would run the sync as root against a
    project folder owned by someone else. A user timer installs with no privileges
    at all and runs as the account that owns the credentials it is about to use.
    """
    return Path.home() / ".config" / "systemd" / "user"


def systemd_units(name: str, launcher: str | Path, interval_minutes: int) -> dict[str, str]:
    """The `.service` and `.timer` text for this schedule.

    `Persistent=true` is the reason to prefer this over cron: a run that was due
    while the machine was off happens once shortly after boot, instead of being
    lost the way a missed cron run is. `OnUnitActiveSec` measures from the end of
    the last run, so a slow cycle delays the next one rather than overlapping it —
    two concurrent runs would contend for the same state lock.
    """
    if interval_minutes < 1:
        raise ScheduleError("interval must be at least one minute")
    return {
        f"{name}.service": (
            "[Unit]\n"
            "Description=ALM change detection cycle\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"ExecStart={launcher}\n"
        ),
        f"{name}.timer": (
            "[Unit]\n"
            "Description=Run the ALM change detection cycle periodically\n"
            "\n"
            "[Timer]\n"
            f"OnBootSec={min(interval_minutes, 5)}min\n"
            f"OnUnitActiveSec={interval_minutes}min\n"
            "AccuracySec=30s\n"
            "Persistent=true\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def _systemctl(runner: Runner, *args: str) -> CommandResult:
    return runner(["systemctl", "--user", *args], None)


def _systemd_install(
    name: str, launcher: Path, interval_minutes: int, unit_dir: Path, runner: Runner
) -> None:
    unit_dir.mkdir(parents=True, exist_ok=True)
    for filename, body in systemd_units(name, launcher, interval_minutes).items():
        (unit_dir / filename).write_text(body, encoding="utf-8", newline="\n")

    _systemctl(runner, "daemon-reload")
    result = _systemctl(runner, "enable", "--now", f"{name}.timer")
    if not result.ok:
        raise ScheduleError(
            f"could not enable the timer: {(result.stderr or result.stdout).strip()}",
            remediation=(
                "a user timer only runs while the user has a session unless lingering "
                "is on; enable it with: loginctl enable-linger $USER"
            ),
        )


# ------------------------------------------------------------------------- record


def _write_record(state_dir: str | Path, record: dict[str, Any]) -> None:
    atomic_write_json(_record_path(state_dir), record)


def _read_record(state_dir: str | Path) -> dict[str, Any]:
    record = read_json(_record_path(state_dir), default={})
    return record if isinstance(record, dict) else {}


# ------------------------------------------------------------------------ actions


def _schtasks_install(
    name: str,
    launcher: Path,
    interval_minutes: int,
    state_dir: str | Path,
    runner: Runner,
) -> None:
    """Register the task from the XML definition, or from flags if that is refused.

    The XML is what carries the power settings a laptop needs (see
    `windows_task_xml`), so it is always tried first. It can still be rejected —
    an older Task Scheduler that does not know the 1.2 schema, a policy that blocks
    importing a definition — and in that case a schedule that runs on mains power is
    worth far more than no schedule at all, so the flag form is used instead. Both
    are keyed by the same task name, so whichever succeeds leaves exactly one entry.
    """
    vbs_wrapper = _vbs_wrapper_path(state_dir)
    vbs_wrapper.parent.mkdir(parents=True, exist_ok=True)
    vbs_wrapper.write_text(_render_vbs_wrapper(launcher), encoding="utf-8", newline="")

    xml = _write_task_xml(state_dir, vbs_wrapper, interval_minutes)
    result = runner(["schtasks", "/Create", "/F", "/TN", name, "/XML", str(xml)], None)
    if result.ok:
        return

    fallback = runner(
        [
            "schtasks", "/Create", "/F",
            "/SC", "MINUTE", "/MO", str(interval_minutes),
            "/TN", name,
            "/TR", f'"%windir%\\System32\\wscript.exe" //B "{vbs_wrapper}"',
        ],
        None,
    )
    if not fallback.ok:
        raise ScheduleError(
            "could not register the scheduled task: "
            f"{(fallback.stderr or fallback.stdout).strip()}",
            remediation="registering a task can require an elevated prompt",
        )


def install(
    project: str | Path,
    *,
    state_dir: str | Path,
    interval_minutes: int = 5,
    source: str | None = None,
    system: str | None = None,
    backend: str | None = None,
    python_exe: str | None = None,
    unit_dir: str | Path | None = None,
    graph: bool = False,
    graph_prune: bool = False,
    graph_state: str | Path | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Register the recurring sync, replacing any entry this project already had.

    Idempotent by construction: the entry is keyed by `task_name`, and every backend
    overwrites rather than appends, so installing twice leaves one schedule and not
    two pollers racing each other over the same state file.

    `graph=True` makes each run rebuild the traceability graph after the cycle. It
    needs no extra configuration for a site's Xray: which tier a site has is detected
    per build, so the same installed schedule keeps working when a project moves from
    plain Jira to Xray Server, or from Server to Cloud.
    """
    if interval_minutes < 1:
        raise ScheduleError("interval must be at least one minute")

    state_dir = Path(state_dir).expanduser().resolve()
    name = task_name(project)
    chosen = resolve_backend(system, backend, runner)
    launcher = _write_launcher(
        state_dir,
        project,
        system=system,
        python_exe=python_exe or sys.executable,
        source=source,
        graph=graph,
        graph_prune=graph_prune,
        graph_state=graph_state,
    )
    units = Path(unit_dir) if unit_dir else _default_unit_dir()

    if chosen == "schtasks":
        _schtasks_install(name, launcher, interval_minutes, state_dir, runner)
    elif chosen == "systemd":
        _systemd_install(name, launcher, interval_minutes, units, runner)
    else:
        marker = _cron_marker(name)
        lines = _apply_cron(
            _read_crontab(runner), marker, cron_line(interval_minutes, launcher, marker)
        )
        _write_crontab(lines, runner)

    record = {
        "task_name": name,
        "system": _system(system),
        "backend": chosen,
        "project": str(Path(project).expanduser().resolve()),
        "state_dir": str(state_dir),
        "source": source,
        "interval_minutes": interval_minutes,
        "graph": graph,
        "graph_prune": graph_prune,
        "graph_state": str(graph_state) if graph_state else None,
        "launcher": str(launcher),
        "log": str(_log_path(state_dir)),
        "unit_dir": str(units) if chosen == "systemd" else None,
        "enabled": True,
        "installed_at": now_iso(),
    }
    _write_record(state_dir, record)
    return record


def _installed_backend(state_dir: str | Path, system: str | None, backend: str | None) -> str:
    """The backend to operate on: the explicit one, else whatever install recorded.

    Falling back to the record rather than re-detecting matters on a host where
    detection could now answer differently than it did at install time — systemd
    arriving on a box that was using cron must not make `stop` look in the wrong
    place and report success having changed nothing.
    """
    if backend and backend != "auto":
        return resolve_backend(system, backend)
    recorded = _read_record(state_dir).get("backend")
    if recorded in BACKENDS and recorded != "auto":
        return str(recorded)
    return resolve_backend(system, backend)


def remove(
    project: str | Path,
    *,
    state_dir: str | Path,
    system: str | None = None,
    backend: str | None = None,
    unit_dir: str | Path | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Unregister the schedule. Absent is treated as success, not as an error.

    The launcher and the log are left on disk. They cost nothing, and the log is the
    only record of what the poller did while it was running — deleting the evidence
    as part of turning something off is how an incident becomes unexplainable.
    """
    name = task_name(project)
    chosen = _installed_backend(state_dir, system, backend)
    removed = True

    if chosen == "schtasks":
        result = runner(["schtasks", "/Delete", "/F", "/TN", name], None)
        # A missing task exits non-zero. `remove` is meant to be safe to call
        # without checking first, so only a task that still exists afterwards is a
        # real failure.
        if not result.ok:
            removed = not _windows_task_exists(name, runner)
            if not removed:
                raise ScheduleError(
                    f"could not remove the scheduled task: "
                    f"{(result.stderr or result.stdout).strip()}"
                )
    elif chosen == "systemd":
        _systemctl(runner, "disable", "--now", f"{name}.timer")
        units = Path(unit_dir) if unit_dir else _default_unit_dir()
        for filename in (f"{name}.timer", f"{name}.service"):
            (units / filename).unlink(missing_ok=True)
        _systemctl(runner, "daemon-reload")
    else:
        marker = _cron_marker(name)
        _write_crontab(_apply_cron(_read_crontab(runner), marker, None), runner)

    record = _read_record(state_dir)
    if record:
        record.update({"enabled": False, "installed": False, "removed_at": now_iso()})
        _write_record(state_dir, record)
    return {"task_name": name, "backend": chosen, "removed": removed}


def set_enabled(
    project: str | Path,
    enabled: bool,
    *,
    state_dir: str | Path,
    system: str | None = None,
    backend: str | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Pause or resume the schedule without losing it.

    Distinct from `remove` on purpose: pausing keeps the interval, the launcher and
    the entry itself, so resuming is one command and cannot resume onto a different
    schedule than the one that was paused.
    """
    name = task_name(project)
    chosen = _installed_backend(state_dir, system, backend)
    verb = "enable" if enabled else "disable"

    if chosen == "schtasks":
        result = runner(
            ["schtasks", "/Change", "/TN", name, "/ENABLE" if enabled else "/DISABLE"], None
        )
        if not result.ok:
            raise ScheduleError(
                f"could not {verb} the scheduled task: "
                f"{(result.stderr or result.stdout).strip()}",
                remediation="is it installed? try: alm-conn schedule status --project ...",
            )
    elif chosen == "systemd":
        result = _systemctl(runner, verb, "--now", f"{name}.timer")
        if not result.ok:
            raise ScheduleError(
                f"could not {verb} the timer: {(result.stderr or result.stdout).strip()}",
                remediation="is it installed? try: alm-conn schedule status --project ...",
            )
    else:
        marker = _cron_marker(name)
        lines = _read_crontab(runner)
        if not any(marker in line for line in lines):
            raise ScheduleError(
                "no schedule is installed for this project",
                remediation="install it first: alm-conn schedule install --project ...",
            )
        _write_crontab(_set_cron_enabled(lines, marker, enabled), runner)

    record = _read_record(state_dir)
    if record:
        record["enabled"] = enabled
        record["resumed_at" if enabled else "paused_at"] = now_iso()
        _write_record(state_dir, record)
    return {"task_name": name, "backend": chosen, "enabled": enabled}


def run_now(
    project: str | Path,
    *,
    state_dir: str | Path,
    system: str | None = None,
    backend: str | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """Fire the schedule once, immediately, through the scheduler itself.

    The point is to exercise the real path — the scheduler's environment, the
    launcher, the interpreter it hard-codes — rather than the shell's. Running the
    launcher by hand proves the launcher works; this proves the *schedule* works,
    which is the part that fails silently.
    """
    name = task_name(project)
    chosen = _installed_backend(state_dir, system, backend)
    log = str(_log_path(state_dir))

    if chosen == "schtasks":
        result = runner(["schtasks", "/Run", "/TN", name], None)
        if not result.ok:
            raise ScheduleError(
                f"could not run the scheduled task: {(result.stderr or result.stdout).strip()}"
            )
        return {"task_name": name, "backend": chosen, "started": True, "log": log}

    if chosen == "systemd":
        result = _systemctl(runner, "start", f"{name}.service")
        if not result.ok:
            raise ScheduleError(
                f"could not start the unit: {(result.stderr or result.stdout).strip()}"
            )
        return {"task_name": name, "backend": chosen, "started": True, "log": log}

    launcher = _launcher_path(state_dir, system)
    if not launcher.is_file():
        raise ScheduleError(
            "no launcher has been generated for this project",
            remediation="install the schedule first: alm-conn schedule install --project ...",
        )
    result = runner([str(launcher)], None)
    return {
        "task_name": name,
        "backend": chosen,
        "started": True,
        "returncode": result.returncode,
        "log": log,
    }


def _windows_task_exists(name: str, runner: Runner) -> bool:
    return runner(["schtasks", "/Query", "/TN", name], None).ok


def status(
    project: str | Path,
    *,
    state_dir: str | Path,
    system: str | None = None,
    backend: str | None = None,
    runner: Runner = _run,
) -> dict[str, Any]:
    """What is registered right now, and what we last recorded installing.

    Both are reported rather than one. The operating system is the authority on
    whether an entry *exists*, but reading back whether it is enabled means parsing
    scheduler output whose wording changes with the machine's display language — so
    that answer comes from the record this module writes when it makes the change,
    which is locale-proof. Where the two can disagree, both are visible instead of
    one being quietly preferred.
    """
    name = task_name(project)
    record = _read_record(state_dir)
    chosen = _installed_backend(state_dir, system, backend)
    log = _log_path(state_dir)

    if chosen == "schtasks":
        query = runner(["schtasks", "/Query", "/TN", name, "/FO", "LIST", "/V"], None)
        installed = query.ok
        detail = _detail_lines(query.stdout or query.stderr)
    elif chosen == "systemd":
        query = _systemctl(runner, "list-timers", "--all", f"{name}.timer")
        installed = query.ok and name in (query.stdout or "")
        detail = _detail_lines(query.stdout or query.stderr)
    else:
        marker = _cron_marker(name)
        lines = [line for line in _read_crontab(runner) if marker in line]
        installed = bool(lines)
        detail = _detail_lines("\n".join(lines))

    return {
        "task_name": name,
        "system": _system(system),
        "backend": chosen,
        "installed": installed,
        "enabled": bool(record.get("enabled")) if installed else False,
        "interval_minutes": record.get("interval_minutes"),
        "graph": bool(record.get("graph")),
        "launcher": str(_launcher_path(state_dir, system)),
        "log": str(log),
        "log_exists": log.is_file(),
        "installed_at": record.get("installed_at"),
        "detail": detail,
    }
