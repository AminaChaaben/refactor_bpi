"""Which observed changes are worth raising a signal for.

A cycle sees everything the configured scope returns, and `state.json` keeps every
one of those items so it stays a faithful picture of the tracker. Raising a *signal*
— a history line and a queued action — is a separate decision. A consumer that acts
on test work has no use for being woken by a change to an unrelated backlog item,
and a queue it has to filter itself is a queue that will eventually be filtered
wrongly, in one place and not another.

So the narrowing happens here, once, between the diff and the two artifacts that
record a signal. It narrows by bucket — the classification `taxonomy` has already
made — and by nothing else: an item in a watched bucket raises every kind of change
it can raise, so nothing about a watched item is ever half-reported.

The default is the test-management buckets, because that is the smallest set that is
useful on its own. A project that wants more names them in `sources.json` under
`sync.watch_buckets`; `"*"` turns the filter off and signals everything.

One property matters and is easy to get wrong when reading this: the filter applies
to the *signal*, not to the *baseline*. Snapshots and the state buckets are still
rebuilt from every item on every cycle, so an ignored change is absorbed rather than
deferred. Widening `watch_buckets` therefore starts signalling from that moment on;
it does not replay changes that were ignored while the filter was narrower. That is
the intended trade — the alternative is an ever-growing backlog of changes nobody
asked to hear about, held on the chance that someone later might.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .models import ChangeEvent

__all__ = [
    "DEFAULT_WATCHED_BUCKETS",
    "WATCH_ALL",
    "is_watched",
    "load_watched_buckets",
    "partition_events",
]

# In `sync.watch_buckets`, the wildcard that disables the filter entirely.
WATCH_ALL = "*"

# Signal on test work and nothing else until a project says otherwise.
DEFAULT_WATCHED_BUCKETS: tuple[str, ...] = ("tests", "test_plans")


def load_watched_buckets(sync_config: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The buckets whose changes raise a signal.

    An absent or malformed `watch_buckets` falls back to the default. An explicitly
    empty list is honoured as written — "observe everything, signal nothing" is a
    real configuration (a project bringing the poller up before anything is ready to
    consume from it), and silently substituting the default would mean a config file
    that says one thing while the engine does another.
    """
    if not isinstance(sync_config, Mapping) or "watch_buckets" not in sync_config:
        return DEFAULT_WATCHED_BUCKETS

    configured = sync_config["watch_buckets"]
    if isinstance(configured, str):
        configured = [configured]
    if not isinstance(configured, Sequence):
        return DEFAULT_WATCHED_BUCKETS

    return tuple(
        name.strip().lower()
        for name in configured
        if isinstance(name, str) and name.strip()
    )


def is_watched(event: ChangeEvent, watched: Sequence[str]) -> bool:
    """Whether this change belongs to a bucket the project asked to hear about."""
    if WATCH_ALL in watched:
        return True
    return (event.bucket or "").strip().lower() in watched


def partition_events(
    events: Iterable[ChangeEvent], watched: Sequence[str]
) -> tuple[list[ChangeEvent], list[ChangeEvent]]:
    """Split into the changes that raise a signal and the ones that stay silent.

    Both halves are returned rather than the filtered list alone so a cycle can
    report how much it deliberately said nothing about. A run that observed two
    hundred changes and signalled none is indistinguishable from a broken poller
    unless it can say which of the two happened.
    """
    signalled: list[ChangeEvent] = []
    ignored: list[ChangeEvent] = []
    for event in events:
        (signalled if is_watched(event, watched) else ignored).append(event)
    return signalled, ignored
