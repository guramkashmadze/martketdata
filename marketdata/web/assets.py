"""The web UI's static files (E15-S1): our styles and script, and the two libraries vendored at
pinned versions (PLAN.md §5, D-034), served from /static by this app, so a page makes no
third-party request.

A file's URL names its version: the vendored ones in their folder (`vendor/htmx-2.0.11/`), ours
with a digest of their content (`app.css?v=…`), so a browser may keep any of them for a year
and still gets a changed file at once.
"""

import hashlib
from functools import cache
from os import PathLike, stat_result
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

STATIC_DIR = Path(__file__).parent / "static"
HTMX = "vendor/htmx-2.0.11/htmx.min.js"
CHART_JS = "vendor/chart.js-4.5.1/chart.umd.min.js"
CACHE_FOR_A_YEAR = "public, max-age=31536000, immutable"


def asset_url(name: str) -> str:
    """The URL of a static file: with a digest of its content, unless its folder names its
    version. The digest is taken again when the file changes, so a running dev server never
    sends a page that names an old one."""
    if name.startswith("vendor/"):
        return f"/static/{name}"
    path = STATIC_DIR / name
    return f"/static/{name}?v={_digest(path, path.stat().st_mtime_ns)}"


@cache
def _digest(path: Path, modified: int) -> str:
    # `modified` is part of the cache's key: a changed file is read again.
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


class Assets(StaticFiles):
    def __init__(self) -> None:
        super().__init__(directory=STATIC_DIR)

    def file_response(
        self,
        full_path: PathLike[str] | str,
        stat_result: stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = CACHE_FOR_A_YEAR
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
