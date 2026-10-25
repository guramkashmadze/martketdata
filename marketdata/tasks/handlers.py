"""The registered task kinds and their handlers (E3-S2 onward).

Handlers are thin wrappers around ingest/: they turn what an ingest step reports into success,
a retry (an exception) or TaskRefused, so ingest/ never depends on the queue.
"""

from marketdata.tasks.registry import Registry


def build_registry() -> Registry:
    """Every kind the worker runs. The watcher routes work only to these (PLAN.md §4)."""
    return Registry()
