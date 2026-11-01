"""E15-S1: the vendored libraries are the pinned versions (PLAN.md §5, D-034), each with its
license, and our static files hold no third-party address."""

import base64
import hashlib
import re

import pytest

from marketdata.web.assets import CHART_JS, HTMX, STATIC_DIR

# The sha256 the npm registry's packages give (checked against their sha512 when downloaded).
PINNED = {
    "vendor/htmx-2.0.11/htmx.min.js": "1v3HXyBOa976mbab8ebUrGm4o2T3eSn0XBNHa0AA9xc=",
    "vendor/htmx-2.0.11/LICENSE": "09JFb3ZBTyRWEEZg69Za/xwEzXlmuUK9q9Y/PNsxajg=",
    "vendor/chart.js-4.5.1/chart.umd.min.js": "SERKgtTty1vsDxll+qzd4Y2cF9swY9BCq62i9wXJ9Uo=",
    "vendor/chart.js-4.5.1/LICENSE.md": "QahKosq6ZF+WahjZwgVrc+bTqB2AvABGvAARomNNTM4=",
}


@pytest.mark.parametrize(("name", "digest"), PINNED.items())
def test_vendored_files_are_the_pinned_versions(name: str, digest: str) -> None:
    content = (STATIC_DIR / name).read_bytes()

    assert base64.b64encode(hashlib.sha256(content).digest()).decode() == digest


def test_only_the_pinned_files_are_vendored() -> None:
    vendored = {
        path.relative_to(STATIC_DIR).as_posix()
        for path in (STATIC_DIR / "vendor").rglob("*")
        if path.is_file()
    }

    assert vendored == set(PINNED)
    assert {HTMX, CHART_JS} <= vendored


def test_the_licenses_travel_with_the_files() -> None:
    assert "Zero-Clause BSD" in (STATIC_DIR / "vendor/htmx-2.0.11/LICENSE").read_text()
    assert "The MIT License" in (STATIC_DIR / "vendor/chart.js-4.5.1/LICENSE.md").read_text()
    assert "Released under the MIT License" in (STATIC_DIR / CHART_JS).read_text()[:200]


def test_our_static_files_name_no_other_site() -> None:
    for path in STATIC_DIR.glob("*.*"):
        text = path.read_text()
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg)", text), path.name
