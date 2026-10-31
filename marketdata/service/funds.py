"""Curated fund portfolios (E9)."""

from typing import Any

from psycopg import AsyncConnection

from marketdata.db.repositories import managers
from marketdata.db.repositories.managers import Manager
from marketdata.service.companies import name_words
from marketdata.service.errors import InvalidParameter, NotFound


async def list_managers(conn: AsyncConnection[Any], *, curated: bool = True) -> list[Manager]:
    """The 13F managers by display name: the curated ones, or all with `curated=False`."""
    return await managers.list_managers(conn, curated=curated)


async def resolve_manager(conn: AsyncConnection[Any], text: str) -> Manager:
    """A curated manager by its CIK or its name: our display name or EDGAR's, as a whole, by its
    start, or by a word in it ("icahn", "pabrai"). InvalidParameter when several match;
    NotFound names the curated managers."""
    curated = await managers.list_managers(conn)
    value = text.strip()
    if value.isdigit():
        found = [manager for manager in curated if manager.manager_cik == int(value)]
        if found:
            return found[0]
        raise NotFound(f"CIK {int(value)} is not a curated manager. {_curated(curated)}")
    words = name_words(value)
    if not words:
        raise InvalidParameter(f"{text!r} is not a manager's name or CIK. {_curated(curated)}")
    named = [(manager, _names(manager)) for manager in curated]
    for test in (
        lambda name: name == words,
        lambda name: name.startswith(words),
        lambda name: f" {words}" in f" {name}",
    ):
        found = [manager for manager, names in named if any(test(name) for name in names)]
        if len(found) == 1:
            return found[0]
        if found:
            listed = ", ".join(f"{m.display_name} ({m.manager_cik})" for m in found)
            raise InvalidParameter(f"{text!r} matches {listed}: ask for one by name or CIK")
    raise NotFound(f"No curated manager matches {text!r}. {_curated(curated)}")


def _names(manager: Manager) -> tuple[str, str]:
    return name_words(manager.display_name), name_words(manager.name)


def _curated(curated: list[Manager]) -> str:
    return "Curated managers: " + ", ".join(manager.display_name for manager in curated) + "."
