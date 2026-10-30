"""Loading the golden sets (E5b-S4): the TOML files under GOLDEN_DIR (tests/golden/ by
default), read as the golden tests read them (normalize/golden.py), replace golden_values in one
transaction. The accuracy report and /v1/status then compare them with what is served, reading
the table rather than files."""

from dataclasses import dataclass
from pathlib import Path

from marketdata.db.repositories import golden
from marketdata.ingest.fetch import Connect
from marketdata.normalize.golden import GoldenFileError, earnings_values, metric_values


@dataclass(frozen=True)
class GoldenLoad:
    files: int
    values: int


async def load_golden(connect: Connect, directory: Path) -> GoldenLoad:
    """Replace the stored golden values with those of the files under `directory`."""
    earnings_files = sorted((directory / "earnings").glob("*.toml"))
    metric_files = sorted((directory / "metrics").glob("*.toml"))
    if not earnings_files and not metric_files:
        raise GoldenFileError(f"{directory}: no golden files in earnings/ or metrics/")
    values = []
    for path in earnings_files:
        values += earnings_values(path.read_text(encoding="utf-8"), _name(directory, path))
    for path in metric_files:
        values += metric_values(path.read_text(encoding="utf-8"), _name(directory, path))
    async with connect() as conn, conn.transaction():
        stored = await golden.replace_all(conn, values)
    return GoldenLoad(files=len(earnings_files) + len(metric_files), values=stored)


def _name(directory: Path, path: Path) -> str:
    """`golden/earnings/aapl.toml`: the file, from the golden folder's own name."""
    return path.relative_to(directory.parent).as_posix()
